"""Account-scoped APIs for AI exploration sessions, traces and reusable knowledge.

This first slice persists exploration intent and evidence. Device-driving orchestration
is deliberately not exposed until the interaction loop and safety gates are implemented.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id
from db.database import AsyncSessionLocal
from db.exploration_models import ExploreKnowledge, ExploreSession, ExploreStep
from core.exploration_timeline import build_scene_timeline
from db.models import Device, TestSuite, TestCase, ExecutionPlan

router = APIRouter(prefix="/api/exploration", tags=["exploration"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _json(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _session_out(row: ExploreSession):
    return {
        "id": row.id, "title": row.title, "goal": row.goal,
        "expected_result": row.expected_result, "device_id": row.device_id,
        "platform": row.platform, "app_package": row.app_package,
        "status": row.status, "max_steps": row.max_steps,
        "timeout_seconds": row.timeout_seconds, "summary": row.summary,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "updated_at": row.updated_at.isoformat(),
    }


def _step_out(row: ExploreStep):
    return {
        "id": row.id, "sequence": row.sequence, "event_type": row.event_type,
        "title": row.title, "detail": row.detail, "action": row.action,
        "executor": row.executor, "action_args": _json(row.action_args_json, {}),
        "page_before": _json(row.page_before_json, {}),
        "page_after": _json(row.page_after_json, {}),
        "evidence": _json(row.evidence_json, {}), "status": row.status,
        "duration_ms": row.duration_ms, "created_at": row.created_at.isoformat(),
    }


def _knowledge_out(row: ExploreKnowledge):
    return {
        "id": row.id, "source_session_id": row.source_session_id,
        "title": row.title, "category": row.category,
        "description": row.description,
        "trigger_conditions": _json(row.trigger_conditions_json, {}),
        "recognition_rules": _json(row.recognition_rules_json, {}),
        "handling_rules": _json(row.handling_rules_json, {}),
        "evidence": _json(row.evidence_json, {}),
        "app_package": row.app_package, "platform": row.platform,
        "risk_level": row.risk_level, "status": row.status,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class SessionCreate(BaseModel):
    title: str = ""
    goal: str = Field(min_length=1, max_length=10000)
    expected_result: str = ""
    device_id: str
    platform: str = "android"
    app_package: str = ""
    max_steps: int = Field(default=20, ge=1, le=100)
    timeout_seconds: int = Field(default=300, ge=10, le=3600)


class SessionPatch(BaseModel):
    status: str | None = None
    summary: str | None = None
    error_message: str | None = None


class StepCreate(BaseModel):
    sequence: int = Field(ge=1)
    event_type: str = "action"
    title: str = ""
    detail: str = ""
    action: str = ""
    executor: str = "deterministic"
    action_args: dict[str, Any] = Field(default_factory=dict)
    page_before: dict[str, Any] = Field(default_factory=dict)
    page_after: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: str = "recorded"
    duration_ms: int = Field(default=0, ge=0, le=3600000)


class KnowledgeCreate(BaseModel):
    source_session_id: str = ""
    title: str = Field(min_length=1, max_length=300)
    category: str = "other"
    description: str = ""
    trigger_conditions: dict[str, Any] = Field(default_factory=dict)
    recognition_rules: dict[str, Any] = Field(default_factory=dict)
    handling_rules: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    app_package: str = ""
    platform: str = ""
    risk_level: str = "normal"


class KnowledgePatch(BaseModel):
    title: str | None = None
    category: str | None = None
    description: str | None = None
    trigger_conditions: dict[str, Any] | None = None
    recognition_rules: dict[str, Any] | None = None
    handling_rules: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    status: str | None = None


@router.get("/overview")
async def overview(request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    sessions = (await db.execute(select(ExploreSession).where(
        ExploreSession.account_id == account_id
    ))).scalars().all()
    knowledge = (await db.execute(select(ExploreKnowledge).where(
        ExploreKnowledge.account_id == account_id
    ))).scalars().all()
    return {
        "session_total": len(sessions),
        "session_running": sum(row.status in {"queued", "running"} for row in sessions),
        "session_completed": sum(row.status == "completed" for row in sessions),
        "knowledge_total": len(knowledge),
        "knowledge_pending": sum(row.status in {"discovered", "pending_review"} for row in knowledge),
        "knowledge_confirmed": sum(row.status == "confirmed" for row in knowledge),
        "recent_sessions": [_session_out(row) for row in sorted(
            sessions, key=lambda item: item.created_at, reverse=True
        )[:5]],
    }


@router.get("/sessions")
async def list_sessions(
    request: Request, status: str = "", limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ExploreSession).where(ExploreSession.account_id == require_account_id(request))
    if status:
        stmt = stmt.where(ExploreSession.status == status)
    rows = (await db.execute(stmt.order_by(ExploreSession.created_at.desc()).limit(limit))).scalars().all()
    return [_session_out(row) for row in rows]


@router.post("/sessions", status_code=201)
async def create_session(body: SessionCreate, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    device = (await db.execute(select(Device).where(
        Device.id == body.device_id, Device.account_id == account_id
    ))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    row = ExploreSession(
        account_id=account_id, title=body.title.strip() or body.goal[:80],
        goal=body.goal.strip(), expected_result=body.expected_result,
        device_id=body.device_id, platform=body.platform,
        app_package=body.app_package, max_steps=body.max_steps,
        timeout_seconds=body.timeout_seconds, status="draft",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _session_out(row)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    row = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    steps = (await db.execute(select(ExploreStep).where(
        ExploreStep.session_id == session_id, ExploreStep.account_id == account_id
    ).order_by(ExploreStep.sequence))).scalars().all()
    return {**_session_out(row), "steps": [_step_out(step) for step in steps]}


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, body: SessionPatch, request: Request, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == require_account_id(request)
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    if body.status is not None:
        if body.status not in {"draft", "running", "paused", "completed", "failed", "cancelled"}:
            raise HTTPException(status_code=422, detail="Invalid exploration status")
        row.status = body.status
        if body.status == "running" and row.started_at is None:
            row.started_at = datetime.utcnow()
        if body.status in {"completed", "failed", "cancelled"}:
            row.finished_at = datetime.utcnow()
    if body.summary is not None:
        row.summary = body.summary
    if body.error_message is not None:
        row.error_message = body.error_message
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return _session_out(row)


@router.get("/sessions/{session_id}/timeline")
async def get_session_timeline(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    session = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    rows = (await db.execute(select(ExploreStep).where(
        ExploreStep.session_id == session_id, ExploreStep.account_id == account_id
    ).order_by(ExploreStep.sequence))).scalars().all()
    steps = [_step_out(row) for row in rows]
    return {
        "session_id": session_id,
        "scenes": build_scene_timeline(steps),
        "event_count": len(steps),
    }


@router.post("/sessions/{session_id}/steps", status_code=201)
async def append_step(session_id: str, body: StepCreate, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    session = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    row = ExploreStep(
        account_id=account_id, session_id=session_id, sequence=body.sequence,
        event_type=body.event_type, title=body.title, detail=body.detail,
        action=body.action, executor=body.executor,
        action_args_json=json.dumps(body.action_args, ensure_ascii=False),
        page_before_json=json.dumps(body.page_before, ensure_ascii=False),
        page_after_json=json.dumps(body.page_after, ensure_ascii=False),
        evidence_json=json.dumps(body.evidence, ensure_ascii=False),
        status=body.status, duration_ms=body.duration_ms,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _step_out(row)


@router.post("/sessions/{session_id}/start", status_code=202)
async def start_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    row = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    from core.exploration_runner import exploration_jobs, run_exploration
    task = exploration_jobs.get(session_id)
    if task and not task.done():
        raise HTTPException(status_code=409, detail="探索任务已经在运行")
    if row.status not in {"draft", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail=f"当前状态不能启动探索：{row.status}")
    row.status = "queued"
    row.error_message = ""
    row.summary = "等待设备空闲"
    row.finished_at = None
    row.updated_at = datetime.utcnow()
    await db.commit()
    task = asyncio.create_task(run_exploration(session_id, account_id))
    exploration_jobs[session_id] = task
    return {"session_id": session_id, "status": "queued"}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    row = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    from core.exploration_runner import stop_exploration
    stopped = await stop_exploration(session_id)
    if not stopped and row.status not in {"completed", "failed", "cancelled"}:
        row.status = "cancelled"
        row.finished_at = datetime.utcnow()
        row.summary = "探索已停止"
        await db.commit()
    return {"session_id": session_id, "stopped": stopped, "status": "cancelled" if stopped else row.status}


@router.get("/knowledge")
async def list_knowledge(
    request: Request, status: str = "", category: str = "",
    limit: int = Query(default=100, ge=1, le=300), db: AsyncSession = Depends(get_db),
):
    stmt = select(ExploreKnowledge).where(ExploreKnowledge.account_id == require_account_id(request))
    if status:
        stmt = stmt.where(ExploreKnowledge.status == status)
    if category:
        stmt = stmt.where(ExploreKnowledge.category == category)
    rows = (await db.execute(stmt.order_by(ExploreKnowledge.updated_at.desc()).limit(limit))).scalars().all()
    return [_knowledge_out(row) for row in rows]


@router.post("/knowledge", status_code=201)
async def create_knowledge(body: KnowledgeCreate, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    if body.source_session_id:
        source = (await db.execute(select(ExploreSession).where(
            ExploreSession.id == body.source_session_id, ExploreSession.account_id == account_id
        ))).scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Source exploration session not found")
    row = ExploreKnowledge(
        account_id=account_id, source_session_id=body.source_session_id,
        title=body.title.strip(), category=body.category, description=body.description,
        trigger_conditions_json=json.dumps(body.trigger_conditions, ensure_ascii=False),
        recognition_rules_json=json.dumps(body.recognition_rules, ensure_ascii=False),
        handling_rules_json=json.dumps(body.handling_rules, ensure_ascii=False),
        evidence_json=json.dumps(body.evidence, ensure_ascii=False),
        app_package=body.app_package, platform=body.platform, risk_level=body.risk_level,
        status="discovered",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _knowledge_out(row)


@router.patch("/knowledge/{knowledge_id}")
async def patch_knowledge(knowledge_id: str, body: KnowledgePatch, request: Request, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(ExploreKnowledge).where(
        ExploreKnowledge.id == knowledge_id, ExploreKnowledge.account_id == require_account_id(request)
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Exploration knowledge not found")
    updates = body.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in {"discovered", "pending_review", "validated", "confirmed", "invalidated", "disabled"}:
        raise HTTPException(status_code=422, detail="Invalid knowledge status")
    json_fields = {
        "trigger_conditions": "trigger_conditions_json",
        "recognition_rules": "recognition_rules_json",
        "handling_rules": "handling_rules_json",
        "evidence": "evidence_json",
    }
    for key, value in updates.items():
        if key in json_fields:
            setattr(row, json_fields[key], json.dumps(value, ensure_ascii=False))
        elif key == "status":
            row.status = value
            if value == "confirmed":
                row.confirmed_at = datetime.utcnow()
                row.confirmed_by = str(getattr(request.state, "user_id", "") or "")
            elif value in {"invalidated", "disabled"}:
                row.confirmed_at = None
                row.confirmed_by = ""
        else:
            setattr(row, key, value)
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return _knowledge_out(row)


@router.get("/sessions/{session_id}/evidence/{filename}")
async def get_session_evidence(session_id: str, filename: str, request: Request):
    """Serve only evidence files belonging to the authenticated session."""
    import mimetypes
    from pathlib import Path
    from fastapi.responses import FileResponse
    if filename != Path(filename).name or filename in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="Evidence not found")
    account_id = require_account_id(request)
    async with AsyncSessionLocal() as db:
        session = (await db.execute(select(ExploreSession).where(
            ExploreSession.id == session_id, ExploreSession.account_id == account_id
        ))).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Exploration session not found")
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Evidence not found")
    session_root = (Path(__file__).resolve().parent.parent / "data" / "explorations" / session_id).resolve()
    target = None
    # Current exploration writes to evidence/; older persisted traces may refer to screenshots/.
    for directory_name in ("evidence", "screenshots"):
        evidence_dir = (session_root / directory_name).resolve()
        if evidence_dir.parent != session_root:
            continue
        candidate = (evidence_dir / filename).resolve()
        if candidate.parent == evidence_dir and candidate.is_file():
            target = candidate
            break
    if target is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@router.post("/sessions/{session_id}/generate-plan", status_code=201)
async def generate_execution_plan(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a draft in the existing versioned execution-plan system."""
    account_id = require_account_id(request)
    session = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id
    ))).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    if session.status not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="探索尚未结束，不能生成执行计划")
    traces = (await db.execute(select(ExploreStep).where(
        ExploreStep.session_id == session_id, ExploreStep.account_id == account_id
    ).order_by(ExploreStep.sequence))).scalars().all()
    if not traces:
        raise HTTPException(status_code=422, detail="没有可转换的操作步骤")

    steps, warnings = [], []
    supported = {"tap", "tap_element", "swipe", "scroll", "input_text", "global_action", "press_key", "start_app", "wait"}
    for index, trace in enumerate(traces, 1):
        if trace.event_type != "action":
            continue
        args = _json(trace.action_args_json, {})
        before = _json(trace.page_before_json, {})
        elements = before.get("elements", []) if isinstance(before, dict) else []
        action = (trace.action or "").lower()
        locator = {}
        if action == "tap_element":
            target_index = args.get("index")
            element = next((item for item in elements if str(item.get("index")) == str(target_index)), None)
            if element:
                for source, dest in (("resourceId", "resource_id"), ("text", "text"),
                                     ("contentDescription", "content_description"), ("className", "class_name")):
                    if element.get(source):
                        locator[dest] = element[source]
            if locator:
                action = "tap"
                args = {}
            else:
                warnings.append(f"步骤 {index}: 原始控件索引 {target_index!r} 缺少可复用定位信息；已保留 tap_element 动作，需人工补充定位后才能回放")
        if action not in supported:
            warnings.append(f"步骤 {index}: 动作 {trace.action or '(空)'} 暂不支持自动回放")
            continue
        if action == "input_text" and str(args.get("text", "")).startswith("[已隐藏"):
            warnings.append(f"步骤 {index}: 输入内容已脱敏，需要人工补录")
        executor = "ai"
        import_status = "ready"
        import_status = "needs_confirmation"
        if action == "tap" and not locator and not all(k in args for k in ("x", "y")):
            import_status = "needs_locator"
        if action == "tap_element" or (trace.action == "tap_element" and not locator):
            import_status = "needs_locator"
            executor = "ai"
        if action == "input_text" and str(args.get("text", "")).startswith("[已隐藏"):
            import_status = "needs_confirmation"
        description = f"{trace.action}：{trace.detail[:160]}"
        steps.append({
            "step_id": f"explore_{trace.id}", "sequence": len(steps) + 1,
            "action": action, "args": args, "executor": executor,
            "execution_channel": "ai_agent",
            "locator": locator, "fallback": None, "precondition": None,
            "wait_condition": None, "timing": {}, "verification": None,
            "on_failure": "fail",
            "intent": {"description": description, "expected_result": "",
                       "source": "exploration", "status": "needs_confirmation"},
            "import_status": import_status,
            "source_event_id": trace.id,
        })
    if not steps:
        raise HTTPException(status_code=422, detail="没有可转换的受支持动作")

    suite = TestSuite(
        name=f"AI探索 · {session.title[:80]}",
        source_format="exploration",
        app_package=session.app_package or "",
        account_id=account_id,
    )
    db.add(suite)
    await db.flush()
    case = TestCase(
        suite_id=suite.id, path=session.goal, expected=session.expected_result or "",
        account_id=account_id,
    )
    db.add(case)
    await db.flush()
    plan = ExecutionPlan(
        test_case_id=case.id, suite_id=suite.id, version=1,
        status="draft", source="exploration", platform=session.platform or "",
        app_package=session.app_package or "",
        metadata_json=json.dumps({
            "exploration_session_id": session_id,
            "expected": session.expected_result or "",
            "review_required": True,
        }, ensure_ascii=False),
        steps_json=json.dumps(steps, ensure_ascii=False),
        warnings_json=json.dumps(warnings, ensure_ascii=False),
        account_id=account_id,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return {
        "plan_id": plan.id, "test_case_id": case.id, "suite_id": suite.id,
        "version": plan.version, "status": plan.status,
        "steps": steps, "warnings": warnings,
        "message": "已生成执行计划草稿；所有步骤均需人工确认意图和验证条件后才能运行。",
    }
