"""Execution-plan schema helpers and legacy replay import.

This module intentionally does not execute device actions. It only converts
existing deterministic replay data into a reviewable draft plan.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


SUPPORTED_ACTIONS = {
    "tap", "tap_element", "scroll", "swipe", "input_text",
    "global_action", "press_key", "start_app", "audio_play",
}

# These actions use the device/system bridge, not on-screen element locators.
SYSTEM_ACTIONS = {"start_app", "global_action", "press_key"}


def legacy_replay_to_plan(replay_steps: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Convert legacy TestCase.replay_steps JSON into draft PlanStep objects.

    Missing evidence is reported as a warning; no locator or page state is
    fabricated. The original replay step is retained for traceability.
    """
    if not replay_steps:
        raise ValueError("该用例没有可导入的 replay_steps")
    try:
        raw_steps = json.loads(replay_steps)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("replay_steps 不是有效 JSON") from exc
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("replay_steps 必须是非空数组")

    plan_steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            warnings.append(f"步骤 {index + 1}: 原始步骤不是对象，无法自动执行")
            plan_steps.append({
                "step_id": f"legacy_{index + 1}", "sequence": index + 1,
                "action": "unsupported", "args": {}, "executor": "ai",
                "locator": {}, "fallback": None, "precondition": None,
                "wait_condition": None, "verification": None,
                "on_failure": "fail", "legacy_source": raw,
                "intent": {
                    "description": "", "expected_result": "",
                    "source": "legacy_import", "status": "needs_confirmation",
                },
                "import_status": "unsupported",
            })
            continue

        action = str(raw.get("kind") or raw.get("action") or "").strip().lower()
        args = {k: v for k, v in raw.items() if k not in {"kind", "delay_ms"}}
        # Normalize both legacy "package" and the canonical "package_name".
        # Keep "package" for compatibility with the existing Replay adapter.
        if action == "start_app":
            package_name = str(raw.get("package_name") or raw.get("package") or "").strip()
            if package_name:
                args["package_name"] = package_name
                args["package"] = package_name

        locator: Dict[str, Any] = {}
        if isinstance(raw.get("locator"), dict):
            locator.update({key: value for key, value in raw["locator"].items() if value})
        if raw.get("resourceId"):
            locator["resource_id"] = raw["resourceId"]
        if raw.get("text"):
            locator["text"] = raw["text"]
        if raw.get("contentDescription") or raw.get("content_description"):
            locator["content_description"] = raw.get("contentDescription") or raw.get("content_description")
        if raw.get("className") or raw.get("class_name"):
            locator["class_name"] = raw.get("className") or raw.get("class_name")

        coordinate_keys = ("x", "y", "x1", "y1", "x2", "y2")
        coordinates = {key: raw[key] for key in coordinate_keys if raw.get(key) is not None}
        coordinate_space = raw.get("coordinate_space") or raw.get("coordinateSpace")
        fallback = None
        if coordinates:
            fallback = {"type": "coordinate", "coordinates": coordinates}
            if coordinate_space:
                fallback["coordinate_space"] = coordinate_space
            screen_size = raw.get("screen_size")
            if isinstance(screen_size, dict) and screen_size.get("width") and screen_size.get("height"):
                fallback["screen_size"] = {
                    "width": screen_size["width"], "height": screen_size["height"]
                }

        step_warnings: List[str] = []
        if action not in SUPPORTED_ACTIONS:
            executor = "ai"
            import_status = "unsupported"
            step_warnings.append(f"步骤 {index + 1}: 不支持的动作类型 {action or '(空)'}")
        elif action in SYSTEM_ACTIONS:
            executor = "system"
            import_status = "ready"
            if action == "start_app" and not args.get("package_name"):
                import_status = "needs_package"
                step_warnings.append(f"步骤 {index + 1}: 启动应用缺少 Package / Bundle 标识，不能自动执行")
        elif action == "tap" and locator:
            executor = "deterministic"
            import_status = "ready"
        elif action in {"tap", "tap_element"} and not locator:
            executor = "hybrid"
            import_status = "needs_locator"
            if fallback:
                step_warnings.append(f"步骤 {index + 1}: 只有坐标定位，建议补充控件语义")
            else:
                step_warnings.append(f"步骤 {index + 1}: 缺少稳定定位信息和坐标")
        else:
            executor = "deterministic"
            import_status = "ready"

        delay_ms = raw.get("delay_ms", 0)
        try:
            delay_ms = max(0, int(delay_ms or 0))
        except (TypeError, ValueError, OverflowError):
            delay_ms = 0
            step_warnings.append(f"步骤 {index + 1}: delay_ms 无效，已按 0 处理")

        step = {
            "step_id": f"legacy_{index + 1}",
            "sequence": index + 1,
            "action": action or "unsupported",
            "args": args,
            "executor": executor,
            "execution_channel": "system_api" if executor == "system" else ("ui_automation" if executor in {"deterministic", "hybrid"} else "ai_agent"),
            "locator": locator,
            "fallback": fallback,
            "precondition": None,
            "wait_condition": None,
            "timing": {"inter_action_gap_ms": delay_ms} if delay_ms else {},
            "source_event_id": raw.get("source_event_id"),
            "verification": None,
            "on_failure": "ai_recovery" if executor == "hybrid" else "fail",
            # Legacy replay captures actions and coordinates, not the user's
            # business intent. Never pretend that the intent was inferred.
            "intent": {
                "description": "", "expected_result": "",
                "source": "legacy_import", "status": "needs_confirmation",
            },
            "legacy_source": raw,
            "import_status": import_status,
            "warnings": step_warnings,
        }
        if action == "tap_element":
            step_warnings.append(f"步骤 {index + 1}: 原始 element index 不具备跨页面稳定性")
            step["import_status"] = "needs_locator"
        plan_steps.append(step)
        warnings.extend(step_warnings)

    return plan_steps, warnings
