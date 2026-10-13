"""Authentication endpoints: register, login, me."""
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt_handler import create_access_token
from auth.password import hash_password, verify_password
from db.database import AsyncSessionLocal
from db.models import (
    Account, Device, LessonLearned, StepNode, TestCase, TestResult, TestRun,
    TestStepLog, TestSuite, User,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _enforce_rate(scope: str, identity: str, limit: int, window_seconds: int) -> None:
    """Small single-instance throttle; production proxies may add a global limit."""
    key = (scope, identity.strip().lower())
    now = time.monotonic()
    attempts = _attempts[key]
    while attempts and attempts[0] <= now - window_seconds:
        attempts.popleft()
    if len(attempts) >= limit:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    attempts.append(now)


def _clear_rate(scope: str, identity: str) -> None:
    _attempts.pop((scope, identity.strip().lower()), None)


def _public_registration_enabled(has_account: bool) -> bool:
    configured = os.getenv("SAB_ALLOW_PUBLIC_REGISTRATION", "").strip().lower()
    return not has_account or configured in {"1", "true", "yes", "on"}


# ── Request models ────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    account_name: str      # e.g. "MyTeam"
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str




# ── Response models ───────────────────────────────────────────────────────────

class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    account_id: str
    account_name: str
    role: str


async def _claim_legacy_resources(db: AsyncSession, account_id: str) -> None:
    """Assign pre-auth, unowned rows to the installation's first account only."""
    for model in (
        Device, TestSuite, TestCase, StepNode, TestRun, TestResult,
        TestStepLog, LessonLearned,
    ):
        await db.execute(
            update(model).where(model.account_id.is_(None)).values(account_id=account_id)
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/config")
async def auth_config():
    async with AsyncSessionLocal() as db:
        has_account = (
            await db.execute(select(Account.id).limit(1))
        ).scalar_one_or_none() is not None
    return {
        "registration_enabled": _public_registration_enabled(has_account),
    }

@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request):
    """Create a new account with one owner user."""
    # Validate
    client = request.client.host if request.client else "unknown"
    _enforce_rate("register", client, 5, 3600)
    if len(req.password) < 10:
        raise HTTPException(status_code=400, detail="密码至少 10 位")
    if "@" not in req.email:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")

    async with AsyncSessionLocal() as db:
        has_account = (
            await db.execute(select(Account.id).limit(1))
        ).scalar_one_or_none() is not None
        if not _public_registration_enabled(has_account):
            raise HTTPException(status_code=403, detail="公开注册已关闭，请联系管理员邀请")

        # Check email uniqueness
        existing = await db.execute(select(User).where(User.email == req.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="该邮箱已被注册")

        # Check account name uniqueness
        existing_acct = await db.execute(
            select(Account).where(Account.name == req.account_name)
        )
        if existing_acct.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="该账号名已被使用")

        is_first_account = not has_account

        # Create account
        account = Account(name=req.account_name)
        db.add(account)
        await db.flush()

        # Create owner user
        user = User(
            account_id=account.id,
            email=req.email,
            password_hash=hash_password(req.password),
            name=req.name or req.email.split("@")[0],
            role="owner",
        )
        db.add(user)
        if is_first_account:
            await _claim_legacy_resources(db, account.id)
        await db.commit()
        await db.refresh(user)

        # Issue JWT
        token = create_access_token({
            "account_id": account.id,
            "user_id": user.id,
            "role": user.role,
        })
        return AuthResponse(
            access_token=token,
            user={
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "account_id": account.id,
                "account_name": account.name,
                "role": user.role,
            },
        )


@router.post("/login")
async def login(req: LoginRequest):
    """Email + password → JWT."""
    _enforce_rate("login", req.email, 10, 300)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == req.email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="邮箱或密码错误")

        if not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="邮箱或密码错误")

        _clear_rate("login", req.email)

        # Update last login
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()

        # Load account name
        account = await db.get(Account, user.account_id)

        token = create_access_token({
            "account_id": user.account_id,
            "user_id": user.id,
            "role": user.role,
        })
        return AuthResponse(
            access_token=token,
            user={
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "account_id": user.account_id,
                "account_name": account.name if account else "",
                "role": user.role,
            },
        )


@router.get("/me", response_model=MeResponse)
async def get_me(request: Request):
    """Return the current authenticated user's info."""
    if not request.state.user_id:
        raise HTTPException(status_code=401, detail="未登录")

    async with AsyncSessionLocal() as db:
        user = await db.get(User, request.state.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        account = await db.get(Account, user.account_id)
        return MeResponse(
            user_id=user.id,
            email=user.email,
            name=user.name,
            account_id=user.account_id,
            account_name=account.name if account else "",
            role=user.role,
        )
