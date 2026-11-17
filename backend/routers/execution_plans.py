"""Versioned execution-plan APIs and legacy replay import."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id, require_device
from core.execution_plan import SUPPORTED_ACTIONS, SYSTEM_ACTIONS, legacy_replay_to_plan
from db.database import AsyncSessionLocal
from db.models import ExecutionPlan, TestCase, TestSuite, TestRun, TestResult

router = APIRouter(prefix="/api/execution-plans", tags=["execution-plans"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class ExecutionPlanRunRequest(BaseModel):
    device_id: str
    provider: str = ""
    model: str = ""
    max_steps: int = 8


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
        executor = step.get("executor")
        action = str(step.get("action") or "").strip().lower()
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if executor not in {"deterministic", "ai", "hybrid", "system"}:
            raise HTTPException(status_code=422, detail=f"steps[{index}].executor is invalid")
        if executor == "system" and action not in SYSTEM_ACTIONS:
            raise HTTPException(status_code=422, detail=f"steps[{index}] system executor only supports {sorted(SYSTEM_ACTIONS)}")
        if action == "start_app" and not str(args.get("package_name") or args.get("package") or "").strip():
            raise HTTPException(status_code=422, detail=f"steps[{index}] start_app requires args.package_name")

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



@router.post("/{plan_id}/run", status_code=201)
async def run_execution_plan(
    plan_id: str,
    body: ExecutionPlanRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run the selected immutable plan version through the shared device scheduler."""
    account_id = require_account_id(request)
    await require_device(request, db, body.device_id)
    plan = (await db.execute(select(ExecutionPlan).where(
        ExecutionPlan.id == plan_id,
        ExecutionPlan.account_id == account_id,
    ))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    case = (await db.execute(select(TestCase).where(
        TestCase.id == plan.test_case_id,
        TestCase.suite_id == plan.suite_id,
    ))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Execution plan case not found")
    steps = json.loads(plan.steps_json or "[]")
    if not steps:
        raise HTTPException(status_code=422, detail="执行计划没有步骤")
    for index, step in enumerate(steps):
        executor = str(step.get("executor") or "").lower()
        action = str(step.get("action") or "").lower()
        if executor not in {"deterministic", "ai", "hybrid", "system"}:
            raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 执行方式无效")
        if action not in SUPPORTED_ACTIONS and action != "wait":
            raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 动作类型不受支持：{action}")
        if executor in {"ai", "hybrid"}:
            intent = step.get("intent") if isinstance(step.get("intent"), dict) else {}
            if intent.get("status") != "confirmed" or not str(intent.get("description") or "").strip() or not str(intent.get("expected_result") or "").strip():
                raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 的操作意图尚未确认")
        if executor == "system" and action not in SYSTEM_ACTIONS:
            raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 不支持系统动作：{action}")
    from agent.ws_device import WebSocketDevice
    device = WebSocketDevice(body.device_id)
    if not device.is_connected():
        raise HTTPException(status_code=400, detail=f"Device {body.device_id} is not connected")
    from core.settings_store import resolve_provider_model, load_settings
    settings = await load_settings(account_id)
    default_provider, default_model = resolve_provider_model(settings)
    run = TestRun(
        suite_id=plan.suite_id,
        device_id=body.device_id,
        provider=body.provider or default_provider,
        model=body.model or default_model,
        status="pending",
        account_id=account_id,
    )
    db.add(run)
    await db.flush()
    db.add(TestResult(run_id=run.id, case_id=case.id, status="pending", account_id=account_id))
    await db.commit()
    await db.refresh(run)
    from core.test_runner import start_execution_plan_run
    try:
        await start_execution_plan_run(run.id, plan.id, max_steps=max(1, min(body.max_steps, 20)))
    except Exception as exc:
        await db.execute(update(TestRun).where(TestRun.id == run.id).values(
            status="error", finished_at=__import__("datetime").datetime.utcnow()
        ))
        await db.execute(update(TestResult).where(
            TestResult.run_id == run.id, TestResult.status == "pending"
        ).values(status="error", reason=str(exc)))
        await db.commit()
        raise HTTPException(status_code=500, detail=f"无法启动执行计划：{exc}")
    from routers.testruns import _run_out
    return await _run_out(run, db)


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
