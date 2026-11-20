"""Run a bounded AI exploration session using the existing agent/device adapter."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from agent.ws_device import WebSocketDevice
from core.device_scheduler import device_ownership
from core.settings_store import (
    apply_aws_env, build_fallback_chain, load_settings, resolve_api_base,
    resolve_api_key, resolve_language, resolve_provider_model, resolve_verifier,
)
from core.test_agent import TestCaseAgent
from core.test_parser import TestCaseData
from db.database import AsyncSessionLocal
from db.exploration_models import ExploreSession, ExploreStep

logger = logging.getLogger(__name__)
exploration_jobs: dict[str, asyncio.Task] = {}


def _safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)[:100] or "step"


async def _set_session(session_id: str, **values) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(ExploreSession, session_id)
        if row:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = datetime.utcnow()
            await db.commit()


async def _persist_result(session_id: str, account_id: str, result) -> None:
    # Keep binary evidence on disk; SQLite stores only relative paths and metadata.
    root = Path(__file__).resolve().parent.parent / "data" / "explorations" / session_id / "screenshots"
    root.mkdir(parents=True, exist_ok=True)
    actions = result.action_history or []
    step_logs = result.step_logs or []
    logs_by_step = {getattr(item, 'step', index + 1): item for index, item in enumerate(step_logs)}
    async with AsyncSessionLocal() as db:
        for index, action in enumerate(actions, 1):
            record = action if isinstance(action, dict) else {}
            fn_name = str(record.get("fn_name") or record.get("action") or "")
            args = record.get("args") if isinstance(record.get("args"), dict) else {}
            detail = str(record.get("result") or record.get("action_result") or "")
            evidence = {}
            step_number = int(record.get("step") or index)
            step_log = logs_by_step.get(step_number)
            if step_log is not None:
                encoded = getattr(step_log, "screenshot_b64", "") or ""
                if encoded:
                    try:
                        raw = base64.b64decode(encoded, validate=True)
                        name = f"{index:03d}_{_safe_filename(fn_name)}.jpg"
                        (root / name).write_bytes(raw)
                        evidence["screenshot"] = str(Path("screenshots") / name)
                    except Exception:
                        logger.warning("Could not persist exploration screenshot %s/%s", session_id, index)
            db.add(ExploreStep(
                account_id=account_id, session_id=session_id, sequence=index,
                event_type="action", title=fn_name or f"Step {index}",
                detail=detail, action=fn_name, executor="ai",
                action_args_json=json.dumps(args, ensure_ascii=False),
                page_before_json="{}", page_after_json="{}",
                evidence_json=json.dumps(evidence, ensure_ascii=False),
                status="completed" if not detail.startswith("ERROR") else "failed",
                duration_ms=int(getattr(step_log, "action_ms", 0)) if step_log is not None else 0,
            ))
        await db.commit()


async def run_exploration(session_id: str, account_id: str) -> None:
    acquired = False
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(ExploreSession).where(
                ExploreSession.id == session_id,
                ExploreSession.account_id == account_id,
            ))).scalar_one_or_none()
            if not row:
                return
            config = {
                "device_id": row.device_id, "goal": row.goal,
                "expected_result": row.expected_result, "app_package": row.app_package,
                "max_steps": row.max_steps, "timeout_seconds": row.timeout_seconds,
            }
        async with device_ownership(config["device_id"], f"explore:{session_id}"):
            acquired = True
            device = WebSocketDevice(config["device_id"])
            if not device.is_connected():
                raise RuntimeError("设备离线，未执行；请检查 Portal 或本机 ADB 连接")
            await _set_session(session_id, status="running", started_at=datetime.utcnow(),
                               error_message="", summary="已获得设备独占权，开始 AI 探索")
            settings = await load_settings(account_id)
            apply_aws_env(settings)
            provider, model = resolve_provider_model(settings)
            api_key, api_base = resolve_api_key(settings, provider), resolve_api_base(settings, provider)
            if not api_key and provider.lower() != "ollama":
                raise RuntimeError(f"当前账号未配置 {provider} API Key")
            verifier = resolve_verifier(settings)
            fallbacks = build_fallback_chain(settings, provider, model)

            async def emit(message: str) -> None:
                logger.info("[exploration:%s] %s", session_id, message)
                # The summary is also updated during execution so polling clients
                # can show the most recent activity without a second event system.
                await _set_session(session_id, summary=str(message)[:2000])

            agent = TestCaseAgent(
                device=device, provider=provider, model=model, api_key=api_key,
                api_base=api_base, max_steps=config["max_steps"], step_delay=0.8,
                log_callback=emit, verifier_provider=verifier[0],
                verifier_model=verifier[1], verifier_api_key=verifier[2],
                verifier_api_base=verifier[3], fallbacks=fallbacks,
                allow_subagents=False, language=resolve_language(settings),
                target_package=config["app_package"],
            )
            # Exploration intentionally excludes package installation and other
            # non-essential tools. Device actions still go through TestCaseAgent's
            # existing dispatcher and WebSocketDevice transport.
            allowed = {
                "tap", "tap_element", "swipe", "scroll", "input_text",
                "press_key", "global_action", "start_app", "list_packages",
                "wait", "request_screenshot", "mark_done", "remember",
                "search_knowledge",
            }
            agent._tools = [
                tool for tool in agent._tools
                if tool.get("function", {}).get("name") in allowed
            ]
            expected = config["expected_result"].strip() or "已完成用户描述的探索目标"
            case = TestCaseData(
                path=f"AI 探索目标：{config['goal']}",
                expected=expected,
            )
            result = await asyncio.wait_for(agent.run(case), timeout=config["timeout_seconds"])
            await _persist_result(session_id, account_id, result)
            status = "completed" if result.status == "pass" else "failed"
            await _set_session(session_id, status=status, finished_at=datetime.utcnow(),
                               summary=result.reason or f"探索结束：{result.status}",
                               error_message="" if status == "completed" else result.reason)
    except asyncio.CancelledError:
        await _set_session(session_id, status="cancelled", finished_at=datetime.utcnow(),
                           summary="用户已停止探索")
        raise
    except asyncio.TimeoutError:
        await _set_session(session_id, status="failed", finished_at=datetime.utcnow(),
                           summary="探索超时", error_message="探索超过设定时限，已自动停止")
    except Exception as exc:
        logger.exception("Exploration %s failed", session_id)
        await _set_session(session_id, status="failed", finished_at=datetime.utcnow(),
                           summary="探索失败", error_message=str(exc)[:2000])
    finally:
        exploration_jobs.pop(session_id, None)


async def stop_exploration(session_id: str) -> bool:
    task = exploration_jobs.get(session_id)
    if task is None:
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return True


async def shutdown_explorations() -> None:
    tasks = list(exploration_jobs.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    exploration_jobs.clear()
