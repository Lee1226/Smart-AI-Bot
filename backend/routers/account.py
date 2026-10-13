"""Account management: users, API keys (owner/admin only)."""
import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.password import hash_password
from db.database import AsyncSessionLocal
from db.models import Account, ApiKey, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/account", tags=["account"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _require_role(request: Request, allowed: set):
    """Raise 403 if the current user doesn't have one of the allowed roles."""
    if not request.state.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    if request.state.role not in allowed:
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, key_hash, key_prefix). Caller shows full_key once."""
    raw = "sk-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12] + "…"
    return raw, key_hash, prefix


# ── Request / Response models ─────────────────────────────────────────────────

class AccountInfo(BaseModel):
    id: str
    name: str
    created_at: str


class UpdateAccountRequest(BaseModel):
    name: Optional[str] = None


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: str
    last_login_at: Optional[str] = None


class CreateUserRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "member"


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: Optional[str] = None


class CreateApiKeyRequest(BaseModel):
    name: str = ""


class CreateApiKeyResponse(BaseModel):
    """The full key is returned only once at creation time."""
    api_key: ApiKeyOut
    full_key: str


# ── Account ───────────────────────────────────────────────────────────────────

@router.get("", response_model=AccountInfo)
async def get_account(request: Request):
    await _require_role(request, {"owner", "admin", "member"})
    async with AsyncSessionLocal() as db:
        account = await db.get(Account, request.state.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return AccountInfo(
            id=account.id,
            name=account.name,
            created_at=account.created_at.isoformat(),
        )


@router.put("", response_model=AccountInfo)
async def update_account(request: Request, req: UpdateAccountRequest):
    await _require_role(request, {"owner"})
    async with AsyncSessionLocal() as db:
        account = await db.get(Account, request.state.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if req.name:
            # Check uniqueness
            existing = await db.execute(
                select(Account).where(Account.name == req.name, Account.id != account.id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="该账号名已被使用")
            account.name = req.name
        await db.commit()
        return AccountInfo(
            id=account.id,
            name=account.name,
            created_at=account.created_at.isoformat(),
        )


# ── Users (sub-accounts) ──────────────────────────────────────────────────────

@router.get("/users", response_model=List[UserOut])
async def list_users(request: Request):
    await _require_role(request, {"owner", "admin"})
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User)
            .where(User.account_id == request.state.account_id)
            .order_by(User.created_at)
        )
        users = result.scalars().all()
        return [
            UserOut(
                id=u.id, email=u.email, name=u.name, role=u.role,
                created_at=u.created_at.isoformat(),
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
            for u in users
        ]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(request: Request, req: CreateUserRequest):
    await _require_role(request, {"owner", "admin"})
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if "@" not in req.email:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if req.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 member")

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == req.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="该邮箱已被注册")

        user = User(
            account_id=request.state.account_id,
            email=req.email,
            password_hash=hash_password(req.password),
            name=req.name or req.email.split("@")[0],
            role=req.role,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return UserOut(
            id=user.id, email=user.email, name=user.name, role=user.role,
            created_at=user.created_at.isoformat(),
            last_login_at=None,
        )


@router.put("/users/{user_id}", response_model=UserOut)
async def update_user(request: Request, user_id: str, req: UpdateUserRequest):
    await _require_role(request, {"owner", "admin"})
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if not user or user.account_id != request.state.account_id:
            raise HTTPException(status_code=404, detail="用户不存在")
        # Cannot modify owner
        if user.role == "owner":
            raise HTTPException(status_code=403, detail="不能修改 owner 用户")
        if req.role and req.role not in ("admin", "member"):
            raise HTTPException(status_code=400, detail="角色只能是 admin 或 member")
        if req.name is not None:
            user.name = req.name
        if req.role is not None:
            user.role = req.role
        await db.commit()
        await db.refresh(user)
        return UserOut(
            id=user.id, email=user.email, name=user.name, role=user.role,
            created_at=user.created_at.isoformat(),
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        )


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(request: Request, user_id: str):
    await _require_role(request, {"owner", "admin"})
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if not user or user.account_id != request.state.account_id:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.role == "owner":
            raise HTTPException(status_code=403, detail="不能删除 owner 用户")
        await db.delete(user)
        await db.commit()


# ── API Keys ──────────────────────────────────────────────────────────────────

@router.get("/api-keys", response_model=List[ApiKeyOut])
async def list_api_keys(request: Request):
    await _require_role(request, {"owner", "admin"})
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ApiKey)
            .where(ApiKey.account_id == request.state.account_id)
            .order_by(ApiKey.created_at.desc())
        )
        keys = result.scalars().all()
        return [
            ApiKeyOut(
                id=k.id, name=k.name, key_prefix=k.key_prefix,
                created_at=k.created_at.isoformat(),
                last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            )
            for k in keys
        ]


@router.post("/api-keys", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key(request: Request, req: CreateApiKeyRequest):
    await _require_role(request, {"owner", "admin"})
    full_key, key_hash, key_prefix = _generate_api_key()
    async with AsyncSessionLocal() as db:
        api_key = ApiKey(
            account_id=request.state.account_id,
            name=req.name or "API Key",
            key_hash=key_hash,
            key_prefix=key_prefix,
            permissions="[]",
            created_by=request.state.user_id,
        )
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        return CreateApiKeyResponse(
            api_key=ApiKeyOut(
                id=api_key.id, name=api_key.name, key_prefix=api_key.key_prefix,
                created_at=api_key.created_at.isoformat(),
                last_used_at=None,
            ),
            full_key=full_key,
        )


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(request: Request, key_id: str):
    await _require_role(request, {"owner", "admin"})
    async with AsyncSessionLocal() as db:
        api_key = await db.get(ApiKey, key_id)
        if not api_key or api_key.account_id != request.state.account_id:
            raise HTTPException(status_code=404, detail="API Key 不存在")
        await db.delete(api_key)
        await db.commit()
