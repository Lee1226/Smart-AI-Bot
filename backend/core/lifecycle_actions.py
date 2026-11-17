"""Shared execution lifecycle action dispatcher for recorded cases and plans."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


async def run_lifecycle_actions(device, actions: list, phase: str, emit: Callable[[str], Awaitable[None]]) -> None:
    """Run enabled lifecycle actions in order using the shared device adapter."""
    enabled = [item for item in actions if isinstance(item, dict) and item.get("enabled", True)]
    if not enabled:
        return
    await emit(f"{phase}：开始执行 {len(enabled)} 个操作")
    for index, item in enumerate(enabled):
        action = str(item.get("action") or "").strip().lower()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        try:
            if action == "stop_app":
                package = str(args.get("package_name") or args.get("package") or "").strip()
                if not package:
                    raise ValueError("强制停止应用缺少包名")
                result = await device.stop_app(package)
            elif action == "start_app":
                package = str(args.get("package_name") or args.get("package") or "").strip()
                if not package:
                    raise ValueError("启动应用缺少包名")
                result = await device.start_app(package, str(args.get("activity") or ""))
                if str(result).startswith("ERROR:"):
                    raise RuntimeError(result)
            elif action == "global_action":
                result = await device.global_action(str(args.get("action") or ""))
            elif action == "press_key":
                result = await device.press_key(str(args.get("key") or ""))
            elif action == "wait":
                duration_ms = max(0, min(float(args.get("duration_ms", 1000)), 60000))
                await asyncio.sleep(duration_ms / 1000)
                result = f"等待 {duration_ms:g} ms"
            else:
                raise ValueError(f"不支持的生命周期操作：{action or '(empty)'}")
            await emit(f"  ✓ {index + 1}/{len(enabled)} {action}：{result}")
        except Exception as exc:
            await emit(f"  ✗ {index + 1}/{len(enabled)} {action}：{exc}")
            if str(item.get("on_failure") or "stop").lower() != "continue":
                raise RuntimeError(f"{phase}第 {index + 1} 个操作失败：{exc}") from exc
