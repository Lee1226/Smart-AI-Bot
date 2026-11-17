"""Execute versioned plans through one device-owned dispatcher.

The scheduler owns the device for the whole run. This module only selects the
step strategy (deterministic / AI / hybrid / system) and always uses the
existing WebSocketDevice adapter for physical actions.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select, update

from agent.ws_device import WebSocketDevice
from agent.verifier import LLMVerifier
from core.run_recorder import RunRecorder
from core.settings_store import (
    apply_aws_env,
    build_fallback_chain,
    load_settings,
    resolve_api_base,
    resolve_api_key,
    resolve_language,
    resolve_verifier,
)
from core.test_agent import TestCaseAgent
from core.test_parser import TestCaseData
from db.database import AsyncSessionLocal
from db.models import ExecutionPlan, TestCase, TestResult, TestRun, TestStepLog

logger = logging.getLogger(__name__)


def build_replay_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a versioned plan step to the existing device replay contract."""
    action = str(step.get("action") or "").strip().lower()
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    locator = step.get("locator") if isinstance(step.get("locator"), dict) else {}
    fallback = step.get("fallback") if isinstance(step.get("fallback"), dict) else {}
    coords = fallback.get("coordinates") if isinstance(fallback.get("coordinates"), dict) else {}

    raw: Dict[str, Any] = {"kind": action}
    raw.update(args)
    if locator.get("resource_id") or locator.get("resourceId"):
        raw["resourceId"] = locator.get("resource_id") or locator.get("resourceId")
    if locator.get("text"):
        raw["text"] = locator["text"]
    if locator.get("class_name") or locator.get("className"):
        raw["className"] = locator.get("class_name") or locator.get("className")
    if locator.get("content_description") or locator.get("contentDescription"):
        raw["contentDescription"] = locator.get("content_description") or locator.get("contentDescription")

    if action == "start_app":
        raw["package"] = args.get("package_name") or args.get("package") or ""
        raw["activity"] = args.get("activity") or ""
    if action == "tap":
        if coords:
            raw["x"], raw["y"] = coords.get("x"), coords.get("y")
            raw["coordinate_space"] = fallback.get("coordinate_space") or "device_px"
        elif args.get("x") is not None and args.get("y") is not None:
            raw["x"], raw["y"] = args["x"], args["y"]
            raw["coordinate_space"] = args.get("coordinate_space") or "device_px"
    if action == "swipe":
        for key in ("x1", "y1", "x2", "y2", "duration_ms"):
            if key in coords and key not in raw:
                raw[key] = coords[key]
        if fallback.get("coordinate_space"):
            raw["coordinate_space"] = fallback["coordinate_space"]
    if action == "tap_element":
        raw["index"] = args.get("index", 0)
    return raw


