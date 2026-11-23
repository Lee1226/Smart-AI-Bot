"""Stable, tenant-scoped read-only API for external integrations."""
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import Device, TestCase, TestRun, TestSuite

router = APIRouter(prefix="/api/open/v1", tags=["open-platform"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def require_scope(request: Request, scope: str) -> str:
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if getattr(request.state, "auth_type", "") == "apikey":
        permissions = set(getattr(request.state, "permissions", []) or [])
        if scope not in permissions:
            raise HTTPException(status_code=403, detail=f"API key missing permission: {scope}")
    return account_id


@router.get("")
async def open_api_info(request: Request):
    require_scope(request, "devices:read")
    return {
        "name": "AI Terminal Open API",
        "version": "v1",
        "authentication": "Bearer API Key",
        "available_resources": ["devices", "suites", "runs"],
        "read_only": True,
    }


@router.get("/devices")
async def list_open_devices(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    account_id = require_scope(request, "devices:read")
    async with AsyncSessionLocal() as db:
        stmt = select(Device).where(Device.account_id == account_id)
        total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = (await db.execute(
            stmt.order_by(Device.last_seen.desc()).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {
            "items": [{
                "id": d.id,
                "name": d.name,
                "status": d.status,
                "platform": {"hdc_bridge": "harmony", "ios_bridge": "ios"}.get(d.connection_type, "android"),
                "connection_type": d.connection_type,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            } for d in rows],
            "total": total, "page": page, "page_size": page_size,
        }


@router.get("/suites")
async def list_open_suites(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    account_id = require_scope(request, "suites:read")
    async with AsyncSessionLocal() as db:
        stmt = select(TestSuite).where(TestSuite.account_id == account_id)
        total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = (await db.execute(
            stmt.order_by(TestSuite.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        items = []
        for suite in rows:
            count = await db.scalar(select(func.count()).select_from(TestCase).where(TestCase.suite_id == suite.id)) or 0
            items.append({
                "id": suite.id, "name": suite.name, "source_format": suite.source_format,
                "case_count": count, "app_package": suite.app_package or "",
                "tag": suite.tag or "", "created_at": suite.created_at.isoformat(),
            })
        return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/runs")
async def list_open_runs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("all", pattern="^(all|active|done|problem)$"),
):
    account_id = require_scope(request, "runs:read")
    async with AsyncSessionLocal() as db:
        stmt = select(TestRun).where(TestRun.account_id == account_id)
        if status == "active":
            stmt = stmt.where(TestRun.status.in_(["queued", "preparing", "pending", "running"]))
        elif status == "done":
            stmt = stmt.where(TestRun.status == "done")
        elif status == "problem":
            stmt = stmt.where(TestRun.status.in_(["error", "interrupted"]))
        total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = (await db.execute(
            stmt.order_by(TestRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {
            "items": [{
                "id": run.id, "suite_id": run.suite_id, "device_id": run.device_id,
                "status": run.status, "provider": run.provider, "model": run.model,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            } for run in rows],
            "total": total, "page": page, "page_size": page_size,
        }
