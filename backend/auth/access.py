"""Central authorization helpers for account-scoped and operator resources."""

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Account, Device, StepNode, TestRun, TestSuite, User


def require_account_id(request: Request) -> str:
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return account_id


def require_role(request: Request, *roles: str) -> None:
    if getattr(request.state, "role", None) not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


async def operator_account_id(db: AsyncSession) -> str:
    import os

    # 1) Explicit pin wins (documented escape hatch; survives a fresh DB).
    configured = os.getenv("SAB_OPERATOR_ACCOUNT_ID", "").strip()
    if configured:
        return configured
    # 2) Account flagged `is_operator` in the DB — the durable, per-installation
    #    source of truth.  Survives restarts/rebuilds and is not tied to creation order.
    flagged = (
        await db.execute(select(Account.id).where(Account.is_operator.is_(True)))
    ).scalars().first()
    if flagged:
        return flagged
    # 3) Fallback: oldest account (keeps legacy single-account installs working).
    return (
        await db.execute(select(Account.id).order_by(Account.created_at, Account.id).limit(1))
    ).scalar_one_or_none() or ""


async def require_operator(request: Request, db: AsyncSession) -> None:
    """Restrict instance-global configuration to the installation's operator account.

    ``SAB_OPERATOR_ACCOUNT_ID`` can pin it explicitly; otherwise the account
    flagged ``is_operator`` in the DB wins; the oldest account is a last-resort
    fallback for legacy single-account installs.  This keeps global secrets
    (LLM API keys) out of every non-operator tenant's reach.
    """
    require_role(request, "owner", "admin")
    expected = await operator_account_id(db)
    if not expected or require_account_id(request) != expected:
        raise HTTPException(status_code=403, detail="Operator account required")


async def current_user(request: Request, db: AsyncSession) -> User:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="User session required")
    user = await db.get(User, user_id)
    if not user or user.account_id != require_account_id(request):
        raise HTTPException(status_code=401, detail="User session is no longer valid")
    return user


async def require_device(request: Request, db: AsyncSession, device_id: str) -> Device:
    row = (await db.execute(select(Device).where(
        Device.id == device_id, Device.account_id == require_account_id(request)
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    return row


async def require_suite(request: Request, db: AsyncSession, suite_id: str) -> TestSuite:
    row = (await db.execute(select(TestSuite).where(
        TestSuite.id == suite_id, TestSuite.account_id == require_account_id(request)
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Suite not found")
    return row


async def require_run(request: Request, db: AsyncSession, run_id: str) -> TestRun:
    row = (await db.execute(select(TestRun).where(
        TestRun.id == run_id, TestRun.account_id == require_account_id(request)
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


async def require_node(request: Request, db: AsyncSession, node_id: str) -> StepNode:
    row = (await db.execute(
        select(StepNode).join(TestSuite, TestSuite.id == StepNode.suite_id).where(
            StepNode.id == node_id, TestSuite.account_id == require_account_id(request)
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    return row