async def verify_plan_condition(device: WebSocketDevice, condition: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Evaluate supported low-cost verification conditions; None means unspecified."""
    if not condition:
        return None
    kind = str(condition.get("type") or "").strip().lower()
    if kind in {"delay", "wait"}:
        await asyncio.sleep(max(0, min(float(condition.get("duration_ms", 0)) / 1000, 60)))
        return True
    if kind in {"element_visible", "element_exists", "text_visible"}:
        expected = str(condition.get("target") or condition.get("text") or condition.get("value") or "").strip()
        if not expected:
            raise ValueError("verification target is required")
        _, elements = await device.get_ui_state()
        key = str(condition.get("field") or "text")
        aliases = {
            "resource_id": ("resourceId", "resource_id"),
            "content_description": ("contentDescription", "content_description"),
            "text": ("text",),
        }
        fields = aliases.get(key, (key,))
        return any(
            bool(el.get("visible", True))
            and any(expected in str(el.get(field) or "") for field in fields)
            for el in elements
        )
    if kind == "package_foreground":
        expected = str(condition.get("package") or condition.get("target") or "").strip()
        if not expected:
            raise ValueError("verification package is required")
        return await device.current_package() == expected
    raise ValueError(f"Unsupported verification type: {kind or '(empty)'}")


async def _execute_deterministic(device: WebSocketDevice, step: Dict[str, Any], emit) -> str:
    action = str(step.get("action") or "").strip().lower()
    if action == "wait":
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        duration = (step.get("wait_condition") or {}).get("duration_ms", args.get("duration_ms", 1000))
        seconds = max(0, min(float(duration) / 1000, 60))
        await asyncio.sleep(seconds)
        return f"waited {seconds:.2f}s"
    raw = build_replay_step(step)
    if action == "tap" and raw.get("x") is None and not any(raw.get(k) for k in ("resourceId", "text", "className")):
        raise ValueError("tap requires a locator or recorded coordinates")
    if action == "swipe" and not all(raw.get(k) is not None for k in ("x1", "y1", "x2", "y2")):
        raise ValueError("swipe requires x1/y1/x2/y2 coordinates")
    from core.test_runner import _replay_step
    return await _replay_step(device, raw, emit)


async def _execute_ai(
    device: WebSocketDevice,
    step: Dict[str, Any],
    provider: str,
    model: str,
    api_key: str,
    api_base: str,
    fallbacks: list,
    language: str,
    max_steps: int,
    emit,
    login_context=None,
    target_package: str = "",
):
    intent = step.get("intent") if isinstance(step.get("intent"), dict) else {}
    description = str(intent.get("description") or "").strip()
    expected = str(intent.get("expected_result") or "").strip()
    if not description or not expected or intent.get("status") != "confirmed":
        raise ValueError("AI 执行步骤必须先填写并确认操作意图和预期结果")
    locator = step.get("locator") if isinstance(step.get("locator"), dict) else {}
    fallback = step.get("fallback") if isinstance(step.get("fallback"), dict) else {}
    context = (
        f"执行计划步骤 {step.get('sequence', '?')}：{description}\n"
        f"本步骤预期结果：{expected}\n"
        "只完成当前这一个逻辑步骤，不要擅自执行后续测试步骤；达到预期结果后立即停止。\n"
        "以下定位信息仅是历史线索，不保证当前仍然有效；必须先观察当前页面并重新判断，不可盲目点击旧坐标。\n"
        f"历史定位线索：{json.dumps(locator, ensure_ascii=False)}\n"
        f"备用坐标线索：{json.dumps(fallback, ensure_ascii=False)}"
    )
    agent = TestCaseAgent(
        device=device,
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
        max_steps=max(1, min(int(max_steps), 20)),
        step_delay=0.4,
        log_callback=emit,
        fallbacks=fallbacks,
        allow_subagents=False,
        language=language,
        login_context=login_context,
        target_package=target_package,
    )
    return await agent.run(TestCaseData(path=context, expected=expected))


async def execute_execution_plan_run(
    run_id: str,
    plan_id: str,
    state,
    max_steps: int = 8,
) -> None:
    """Run one saved plan version as a normal TestRun/TestResult."""
    async def emit(message: str) -> None:
        message = state.redact(message)
        logger.info("[plan:%s] %s", run_id, message)
        await state.emit(message)

    recorder = RunRecorder(run_id)
    try:
        async with AsyncSessionLocal() as db:
            run = await db.get(TestRun, run_id)
            plan = await db.get(ExecutionPlan, plan_id)
            if not run or not plan or plan.suite_id != run.suite_id:
                raise RuntimeError("执行计划或测试运行记录不存在")
            case = await db.get(TestCase, plan.test_case_id)
            result = (await db.execute(select(TestResult).where(
                TestResult.run_id == run_id, TestResult.case_id == plan.test_case_id
            ))).scalar_one_or_none()
            if not case or not result:
                raise RuntimeError("执行计划关联的用例或结果记录不存在")
            steps = json.loads(plan.steps_json or "[]")
            device_id, provider, model, account_id = run.device_id, run.provider, run.model, run.account_id

        if not steps:
            raise RuntimeError("执行计划没有步骤")
        device = WebSocketDevice(device_id)
        if not device.is_connected():
            raise RuntimeError(f"设备 {device_id} 未连接")

        settings = await load_settings(account_id)
        apply_aws_env(settings)
        api_key = resolve_api_key(settings, provider)
        api_base = resolve_api_base(settings, provider)
        v_provider, v_model, v_key, v_base = resolve_verifier(settings)
        fallbacks = build_fallback_chain(settings, provider, model)
        language = resolve_language(settings)
        if not api_key and (provider or "").lower() != "ollama":
            raise RuntimeError("当前账号未配置模型 API Key")
        from core.test_accounts import context_for_run
        login_context = await context_for_run(run_id)
        if login_context:
            state.redact = login_context.redact

        from core.test_runner import _suite_app_package
        target_package = plan.app_package or await _suite_app_package(plan.suite_id, device.platform)
        verifier = LLMVerifier(v_provider, v_model, v_key, v_base, fallbacks=fallbacks, language=language)

        async with AsyncSessionLocal() as db:
            await db.execute(update(TestRun).where(TestRun.id == run_id).values(status="running"))
            await db.execute(update(TestResult).where(TestResult.id == result.id).values(
                status="running", started_at=datetime.utcnow()
            ))
            await db.commit()

        await emit(f"执行计划 v{plan.version}：{len(steps)} 步 · 设备={device_id}")
        if await recorder.start(device_id):
            await emit("屏幕录制已启动")

        action_history = []
        step_count = 0
        total_tokens = 0
        final_status = "pass"
        final_reason = ""
        final_screenshot = ""

        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"步骤 {index + 1} 格式无效")
            executor = str(step.get("executor") or "").lower()
            action = str(step.get("action") or "").lower()
            if executor not in {"deterministic", "ai", "hybrid", "system"}:
                raise ValueError(f"步骤 {index + 1} 执行方式无效：{executor}")
            if executor == "system" and action not in {"start_app", "global_action", "press_key"}:
                raise ValueError(f"步骤 {index + 1} 不支持系统动作：{action}")
            if executor in {"ai", "hybrid"}:
                intent = step.get("intent") if isinstance(step.get("intent"), dict) else {}
                if intent.get("status") != "confirmed" or not str(intent.get("description") or "").strip() or not str(intent.get("expected_result") or "").strip():
                    raise ValueError(f"步骤 {index + 1} 的 AI 操作意图尚未确认")

            await emit(f"[{index + 1}/{len(steps)}] {action} · {executor}")
            step_started = datetime.utcnow()
            step_result = ""
            step_status = "success"
            step_tokens = 0
            try:
                if executor == "ai":
                    ai_result = await _execute_ai(
                        device, step, provider, model, api_key, api_base,
                        fallbacks, language, max_steps, emit, login_context, target_package,
                    )
                    step_tokens = ai_result.total_tokens
                    step_result = ai_result.reason
                    if ai_result.status != "pass":
                        raise RuntimeError(ai_result.reason or "AI 未能完成当前步骤")
                else:
                    try:
                        step_result = await _execute_deterministic(device, step, emit)
                        condition_result = await verify_plan_condition(device, step.get("verification"))
                        if condition_result is False:
                            raise RuntimeError("步骤验证条件未满足")
                    except Exception as deterministic_error:
                        if executor != "hybrid":
                            raise
                        await emit(f"  确定性执行未通过，转交 AI 恢复：{deterministic_error}")
                        ai_result = await _execute_ai(
                            device, step, provider, model, api_key, api_base,
                            fallbacks, language, max_steps, emit, login_context, target_package,
                        )
                        step_tokens = ai_result.total_tokens
                        step_result = f"AI 恢复：{ai_result.reason}"
                        if ai_result.status != "pass":
                            raise RuntimeError(ai_result.reason or "AI 恢复失败")

                wait_condition = step.get("wait_condition") if isinstance(step.get("wait_condition"), dict) else {}
                if wait_condition.get("type") == "delay" and action != "wait":
                    delay = max(0, min(float(wait_condition.get("duration_ms", 0)) / 1000, 60))
                    if delay:
                        await asyncio.sleep(delay)
                timing = step.get("timing") if isinstance(step.get("timing"), dict) else {}
                gap = max(0, min(float(timing.get("inter_action_gap_ms", 0)) / 1000, 60))
                if gap:
                    await asyncio.sleep(gap)
                step_count += 1
                action_history.append({
                    "step": index + 1, "executor": executor, "action": action,
                    "result": step_result, "intent": (step.get("intent") or {}).get("description", ""),
                })
                total_tokens += step_tokens
                try:
                    shot = await device.screenshot()
                    final_screenshot = base64.b64encode(shot).decode("ascii")
                except Exception:
                    final_screenshot = ""
                async with AsyncSessionLocal() as db:
                    db.add(TestStepLog(
                        result_id=result.id, step=index + 1,
                        thought=str((step.get("intent") or {}).get("description") or ""),
                        action=f"{executor}:{action}", action_result=step_result,
                        screenshot_b64=final_screenshot, total_tokens=step_tokens,
                        account_id=account_id,
                    ))
                    await db.commit()
                await emit(f"  ✓ {step_result}")
            except Exception as exc:
                step_status = "error"
                final_status = "error"
                final_reason = f"步骤 {index + 1} 执行失败：{exc}"
                action_history.append({"step": index + 1, "executor": executor, "action": action, "error": str(exc)})
                await emit(f"  ✗ {final_reason}")
                break

        if final_status == "pass" and case.expected:
            await emit("所有步骤已执行，正在验证用例预期结果…")
            confirmed, reason, shot_b64, _gap = await verifier.verify(
                device, case.expected, log=emit, action_history=[
                    f"Step {item.get('step')}: {item.get('executor')} {item.get('action')} → {item.get('result', item.get('error', ''))}"
                    for item in action_history
                ],
            )
            final_status = "pass" if confirmed else "fail"
            final_reason = reason
            if shot_b64:
                final_screenshot = shot_b64
        elif final_status == "pass":
            final_reason = "所有计划步骤均已完成；用例未配置最终预期结果，未执行最终断言。"

        async with AsyncSessionLocal() as db:
            await db.execute(update(TestResult).where(TestResult.id == result.id).values(
                status=final_status, reason=final_reason, steps=step_count,
                screenshot_b64=final_screenshot, action_history_json=json.dumps(action_history, ensure_ascii=False),
                total_tokens=total_tokens, finished_at=datetime.utcnow(),
            ))
            await db.execute(update(TestRun).where(TestRun.id == run_id).values(
                status="done", finished_at=datetime.utcnow()
            ))
            await db.commit()
        await emit(f"执行计划结束：{final_status} · {final_reason}")
    except asyncio.CancelledError:
        async with AsyncSessionLocal() as db:
            await db.execute(update(TestRun).where(TestRun.id == run_id).values(
                status="cancelled", finished_at=datetime.utcnow()
            ))
            await db.execute(update(TestResult).where(TestResult.run_id == run_id, TestResult.status.in_(["pending", "running"])).values(
                status="error", reason="任务已取消", finished_at=datetime.utcnow()
            ))
            await db.commit()
        await emit("执行计划已取消")
        raise
    except Exception as exc:
        logger.exception("Execution plan run failed: %s", run_id)
        async with AsyncSessionLocal() as db:
            await db.execute(update(TestRun).where(TestRun.id == run_id).values(
                status="error", finished_at=datetime.utcnow()
            ))
            await db.execute(update(TestResult).where(TestResult.run_id == run_id, TestResult.status.in_(["pending", "running"])).values(
                status="error", reason=str(exc), finished_at=datetime.utcnow()
            ))
            await db.commit()
        await emit(f"执行计划运行错误：{exc}")
    finally:
        await recorder.stop()
