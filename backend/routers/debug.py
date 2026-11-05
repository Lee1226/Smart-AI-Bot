"""Isolated interactive agent debug sessions (no TestRun/TestResult persistence)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent.ws_device import WebSocketDevice
from core.settings_store import (
    load_settings, resolve_api_key, resolve_api_base, resolve_provider_model,
    apply_aws_env, resolve_language,
)
from core.test_agent import TestCaseAgent
from core.test_parser import TestCaseData
from core.device_scheduler import locks
from db.database import AsyncSessionLocal
from db.models import Device as DeviceModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug-console"])


class DebugStart(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4000)
    expected: str = Field(default="完成用户要求的操作，并说明执行结果", max_length=2000)
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=256)
    max_steps: int = Field(default=20, ge=1, le=60)


@dataclass
class DebugSession:
    id: str
    device_id: str
    logs: list[str] = field(default_factory=list)
    status: str = "queued"
    task: Optional[asyncio.Task] = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, message: str):
        async with self.condition:
            self.logs.append(str(message))
            self.condition.notify_all()

    async def set_status(self, status: str):
        async with self.condition:
            self.status = status
            self.condition.notify_all()


_sessions: dict[str, DebugSession] = {}


def _owned_session(session_id: str, request: Request) -> DebugSession:
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="调试会话不存在或已过期")
    account_id = getattr(request.state, "account_id", None)
    if getattr(state, "account_id", None) != account_id:
        raise HTTPException(status_code=404, detail="调试会话不存在")
    return state


@router.post("/sessions", status_code=202)
async def start_debug(body: DebugStart, request: Request):
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    async with AsyncSessionLocal() as db:
        owned_device = await db.get(DeviceModel, body.device_id)
        if not owned_device or owned_device.account_id != account_id:
            raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    settings = await load_settings(account_id)
    default_provider, default_model = resolve_provider_model(settings)
    provider = (body.provider or default_provider).strip().lower()
    model = (body.model or default_model).strip()
    if not provider or not model:
        raise HTTPException(status_code=422, detail="请指定模型服务商和模型")
    api_key = resolve_api_key(settings, provider)
    api_base = resolve_api_base(settings, provider)
    if provider != "ollama" and not api_key:
        raise HTTPException(status_code=400, detail=f"尚未配置 {provider} API Key，请先到设置页面配置")
    device = WebSocketDevice(body.device_id)
    if not device.is_connected():
        raise HTTPException(status_code=409, detail="设备当前离线，请先连接设备")
    # Reuse the exact same per-device lock as formal test runs.
    lock = locks.setdefault(body.device_id, asyncio.Lock())
    session = DebugSession(id=uuid.uuid4().hex, device_id=body.device_id)
    session.account_id = account_id
    _sessions[session.id] = session

    async def work():
        acquired = False
        try:
            await session.emit("等待设备独占权；调试不会与正式测试同时操作设备")
            await lock.acquire()
            acquired = True
            if not device.is_connected():
                raise RuntimeError("等待设备空闲期间设备已断开")
            await session.set_status("running")
            await session.emit(f"已获得设备独占权，使用模型：{provider}/{model}")
            apply_aws_env(settings)
            agent = TestCaseAgent(
                device=device, provider=provider, model=model,
                api_key=api_key, api_base=api_base,
                max_steps=body.max_steps, step_delay=1.0,
                log_callback=session.emit,
                fallbacks=[], allow_subagents=False,
                language=resolve_language(settings),
            )
            result = await agent.run(TestCaseData(
                path=body.goal.strip(), expected=body.expected.strip() or "完成用户要求的操作",
            ))
            await session.emit(f"执行结束：{result.status}；步骤数：{result.steps}")
            if result.reason:
                await session.emit(f"结果说明：{result.reason}")
            await session.set_status("done")
        except asyncio.CancelledError:
            await session.emit("调试任务已取消")
            await session.set_status("cancelled")
            raise
        except Exception as exc:
            logger.exception("Debug session %s failed", session.id)
            await session.emit(f"执行错误：{exc}")
            await session.set_status("error")
        finally:
            if acquired:
                lock.release()
            async with session.condition:
                session.condition.notify_all()

    session.task = asyncio.create_task(work(), name=f"debug-{session.id}")
    return {"session_id": session.id, "status": session.status}


@router.get("/sessions/{session_id}")
async def get_debug_session(session_id: str, request: Request, after: int = 0):
    session = _owned_session(session_id, request)
    return {
        "session_id": session.id,
        "device_id": session.device_id,
        "status": session.status,
        "logs": session.logs[max(0, after):],
        "next": len(session.logs),
    }


@router.post("/sessions/{session_id}/stop")
async def stop_debug(session_id: str, request: Request):
    session = _owned_session(session_id, request)
    if session.task and not session.task.done():
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
    return {"session_id": session.id, "status": session.status}
