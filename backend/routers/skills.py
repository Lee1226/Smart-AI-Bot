"""Account-scoped APIs for structured reusable skills."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id
from core.skill_extractor import extract_skill_draft
from db.database import AsyncSessionLocal
from db.exploration_models import ExploreSession, ExploreStep
from db.skill_models import ReusableSkill
from db.skill_validation_models import SkillValidationRun
from core.skill_validator import validate_skill_structure

router = APIRouter(prefix="/api/skills", tags=["reusable-skills"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _out(row: ReusableSkill) -> dict:
    return {
        "id": row.id, "source_session_id": row.source_session_id,
        "name": row.name, "description": row.description, "category": row.category,
        "platform": row.platform, "app_package": row.app_package,
        "preconditions": _loads(row.preconditions_json, []),
        "recognition": _loads(row.recognition_json, {}),
        "steps": _loads(row.steps_json, []), "branches": _loads(row.branches_json, []),
        "postconditions": _loads(row.postconditions_json, []),
        "safety": _loads(row.safety_json, {}), "evidence": _loads(row.evidence_json, []),
        "warnings": _loads(row.warnings_json, []), "status": row.status,
        "version": row.version, "review_note": row.review_note,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
    }


def _validation_out(row: SkillValidationRun) -> dict:
    return {
        "id": row.id, "skill_id": row.skill_id, "skill_version": row.skill_version,
        "validation_type": row.validation_type, "status": row.status,
        "summary": _loads(row.summary_json, {}),
        "findings": _loads(row.findings_json, []),
        "device_touched": bool(row.device_touched),
        "created_at": row.created_at.isoformat(),
    }


class SkillPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    category: str | None = None
    preconditions: list[dict[str, Any]] | None = None
    recognition: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    branches: list[dict[str, Any]] | None = None
    postconditions: list[dict[str, Any]] | None = None
    safety: dict[str, Any] | None = None
    review_note: str | None = None
    status: str | None = None


_JSON_FIELDS = {
    "preconditions": "preconditions_json", "recognition": "recognition_json",
    "steps": "steps_json", "branches": "branches_json",
    "postconditions": "postconditions_json", "safety": "safety_json",
}


@router.get("")
async def list_skills(
    request: Request, status: str = "", q: str = "",
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ReusableSkill).where(ReusableSkill.account_id == require_account_id(request))
    if status:
        stmt = stmt.where(ReusableSkill.status == status)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(ReusableSkill.name.ilike(needle) | ReusableSkill.description.ilike(needle))
    rows = (await db.execute(stmt.order_by(ReusableSkill.updated_at.desc()).limit(limit))).scalars().all()
    return [_out(row) for row in rows]


@router.post("/from-exploration/{session_id}", status_code=201)
async def create_skill_from_exploration(
    session_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    account_id = require_account_id(request)
    source = (await db.execute(select(ExploreSession).where(
        ExploreSession.id == session_id, ExploreSession.account_id == account_id,
    ))).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    if source.status != "completed":
        raise HTTPException(status_code=409, detail="仅已完成的探索记录可以提炼技能")
    events = (await db.execute(select(ExploreStep).where(
        ExploreStep.session_id == session_id, ExploreStep.account_id == account_id,
    ).order_by(ExploreStep.sequence))).scalars().all()
    event_data = [{
        "id": e.id, "sequence": e.sequence, "event_type": e.event_type,
        "title": e.title, "detail": e.detail, "action": e.action, "status": e.status,
        "action_args": _loads(e.action_args_json, {}),
        "page_before": _loads(e.page_before_json, {}),
        "page_after": _loads(e.page_after_json, {}),
        "evidence": _loads(e.evidence_json, {}),
    } for e in events]
    draft = extract_skill_draft({
        "title": source.title, "goal": source.goal, "expected_result": source.expected_result,
        "platform": source.platform, "app_package": source.app_package,
    }, event_data)
    row = ReusableSkill(
        account_id=account_id, source_session_id=session_id,
        name=draft["name"], description=draft["description"], category=draft["category"],
        platform=draft["platform"], app_package=draft["app_package"],
        preconditions_json=json.dumps(draft["preconditions"], ensure_ascii=False),
        recognition_json=json.dumps(draft["recognition"], ensure_ascii=False),
        steps_json=json.dumps(draft["steps"], ensure_ascii=False),
        branches_json=json.dumps(draft["branches"], ensure_ascii=False),
        postconditions_json=json.dumps(draft["postconditions"], ensure_ascii=False),
        safety_json=json.dumps(draft["safety"], ensure_ascii=False),
        evidence_json=json.dumps(draft["evidence"], ensure_ascii=False),
        warnings_json=json.dumps(draft["warnings"], ensure_ascii=False),
        status="draft",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("/{skill_id}")
async def get_skill(skill_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(ReusableSkill).where(
        ReusableSkill.id == skill_id, ReusableSkill.account_id == require_account_id(request),
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _out(row)


@router.patch("/{skill_id}")
async def update_skill(
    skill_id: str, body: SkillPatch, request: Request, db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(ReusableSkill).where(
        ReusableSkill.id == skill_id, ReusableSkill.account_id == require_account_id(request),
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")
    updates = body.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in {"draft", "pending_review", "approved", "disabled"}:
        raise HTTPException(status_code=422, detail="Invalid skill status")
    content_fields = set(_JSON_FIELDS) | {"name", "description", "category"}
    content_changed = any(key in content_fields for key in updates)
    if content_changed and updates.get("status") == "approved":
        raise HTTPException(status_code=409, detail="请先保存技能内容，再单独进行审核")
    if content_changed:
        # Any definition edit creates a new revision and invalidates prior approval.
        row.version += 1
        row.status = "draft"
    if updates.get("status") == "approved":
        # Approval is a review marker only. Runtime execution is deliberately not wired in this phase.
        row.status = "approved"
    for key, value in updates.items():
        if key == "status":
            continue
        if key in _JSON_FIELDS:
            setattr(row, _JSON_FIELDS[key], json.dumps(value, ensure_ascii=False))
        else:
            setattr(row, key, value)
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.post("/{skill_id}/validate-structure", status_code=201)
async def validate_skill_structure_api(
    skill_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    """Validate the stored definition only; never connects to or controls a device."""
    account_id = require_account_id(request)
    row = (await db.execute(select(ReusableSkill).where(
        ReusableSkill.id == skill_id, ReusableSkill.account_id == account_id,
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")

    snapshot = _out(row)
    evidence_root = (
        Path(__file__).resolve().parent.parent / "data" / "explorations"
        / row.source_session_id
    )
    result = validate_skill_structure(snapshot, evidence_root=evidence_root)
    run = SkillValidationRun(
        account_id=account_id, skill_id=row.id, skill_version=row.version,
        validation_type="structural", status=result["status"],
        summary_json=json.dumps(result["summary"], ensure_ascii=False),
        findings_json=json.dumps(result["findings"], ensure_ascii=False),
        skill_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        device_touched=False,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return _validation_out(run)


@router.get("/{skill_id}/validations")
async def list_skill_validations(
    skill_id: str, request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    account_id = require_account_id(request)
    owned = await db.scalar(select(ReusableSkill.id).where(
        ReusableSkill.id == skill_id, ReusableSkill.account_id == account_id,
    ))
    if not owned:
        raise HTTPException(status_code=404, detail="Skill not found")
    rows = (await db.execute(select(SkillValidationRun).where(
        SkillValidationRun.skill_id == skill_id,
        SkillValidationRun.account_id == account_id,
    ).order_by(SkillValidationRun.created_at.desc()).limit(limit))).scalars().all()
    return [_validation_out(item) for item in rows]
