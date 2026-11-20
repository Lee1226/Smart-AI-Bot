"""Shared low-level manual device control for debug preview and recorder gestures.

The HTTP routers keep their own auth, response contracts and scheduler-lock
ownership. This module only performs coordinate mapping and dispatches the
physical device input.
"""
import time
from typing import Optional

from agent import local_adb
from agent.ws_device import WebSocketDevice

_SCREEN_DIMENSIONS_TTL = 60.0
_screen_dimensions_cache: dict[str, tuple[int, int, float]] = {}


def cache_screen_dimensions(device_id: str, width: int, height: int) -> None:
    if width > 0 and height > 0:
        _screen_dimensions_cache[device_id] = (width, height, time.monotonic())


async def refresh_local_screen_dimensions(device_id: str) -> bool:
    if not local_adb.is_bound(device_id):
        return False
    try:
        width, height = await local_adb.get_screen_size(device_id)
    except Exception:
        return False
    cache_screen_dimensions(device_id, width, height)
    return True


async def execute_manual_device_control(
    device_id: str,
    transport: WebSocketDevice,
    action: str,
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
    duration_ms: int = 450,
    coordinate_space: Optional[str] = None,
) -> str:
    """Dispatch one input. Caller must hold core.device_scheduler.locks[device_id].

    source_width/source_height retain the debug-console video-frame mapping.
    coordinate_space='device_px' means recorder coordinates are already native
    device pixels and must not be scaled a second time.
    """
    if action in ("tap", "swipe"):
        if coordinate_space == "model_image_px":
            if action == "tap":
                if x is None or y is None:
                    raise ValueError("触控坐标缺失")
                return await transport.tap(x, y)
            else:
                if any(value is None for value in (x, y, x2, y2)):
                    raise ValueError("触控坐标缺失")
                return await transport.swipe(x, y, x2, y2, duration_ms)
            return

        required = (x, y) if action == "tap" else (x, y, x2, y2)
        if any(value is None for value in required):
            raise ValueError("触控坐标缺失")
        points = [(x, y)] if action == "tap" else [(x, y), (x2, y2)]

        if coordinate_space != "device_px":
            cached = _screen_dimensions_cache.get(device_id)
            source_ratio = (source_width / source_height) if source_width and source_height else 0

            if local_adb.is_bound(device_id):
                # Keep the debug console's lightweight native-ADB size lookup.
                if not cached:
                    await refresh_local_screen_dimensions(device_id)
                    cached = _screen_dimensions_cache.get(device_id)
                native_w, native_h = (
                    (cached[0], cached[1]) if cached
                    else (transport.screen_width, transport.screen_height)
                )
                if native_w <= 0 or native_h <= 0:
                    native_w, native_h = source_width or 0, source_height or 0

                # The decoded frame may reflect a rotated orientation.
                cached_ratio = native_w / native_h if native_h else 0
                if source_ratio and cached_ratio:
                    same = abs(source_ratio - cached_ratio) <= 0.08
                    inverse = abs(source_ratio - 1 / cached_ratio) <= 0.08
                    if inverse and not same:
                        native_w, native_h = native_h, native_w
                        cache_screen_dimensions(device_id, native_w, native_h)
            else:
                # Preserve the existing Portal/remote-bridge cache policy.
                native_w, native_h = transport.screen_width, transport.screen_height
                if cached and (time.monotonic() - cached[2]) < _SCREEN_DIMENSIONS_TTL:
                    native_w, native_h = cached[0], cached[1]
                if native_w <= 0 or native_h <= 0 or not cached or (time.monotonic() - cached[2]) >= _SCREEN_DIMENSIONS_TTL:
                    try:
                        await transport.get_ui_state()
                        if transport.screen_width > 0 and transport.screen_height > 0:
                            native_w, native_h = transport.screen_width, transport.screen_height
                            cache_screen_dimensions(device_id, native_w, native_h)
                    except Exception:
                        if cached and cached[0] > 0 and cached[1] > 0:
                            native_w, native_h = cached[0], cached[1]

            if source_width and source_height and native_w > 0 and native_h > 0:
                points = [
                    (round(px * native_w / source_width), round(py * native_h / source_height))
                    for px, py in points
                ]
                points = [
                    (max(0, min(native_w - 1, px)), max(0, min(native_h - 1, py)))
                    for px, py in points
                ]

        if action == "tap":
            return await transport.tap_absolute(*points[0])
        else:
            bounded_duration = max(80, min(duration_ms, 2000)) if coordinate_space != "device_px" else duration_ms
            return await transport.swipe_absolute(*points[0], *points[1], bounded_duration)
        return

    key = {"back": "back", "home": "home", "recent": "recent", "power": "power"}.get(action)
    if key is None:
        raise ValueError(f"Unsupported manual device action: {action}")
    return await transport.press_key(key)
