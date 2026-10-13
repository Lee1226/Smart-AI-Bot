"""FastAPI middleware that resolves Authorization header into request.state."""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt_handler import decode_token
from db.database import AsyncSessionLocal
from db.models import ApiKey, Device, StepNode, TestRun, TestSuite, User

logger = logging.getLogger(__name__)

# API endpoints that do NOT require authentication.
_PUBLIC_API_PATHS = {
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/config",
}

# 无需登录的只读下载类接口（前缀匹配）。这些是「手机扫码 / 浏览器 / Portal App
# 直访」场景——客户端没有 SAB 的 JWT / API Key，但本质是公开的 APK / CLI 分发。
# 注意：只放行 GET 下载，写操作 /api/app/upload 不在此列，仍需鉴权。
_PUBLIC_API_PREFIXES = (
    "/api/regression-download",  # short-lived run-scoped APK download grant
    "/api/app/latest",
    "/api/app/download",      # 覆盖 /api/app/download 与 /api/app/download/{filename}
    "/api/app/cli/download",
)


def _is_public_api(path: str) -> bool:
    if path in _PUBLIC_API_PATHS:
        return True
    return any(path == p or path.startswith(p + "/") for p in _PUBLIC_API_PREFIXES)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


_RESOURCE_PATHS = (
    (re.compile(r"^/api/devices/([^/]+)"), Device, set()),
    (re.compile(r"^/api/suites/([^/]+)"), TestSuite, {"import", "manual"}),
    (re.compile(r"^/api/runs/([^/]+)"), TestRun, {"batch", "quick", "tree", "node", "compare"}),
)


async def _owns_path_resource(path: str, account_id: str) -> bool:
    """Fail closed for every ID-addressed tenant resource before routing."""
    async with AsyncSessionLocal() as db:
        for pattern, model, reserved in _RESOURCE_PATHS:
            match = pattern.match(path)
            if not match:
                continue
            resource_id = match.group(1)
            if resource_id in reserved:
                return True
            found = await db.execute(
                select(model.id).where(model.id == resource_id, model.account_id == account_id)
            )
            return found.scalar_one_or_none() is not None

        match = re.match(r"^/api/nodes/([^/]+)", path)
        if match and match.group(1) not in {"search", "usage", "all"}:
            found = await db.execute(
                select(StepNode.id)
                .join(TestSuite, TestSuite.id == StepNode.suite_id)
                .where(StepNode.id == match.group(1), TestSuite.account_id == account_id)
            )
            return found.scalar_one_or_none() is not None
    return True


class AuthMiddleware(BaseHTTPMiddleware):
    """Attach account_id / user_id / role to request.state on every request.

    Resolution order:
    1. ``Authorization: Bearer sk-…`` → API key → account_id + permissions
    2. ``Authorization: Bearer …`` (not sk-) → JWT → account_id + user_id + role
    3. No header → unauthenticated

    All /api/* requests require authentication (except public auth endpoints).
    Non-API paths (static frontend) always pass through.
    """

    async def dispatch(self, request: Request, call_next):
        # Default: unauthenticated
        request.state.account_id = None
        request.state.user_id = None
        request.state.role = None
        request.state.permissions = []
        request.state.api_key_created_by = None
        request.state.api_key_name = None

        auth_header: Optional[str] = request.headers.get("Authorization", "")
        token: Optional[str] = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        # Fallback: token in query string (for <img> tags / WebSocket that
        # can't set the Authorization header).
        if not token:
            token = request.query_params.get("token")

        if token:
            if token.startswith("sk-"):
                # ── API Key ──
                key_hash = _hash_key(token)
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(ApiKey).where(ApiKey.key_hash == key_hash)
                    )
                    api_key = result.scalar_one_or_none()
                    if api_key:
                        request.state.account_id = api_key.account_id
                        request.state.auth_type = "apikey"
                        request.state.api_key_created_by = api_key.created_by
                        request.state.api_key_name = api_key.name
                        creator = await db.get(User, api_key.created_by) if api_key.created_by else None
                        if creator and creator.account_id == api_key.account_id:
                            request.state.user_id = creator.id
                            request.state.role = creator.role
                        try:
                            api_key.last_used_at = datetime.now(timezone.utc)
                            await db.commit()
                        except Exception:
                            pass
            else:
                # ── JWT (user session) ──
                payload = decode_token(token)
                if payload:
                    user_id = payload.get("user_id")
                    account_id = payload.get("account_id")
                    if user_id and account_id:
                        async with AsyncSessionLocal() as db:
                            user = await db.get(User, user_id)
                            if user and user.account_id == account_id:
                                request.state.account_id = user.account_id
                                request.state.user_id = user.id
                                request.state.role = user.role
                                request.state.auth_type = "jwt"

        # All /api/* requests require auth (except public auth endpoints & public downloads)
        path = request.url.path
        if path.startswith("/api/") and not _is_public_api(path):
            if request.state.account_id is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                )
            if not await _owns_path_resource(path, request.state.account_id):
                return JSONResponse(status_code=404, content={"detail": "Resource not found"})

        response = await call_next(request)
        return response
