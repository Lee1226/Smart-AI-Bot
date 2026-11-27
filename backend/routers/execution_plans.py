"""Versioned execution-plan APIs and legacy replay import."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id, require_device
from core.execution_plan import SUPPORTED_ACTIONS, SYSTEM_ACTIONS
from db.database import AsyncSessionLocal
from db.models import ExecutionPlan, TestCase, TestSuite, TestRun, TestResult

router = APIRouter(prefix="/api/execution-plans", tags=["execution-plans"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class ExecutionPlanRunRequest(BaseModel):
    device_id: str
    model_id: str = Field(min_length=1, max_length=128)
    max_steps: int = 8


class SuitePlanCreateRequest(BaseModel):
    platform: str = ""


class PlanVersionRequest(BaseModel):
    steps: List[Dict[str, Any]]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    platform: Optional[str] = None


class ExecutionPlanOut(BaseModel):
    id: str
    test_case_id: Optional[str] = None
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


def _validate_lifecycle_actions(metadata: Dict[str, Any]) -> None:
    for phase in ("pre_actions", "post_actions"):
        actions = metadata.get(phase, [])
        if not isinstance(actions, list):
            raise HTTPException(status_code=422, detail=f"metadata.{phase} must be a list")
        for index, item in enumerate(actions):
            if not isinstance(item, dict):
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] must be an object")
            if item.get("enabled", True) is False:
                continue
            action = str(item.get("action") or "").strip().lower()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            try:
                wait_after_ms = float(item.get("wait_after_ms", 0) or 0)
            except (TypeError, ValueError):
                wait_after_ms = -1
            if not 0 <= wait_after_ms <= 60000:
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}].wait_after_ms must be 0..60000")
            if action not in {"stop_app", "start_app", "global_action", "press_key", "wait", "tap", "input_text", "swipe", "scroll"}:
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] unsupported action: {action}")
            if action in {"stop_app", "start_app"} and not str(args.get("package_name") or args.get("package") or "").strip():
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] requires args.package_name")
            if action == "global_action" and str(args.get("action") or "").lower() not in {"back", "home", "recent", "notifications"}:
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] invalid global action")
            if action == "press_key" and str(args.get("key") or "").strip().lower() not in {"back", "home", "recent", "power", "enter", "del", "tab", "space"}:
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] has an unsupported key")
            if action == "wait":
                try:
                    duration = float(args.get("duration_ms", 1000))
                except (TypeError, ValueError):
                    duration = -1
                if duration < 0 or duration > 60000:
                    raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] duration_ms must be 0..60000")
            if action == "tap" and not all(isinstance(args.get(key), (int, float)) for key in ("x", "y")):
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] tap requires numeric device-pixel x/y")
            if action == "input_text" and not str(args.get("text") or ""):
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] input_text requires args.text")
            if action == "swipe" and not all(isinstance(args.get(key), (int, float)) for key in ("x1", "y1", "x2", "y2")):
                raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] swipe requires numeric x1/y1/x2/y2")
            if action == "scroll":
                if str(args.get("direction") or "down") not in {"up", "down", "left", "right"}:
                    raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] invalid scroll direction")
                if str(args.get("distance") or "medium") not in {"small", "medium", "large"}:
                    raise HTTPException(status_code=422, detail=f"metadata.{phase}[{index}] invalid scroll distance")


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


@router.get("/suites/{suite_id}", response_model=List[ExecutionPlanOut])
async def list_suite_plans(suite_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    suite = (await db.execute(select(TestSuite).where(
        TestSuite.id == suite_id, TestSuite.account_id == account_id
    ))).scalar_one_or_none()
    if not suite:
        raise HTTPException(status_code=404, detail="Test suite not found")
    # A suite has one current execution flow. Return its newest saved revision;
    # older plans remain available through their existing plan IDs/run records.
    plan = (await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.suite_id == suite_id,
            ExecutionPlan.account_id == account_id,
        ).order_by(ExecutionPlan.updated_at.desc(), ExecutionPlan.version.desc()).limit(1)
    )).scalar_one_or_none()
    return [_out(plan)] if plan else []


@router.post("/suites/{suite_id}", response_model=ExecutionPlanOut, status_code=201)
async def create_suite_plan(
    suite_id: str,
    body: SuitePlanCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    account_id = require_account_id(request)
    suite = (await db.execute(select(TestSuite).where(
        TestSuite.id == suite_id, TestSuite.account_id == account_id
    ))).scalar_one_or_none()
    if not suite:
        raise HTTPException(status_code=404, detail="Test suite not found")
    existing = (await db.execute(select(ExecutionPlan.id).where(
        ExecutionPlan.suite_id == suite_id,
        ExecutionPlan.account_id == account_id,
    ).limit(1))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="该套件已存在执行流程")
    plan = ExecutionPlan(
        test_case_id=None,
        suite_id=suite_id,
        version=1,
        status="draft",
        source="manual",
        platform=body.platform.strip(),
        app_package=suite.app_package or "",
        metadata_json="{}",
        steps_json="[]",
        warnings_json="[]",
        account_id=account_id,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _out(plan)


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


@router.put("/{plan_id}", response_model=ExecutionPlanOut)
async def update_execution_plan(
    plan_id: str,
    body: PlanVersionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update the current execution plan in place without creating a new version."""
    account_id = require_account_id(request)
    plan = (await db.execute(
        select(ExecutionPlan).where(
            ExecutionPlan.id == plan_id,
            ExecutionPlan.account_id == account_id,
        )
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Execution plan not found")
    if not body.steps:
        raise HTTPException(status_code=422, detail="steps must not be empty")
    _validate_lifecycle_actions(body.metadata)

    for index, step in enumerate(body.steps):
        if not isinstance(step.get("sequence"), int) or step["sequence"] < 1:
            raise HTTPException(status_code=422, detail=f"steps[{index}].sequence must be a positive integer")
        executor = step.get("executor")
        action = str(step.get("action") or "").strip().lower()
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if executor not in {"deterministic", "ai", "hybrid", "system"}:
            raise HTTPException(status_code=422, detail=f"steps[{index}].executor is invalid")
        if executor == "system" and action not in SYSTEM_ACTIONS and action != "audio_play":
            raise HTTPException(status_code=422, detail=f"steps[{index}] system executor only supports {sorted(SYSTEM_ACTIONS | {'audio_play'})}")
        if action == "audio_play":
            turns = args.get("turns")
            if not isinstance(turns, list) or not turns or len(turns) > 20:
                raise HTTPException(status_code=422, detail=f"steps[{index}] audio_play requires 1..20 turns")
            for turn_index, turn in enumerate(turns):
                if not isinstance(turn, dict) or not str(turn.get("corpus_id") or "").strip():
                    raise HTTPException(status_code=422, detail=f"steps[{index}].turns[{turn_index}] requires corpus_id")
                try:
                    wait_ms = int(turn.get("wait_after_ms", 12000))
                except (TypeError, ValueError):
                    wait_ms = -1
                if wait_ms < 0 or wait_ms > 120000:
                    raise HTTPException(status_code=422, detail=f"steps[{index}].turns[{turn_index}].wait_after_ms must be 0..120000")
            try:
                volume = int(args.get("volume_percent", 80))
            except (TypeError, ValueError):
                volume = -1
            if not 0 <= volume <= 100:
                raise HTTPException(status_code=422, detail=f"steps[{index}].volume_percent must be 0..100")
        if action == "start_app" and not str(args.get("package_name") or args.get("package") or "").strip():
            raise HTTPException(status_code=422, detail=f"steps[{index}] start_app requires args.package_name")

    plan.steps_json = json.dumps(body.steps, ensure_ascii=False)
    plan.metadata_json = json.dumps(body.metadata, ensure_ascii=False)
    plan.warnings_json = json.dumps(body.warnings, ensure_ascii=False)
    case = await db.get(TestCase, plan.test_case_id) if plan.test_case_id else None
    if case:
        case.lifecycle_actions_json = json.dumps({
            "pre_actions": body.metadata.get("pre_actions", []),
            "post_actions": body.metadata.get("post_actions", []),
        }, ensure_ascii=False)
    if body.platform is not None:
        plan.platform = body.platform
    plan.status = "draft"
    plan.validated_run_id = ""
    plan.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(plan)
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
    _validate_lifecycle_actions(body.metadata)

    for index, step in enumerate(body.steps):
        if not isinstance(step.get("sequence"), int) or step["sequence"] < 1:
            raise HTTPException(status_code=422, detail=f"steps[{index}].sequence must be a positive integer")
        executor = step.get("executor")
        action = str(step.get("action") or "").strip().lower()
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if executor not in {"deterministic", "ai", "hybrid", "system"}:
            raise HTTPException(status_code=422, detail=f"steps[{index}].executor is invalid")
        if executor == "system" and action not in SYSTEM_ACTIONS and action != "audio_play":
            raise HTTPException(status_code=422, detail=f"steps[{index}] system executor only supports {sorted(SYSTEM_ACTIONS | {'audio_play'})}")
        if action == "audio_play":
            turns = args.get("turns")
            if not isinstance(turns, list) or not turns or len(turns) > 20:
                raise HTTPException(status_code=422, detail=f"steps[{index}] audio_play requires 1..20 turns")
            for turn_index, turn in enumerate(turns):
                if not isinstance(turn, dict) or not str(turn.get("corpus_id") or "").strip():
                    raise HTTPException(status_code=422, detail=f"steps[{index}].turns[{turn_index}] requires corpus_id")
                try:
                    wait_ms = int(turn.get("wait_after_ms", 12000))
                except (TypeError, ValueError):
                    wait_ms = -1
                if wait_ms < 0 or wait_ms > 120000:
                    raise HTTPException(status_code=422, detail=f"steps[{index}].turns[{turn_index}].wait_after_ms must be 0..120000")
            try:
                volume = int(args.get("volume_percent", 80))
            except (TypeError, ValueError):
                volume = -1
            if not 0 <= volume <= 100:
                raise HTTPException(status_code=422, detail=f"steps[{index}].volume_percent must be 0..100")
        if action == "start_app" and not str(args.get("package_name") or args.get("package") or "").strip():
            raise HTTPException(status_code=422, detail=f"steps[{index}] start_app requires args.package_name")

    latest_version = await db.scalar(
        select(func.max(ExecutionPlan.version)).where(
            ExecutionPlan.suite_id == source.suite_id,
            ExecutionPlan.account_id == account_id,
        )
    )
    source_case = await db.get(TestCase, source.test_case_id) if source.test_case_id else None
    if source_case:
        source_case.lifecycle_actions_json = json.dumps({
            "pre_actions": body.metadata.get("pre_actions", []),
            "post_actions": body.metadata.get("post_actions", []),
        }, ensure_ascii=False)

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
    case = None
    if plan.test_case_id:
        case = (await db.execute(select(TestCase).where(
            TestCase.id == plan.test_case_id,
            TestCase.suite_id == plan.suite_id,
        ))).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="Execution plan source case not found")
    metadata = json.loads(plan.metadata_json or "{}")
    _validate_lifecycle_actions(metadata)
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
        if executor == "system" and action not in SYSTEM_ACTIONS and action != "audio_play":
            raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 不支持系统动作：{action}")
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        if action == "start_app" and not str(args.get("package_name") or args.get("package") or "").strip():
            raise HTTPException(status_code=422, detail=f"步骤 {index + 1} 缺少应用 Package / Bundle 标识")
    from agent.ws_device import WebSocketDevice
    device = WebSocketDevice(body.device_id)
    if not device.is_connected():
        raise HTTPException(status_code=400, detail=f"Device {body.device_id} is not connected")
    from core.model_resolver import validate_model_selection
    selected_model, selected_connection = await validate_model_selection(db, account_id, body.model_id)
    plan_snapshot = {
        "id": plan.id,
        "suite_id": plan.suite_id,
        "test_case_id": plan.test_case_id,
        "version": plan.version,
        "platform": plan.platform or "",
        "app_package": plan.app_package or "",
        "metadata": json.loads(plan.metadata_json or "{}"),
        "steps": steps,
    }
    run = TestRun(
        suite_id=plan.suite_id,
        device_id=body.device_id,
        provider=selected_connection.provider,
        model=selected_model.model_name,
        model_id=selected_model.id,
        status="pending",
        account_id=account_id,
        execution_plan_id=plan.id,
        execution_plan_version=plan.version,
        execution_plan_snapshot_json=json.dumps(plan_snapshot, ensure_ascii=False),
    )
    db.add(run)
    await db.flush()
    db.add(TestResult(run_id=run.id, target_name="执行计划", target_path="执行计划", status="pending", account_id=account_id))
    plan.status = "draft"
    plan.validated_run_id = ""
    plan.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(run)
    from core.test_runner import start_execution_plan_run
    try:
        await start_execution_plan_run(run.id, plan.id, max_steps=max(1, min(body.max_steps, 20)))
    except Exception as exc:
        await db.execute(update(TestRun).where(TestRun.id == run.id).values(
            status="error", finished_at=datetime.utcnow()
        ))
        await db.execute(update(TestResult).where(
            TestResult.run_id == run.id, TestResult.status == "pending"
        ).values(status="error", reason=str(exc)))
        await db.commit()
        raise HTTPException(status_code=500, detail=f"无法启动执行计划：{exc}")
    from routers.testruns import _run_out
    return await _run_out(run, db)


