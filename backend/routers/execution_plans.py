"""Versioned execution-plan APIs and legacy replay import."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id
from core.execution_plan import legacy_replay_to_plan
from db.database import AsyncSessionLocal
from db.models import ExecutionPlan, TestCase, TestSuite

router = APIRouter(prefix="/api/execution-plans", tags=["execution-plans"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class LegacyImportRequest(BaseModel):
    platform: str = ""


class PlanVersionRequest(BaseModel):
    steps: List[Dict[str, Any]]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    platform: Optional[str] = None


class ExecutionPlanOut(BaseModel):
    id: str
    test_case_id: str
    suite_id: str
    version: int
    status: str
    source: str
    platform: str
    app_package: str
    metadata: Dict[str, Any]
    steps: List[Dict[str, Any]]
    warnings: List[str]
    previous_plan_id: Optional[str] = None
    validated_run_id: str = ""
    created_at: str
    updated_at: str


def _out(plan: ExecutionPlan) -> ExecutionPlanOut:
    return ExecutionPlanOut(
        id=plan.id,
        test_case_id=plan.test_case_id,
        suite_id=plan.suite_id,
        version=plan.version,
        status=plan.status,
        source=plan.source,
        platform=plan.platform or "",
        app_package=plan.app_package or "",
        metadata=json.loads(plan.metadata_json or "{}"),
        steps=json.loads(plan.steps_json or "[]"),
        warnings=json.loads(plan.warnings_json or "[]"),
        previous_plan_id=plan.previous_plan_id,
        validated_run_id=plan.validated_run_id or "",
        created_at=plan.created_at.isoformat(),
        updated_at=plan.updated_at.isoformat(),
    )


async def _owned_case(db: AsyncSession, request: Request, case_id: str):
    account_id = require_account_id(request)
    row = (await db.execute(
        select(TestCase, TestSuite)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(TestCase.id == case_id, TestSuite.account_id == account_id)
    )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Test case not found")
    return row[0], row[1]


@router.get("/cases/{case_id}", response_model=List[ExecutionPlanOut])
async def list_case_plans(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _owned_case(db, request, case_id)
    plans = (await db.execute(
        select(ExecutionPlan)
        .where(ExecutionPlan.test_case_id == case_id,
               ExecutionPlan.account_id == request.state.account_id)
        .order_by(ExecutionPlan.version.desc())
    )).scalars().all()
    return [_out(plan) for plan in plans]


@router.get("/{plan_id}", response_model=ExecutionPlanOut)
async def get_execution_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    plan = (await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.id == plan_id,
            ExecutionPlan.account_id == require_account_id(request),
        )
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    return _out(plan)


@router.post("/{plan_id}/versions", response_model=ExecutionPlanOut, status_code=201)
async def create_plan_version(
    plan_id: str,
    body: PlanVersionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new immutable draft version; never mutate an existing plan."""
    account_id = require_account_id(request)
    source = (await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.id == plan_id,
            ExecutionPlan.account_id == account_id,
        )
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    if not body.steps:
        raise HTTPException(status_code=422, detail="steps must not be empty")

    for index, step in enumerate(body.steps):
        if not isinstance(step.get("sequence"), int) or step["sequence"] < 1:
            raise HTTPException(status_code=422, detail=f"steps[{index}].sequence must be a positive integer")
        if step.get("executor") not in {"deterministic", "ai", "hybrid", "system"}:
            raise HTTPException(status_code=422, detail=f"steps[{index}].executor is invalid")

    latest_version = await db.scalar(
        select(func.max(ExecutionPlan.version)).where(
            ExecutionPlan.test_case_id == source.test_case_id,
            ExecutionPlan.account_id == account_id,
        )
    )
    plan = ExecutionPlan(
        test_case_id=source.test_case_id,
        suite_id=source.suite_id,
        version=(latest_version or source.version) + 1,
        status="draft",
        source=source.source,
        platform=(body.platform if body.platform is not None else source.platform) or "",
        app_package=source.app_package or "",
        metadata_json=json.dumps(body.metadata, ensure_ascii=False),
        steps_json=json.dumps(body.steps, ensure_ascii=False),
        warnings_json=json.dumps(body.warnings, ensure_ascii=False),
        previous_plan_id=source.id,
        account_id=account_id,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _out(plan)


@router.post("/cases/{case_id}/import-legacy", response_model=ExecutionPlanOut, status_code=201)
async def import_legacy_replay(
    case_id: str,
    body: LegacyImportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft plan from replay_steps without modifying the source case."""
    case, suite = await _owned_case(db, request, case_id)
    try:
        steps, warnings = legacy_replay_to_plan(case.replay_steps or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    account_id = require_account_id(request)
    latest_version = await db.scalar(
        select(func.max(ExecutionPlan.version)).where(
            ExecutionPlan.test_case_id == case.id,
            ExecutionPlan.account_id == account_id,
        )
    )
    latest = (await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.test_case_id == case.id,
            ExecutionPlan.account_id == account_id,
        ).order_by(ExecutionPlan.version.desc()).limit(1)
    )).scalar_one_or_none()

    plan = ExecutionPlan(
        test_case_id=case.id,
        suite_id=suite.id,
        version=(latest_version or 0) + 1,
        status="draft",
        source="legacy_import",
        platform=body.platform.strip(),
        app_package=suite.app_package or "",
        metadata_json=json.dumps({
            "expected": case.expected or "",
            "source_format": suite.source_format or "",
            "legacy_path": case.path or "",
        }, ensure_ascii=False),
        steps_json=json.dumps(steps, ensure_ascii=False),
        warnings_json=json.dumps(warnings, ensure_ascii=False),
        previous_plan_id=latest.id if latest else None,
        account_id=account_id,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _out(plan)
