"""Account-scoped APIs for AI exploration sessions, traces and reusable knowledge.

This first slice persists exploration intent and evidence. Device-driving orchestration
is deliberately not exposed until the interaction loop and safety gates are implemented.
"""
from __future__ import annotations

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
from db.models import Device

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
        "session_running": sum(row.status == "running" for row in sessions),
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
