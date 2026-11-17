"""
Recorder API — interactive remote-control + step recording.

Flow:
  1. GET  /api/recorder/snapshot   → current screenshot + a11y elements
  2. POST /api/recorder/action     → execute one action, return new snapshot
  3. POST /api/recorder/save       → convert step list into TestSuite + TestCase

The saved TestCase uses `path` as the user-defined case title. Executable
steps are stored separately in `replay_steps` and event evidence.

    [录制步骤]
    1. 启动应用: com.android.settings
    2. 点击 "Wi-Fi"
    3. 点击 "Wi-Fi 开关"
    请重新执行以上步骤并验证期望结果。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent.ws_device import WebSocketDevice
from auth.access import require_device
from db.database import AsyncSessionLocal
from db.models import TestCase, TestSuite
from agent import local_adb
from core.recorder_engine import recorder_engine, sanitize_events_for_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recorder", tags=["recorder"])


async def _authorize_device(request: Request, device_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await require_device(request, db, device_id)

# ── Real-recording session state ──────────────────────────────────────────────
# While a device is "recording", the Portal streams real user interactions
# (tap/scroll/input + coordinates) to the server; we buffer them per device so
# the web UI can show them live and finally save them as a test case.
def handle_record_event(device_id: str, event: dict) -> None:
    """Portal adapter entry point; all sources feed the unified recorder engine."""
    recorder_engine.append(device_id, event, source="portal_touch")


def _to_replay_steps(
    steps: List["RecordedStep"],
    events: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Convert recorded steps into a deterministic replay plan (JSON string).

    Each entry carries the action + coordinates/args and preserves legacy
    delay_ms for backward-compatible replay. New plan imports treat that value
    as an observed inter-action gap, not a measured app-settle wait.
    """
    replay: List[dict] = []
    event_list = events or []
    events_by_id = {
        str(event.get("event_id")): event
        for event in event_list
        if isinstance(event, dict) and event.get("event_id")
    }
    initial_screen = next((
        event.get("session_start_snapshot", {}).get("screen")
        for event in event_list
        if isinstance(event, dict)
        and isinstance(event.get("session_start_snapshot"), dict)
        and isinstance(event["session_start_snapshot"].get("screen"), dict)
    ), None)
    for index, s in enumerate(steps):
        args = s.args or {}
        event_id = args.get("event_id") or args.get("source_event_id")
        event = events_by_id.get(str(event_id)) if event_id else None
        if event is None:
            event = event_list[index] if index < len(event_list) and isinstance(event_list[index], dict) else {}
        # Only event-time target evidence may become an executable locator.
        # observed_target is populated by a later polling snapshot and can point
        # at a different screen after a modal has disappeared.
        target = event.get("target") if isinstance(event.get("target"), dict) else {}
        # Element indexes are tied to one UI-tree snapshot. When the
        # recording contains a semantic target or center coordinate, compile
        # tap_element into a stable tap action; retain index only as legacy
        # evidence when no stronger target exists.
        element_target = target.get("tap_target") if isinstance(target.get("tap_target"), dict) else target
        has_element_identity = isinstance(element_target, dict) and any(
            element_target.get(key) for key in (
                "text", "resource_id", "resourceId", "content_description",
                "contentDescription", "class_name", "className",
            )
        )
        event_coordinates = event.get("coordinates") if isinstance(event.get("coordinates"), dict) else {}
        has_element_point = (
            isinstance(element_target, dict)
            and element_target.get("cx") is not None
            and element_target.get("cy") is not None
        )
        replay_action = (
            "tap" if s.action == "tap_element" and (has_element_identity or has_element_point)
            else s.action
        )
        step: dict = {"kind": replay_action, "delay_ms": int(args.get("delay_ms", 0))}
        if target and any(target.get(key) for key in (
            "text", "resource_id", "resourceId", "content_description",
            "contentDescription", "class_name", "className",
        )):
            step["target"] = target
        if event.get("event_id"):
            step["source_event_id"] = event["event_id"]
        # A touched child may delegate interaction to its clickable ancestor.
        # Keep the raw child evidence, but prefer the recorded tap target for replay.
        tap_target = target.get("tap_target") if isinstance(target.get("tap_target"), dict) else {}
        locator_source = tap_target or target
        locator = {
            "resource_id": locator_source.get("resource_id") or locator_source.get("resourceId"),
            "text": locator_source.get("text"),
            "content_description": locator_source.get("content_description") or locator_source.get("contentDescription"),
            "class_name": locator_source.get("class_name") or locator_source.get("className"),
        }
        locator = {key: value for key, value in locator.items() if value}
        if locator:
            step["locator"] = locator
        coordinates = event.get("coordinates") if isinstance(event.get("coordinates"), dict) else {}
        if coordinates.get("space"):
            step["coordinate_space"] = coordinates["space"]
        screen = (
            event.get("screen")
            or (event.get("before") or {}).get("screen")
            or (event.get("ui_observation") or {}).get("screen")
            or initial_screen
        )
        if isinstance(screen, dict) and screen.get("width") and screen.get("height"):
            step["screen_size"] = {"width": screen["width"], "height": screen["height"]}
        if args.get("delay_ms"):
            # This is the elapsed time between recorded user actions, not a
            # measured application-settle duration.
            step["inter_action_gap_ms"] = max(0, int(args["delay_ms"]))
        if s.action == "tap":
            if args.get("x") is not None:
                step["x"] = args.get("x")
            if args.get("y") is not None:
                step["y"] = args.get("y")
            # Also carry element identity (id/text/class) so replay can prefer
            # element matching over stale coordinates.
            if args.get("resourceId"):
                step["resourceId"] = args.get("resourceId")
            if args.get("text"):
                step["text"] = args.get("text")
            if args.get("className"):
                step["className"] = args.get("className")
            # Coordinates from Portal are physical device pixels; browser actions
            # retain the legacy model-image coordinate space.
            coordinate_space = args.get("coordinate_space") or args.get("coordinateSpace")
            if coordinate_space:
                step["coordinate_space"] = coordinate_space
        elif s.action == "tap_element":
            step["index"] = args.get("index")
            # Copy stable semantic attributes to the legacy replay adapter's
            # top-level fields, then preserve center coordinates as fallback.
            if isinstance(element_target, dict):
                for source_key, target_key in (
                    ("resource_id", "resourceId"), ("resourceId", "resourceId"),
                    ("text", "text"),
                    ("content_description", "contentDescription"),
                    ("contentDescription", "contentDescription"),
                    ("class_name", "className"), ("className", "className"),
                ):
                    if element_target.get(source_key) and not step.get(target_key):
                        step[target_key] = element_target[source_key]
            tap_x = args.get("x", event_coordinates.get("x"))
            tap_y = args.get("y", event_coordinates.get("y"))
            if tap_x is None and has_element_point:
                tap_x = element_target.get("cx")
            if tap_y is None and has_element_point:
                tap_y = element_target.get("cy")
            if tap_x is not None and tap_y is not None and replay_action == "tap":
                step["x"], step["y"] = tap_x, tap_y
                step["coordinate_space"] = event_coordinates.get("space") or "device_px"
        elif s.action == "scroll":
            step["direction"] = args.get("direction")
        elif s.action == "swipe":
            for k in ("x1", "y1", "x2", "y2", "duration_ms"):
                if args.get(k) is not None:
                    step[k] = args[k]
            coordinate_space = args.get("coordinate_space") or args.get("coordinateSpace")
            if coordinate_space:
                step["coordinate_space"] = coordinate_space
        elif s.action == "input_text":
            step["text"] = args.get("text")
            step["clear"] = bool(args.get("clear", False))
        elif s.action == "global_action":
            step["action"] = args.get("action")
        elif s.action == "press_key":
            step["key"] = args.get("key")
        elif s.action == "start_app":
            step["package"] = args.get("package")
        replay.append(step)
    return json.dumps(replay, ensure_ascii=False)


# ── Response / Request models ─────────────────────────────────────────────────

class Element(BaseModel):
    index: int
    text: str
    className: str
    resourceId: str
    cx: int
    cy: int
    contentDescription: str = ""
    bounds: Optional[Dict[str, int]] = None
    clickable: bool = False
    tap_candidate: bool = False
    tap_evidence: str = "none"
    focusable: bool = False
    checkable: bool = False
    longClickable: bool = False
    editable: bool = False
    capabilities: List[str] = []
    tap_target: Optional[Dict[str, Any]] = None
    enabled: bool = True
    visible: bool = True
    scrollable: bool = False
    selected: bool = False
    checked: bool = False
    parentPath: List[str] = []


class SnapshotResponse(BaseModel):
    screenshot_b64: str
    ui_text: str
    elements: List[Element]
    screen_width: int = 0
    screen_height: int = 0


class ActionRequest(BaseModel):
    device_id: str
    action: str          # tap_element / input_text / scroll / global_action / start_app / press_key
    args: Dict[str, Any] = {}


class ActionResponse(BaseModel):
    result: str
    screenshot_b64: str
    ui_text: str
    elements: List[Element]
    description: str     # human-readable step description
    event: Optional[Dict[str, Any]] = None


class RecordedStep(BaseModel):
    action: str
    args: Dict[str, Any]
    description: str


class SaveRequest(BaseModel):
    device_id: str
    suite_name: str
    case_name: Optional[str] = None
    expected: str
    steps: List[RecordedStep]
    events: Optional[List[Dict[str, Any]]] = None
    pre_actions: List[Dict[str, Any]] = Field(default_factory=list)
    post_actions: List[Dict[str, Any]] = Field(default_factory=list)


class SaveResponse(BaseModel):
    suite_id: str
    case_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_device(device_id: str) -> WebSocketDevice:
    device = WebSocketDevice(device_id)
    if not device.is_connected():
        raise HTTPException(status_code=400, detail=f"Device '{device_id}' is not connected")
    return device


def _step_description(action: str, args: dict, elements: list) -> str:
    """Generate a human-readable description of one step."""
    q = "\u201c"  # "
    Q = "\u201d"  # "
    if action == "tap_element":
        idx = args.get("index")
        el = next((e for e in elements if e.get("index") == idx), None)
        label = el["text"] if el and el.get("text") else f"element[{idx}]"
        return f"点击 {q}{label}{Q}"
    if action == "tap":
        return f"点击坐标 ({args.get('x')}, {args.get('y')})"
    if action == "input_text":
        text = args.get("text", "")
        clear = "（清空后）" if args.get("clear") else ""
        return f"输入文本{clear}: {q}{text}{Q}"
    if action == "scroll":
        direction_map = {"down": "向下", "up": "向上", "left": "向左", "right": "向右"}
        d = direction_map.get(args.get("direction", ""), args.get("direction", ""))
        dist = args.get("distance", "medium")
        return f"滚动 {d}（{dist}）"
    if action == "global_action":
        action_map = {"back": "返回", "home": "回到主页", "recent": "打开最近应用", "notifications": "展开通知"}
        return action_map.get(args.get("action", ""), f"系统操作: {args.get('action')}")
    if action == "press_key":
        return f"按键: {args.get('key')}"
    if action == "start_app":
        return f"启动应用: {args.get('package')}"
    if action == "swipe":
        return f"滑动: ({args.get('x1')},{args.get('y1')}) -> ({args.get('x2')},{args.get('y2')})"
    return f"{action}({json.dumps(args, ensure_ascii=False)})"


async def _snapshot(device: WebSocketDevice) -> SnapshotResponse:
    img_bytes = await device.screenshot()
    ui_text, elements_raw = await device.get_ui_state()
    import base64
    screenshot_b64 = base64.b64encode(img_bytes).decode()
    elements = [
        Element(
            index=e["index"],
            text=e.get("text", ""),
            className=e.get("className", ""),
            resourceId=e.get("resourceId", ""),
            cx=e["cx"],
            cy=e["cy"],
            contentDescription=e.get("contentDescription", ""),
            bounds=e.get("bounds"),
            clickable=e.get("clickable", False),
            tap_candidate=e.get("tap_candidate", e.get("clickable", False)),
            tap_evidence=e.get("tap_evidence", "none"),
            focusable=e.get("focusable", False),
            checkable=e.get("checkable", False),
            longClickable=e.get("longClickable", False),
            editable=e.get("editable", False),
            capabilities=e.get("capabilities", []),
            tap_target=e.get("tap_target"),
            enabled=e.get("enabled", True),
            visible=e.get("visible", True),
            scrollable=e.get("scrollable", False),
            selected=e.get("selected", False),
            checked=e.get("checked", False),
            parentPath=e.get("parentPath", []),
        )
        for e in elements_raw
    ]
    return SnapshotResponse(
        screenshot_b64=screenshot_b64,
        ui_text=ui_text,
        elements=elements,
        screen_width=device.screen_width,
        screen_height=device.screen_height,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/session/start")
async def start_web_session(device_id: str, request: Request):
    """Start a unified session for actions issued by the browser recorder."""
    await _authorize_device(request, device_id)
    _get_device(device_id)
    # Make browser recording startup idempotent. A page refresh or a lost
    # stop request must not strand the device in a state where it can never
    # start another browser recording.
    current = recorder_engine.session_info(device_id)
    if current and current["recording"]:
        if current["source"] == "web_action":
            return {
                "ok": True,
                "device_id": device_id,
                "session_id": current["session_id"],
                "source": current["source"],
                "reused": True,
            }
        raise HTTPException(
            status_code=409,
            detail=f"设备当前已有录制会话（{current['source']}），请先停止该录制会话",
        )
    try:
        session = recorder_engine.start(device_id, source="web_action")
    except RuntimeError as e:
        # Protect against a concurrent start between session_info and start().
        current = recorder_engine.session_info(device_id)
        if current and current["recording"] and current["source"] == "web_action":
            return {
                "ok": True,
                "device_id": device_id,
                "session_id": current["session_id"],
                "source": current["source"],
                "reused": True,
            }
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "device_id": device_id, "session_id": session.session_id,
            "source": session.source, "reused": False}


@router.post("/session/stop")
async def stop_web_session(device_id: str, request: Request):
    """Stop a browser-action recording session and return its normalized events."""
    await _authorize_device(request, device_id)
    info = recorder_engine.session_info(device_id)
    if info and info["recording"] and info["source"] != "web_action":
        raise HTTPException(status_code=409, detail="当前设备正在使用其他录制来源")
    events = recorder_engine.stop(device_id)
    return {"ok": True, "events": events,
            "session": recorder_engine.session_info(device_id)}


@router.post("/record/start")
async def start_record(device_id: str, request: Request):
    """Begin real recording: the Portal starts streaming the user's taps/scrolls."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    if device.platform in {"ios", "harmony"}:
        raise HTTPException(status_code=400, detail="当前 USB 桥接不支持真机触摸监控，请使用网页录制")
    if local_adb.is_bound(device_id):
        return {"ok": True, "device_id": device_id, "mode": "manual",
                "message": "本机 ADB 模式不支持手机端触摸事件监听，请通过网页录制器的操作按钮录制。"}
    try:
        session = recorder_engine.start(device_id, source="portal_touch")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # Ask the Portal to emit physical interaction events. A failed RPC must not
    # leave a session that falsely reports successful capture.
    try:
        await device._rpc("start_record", {})
    except Exception as e:
        recorder_engine.stop(device_id)
        logger.warning("start_record RPC failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Portal 无法启动真机事件采集: {e}")
    return {"ok": True, "device_id": device_id, "session_id": session.session_id,
            "source": session.source}


@router.post("/record/stop")
async def stop_record(device_id: str, request: Request):
    """End real recording and return the captured events."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    if local_adb.is_bound(device_id):
        return {"ok": True, "events": [], "mode": "manual"}
    info = recorder_engine.session_info(device_id)
    if info and info["recording"] and info["source"] != "portal_touch":
        raise HTTPException(status_code=409, detail="当前设备正在使用其他录制来源")
    try:
        await device._rpc("stop_record", {})
    except Exception as e:
        logger.warning("stop_record RPC failed: %s", e)
    events = recorder_engine.stop(device_id)
    return {"ok": True, "events": events,
            "session": recorder_engine.session_info(device_id)}


@router.get("/record/events")
async def record_events(device_id: str, request: Request, since: int = 0):
    """Poll captured record events (incremental via `since`)."""
    await _authorize_device(request, device_id)
    events = recorder_engine.events(device_id, since)
    return {"recording": recorder_engine.is_recording(device_id), "events": events}


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(device_id: str, request: Request):
    """Return the current screenshot and a11y element list for a device."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    return await _snapshot(device)


@router.get("/elements")
async def recorder_elements(device_id: str, request: Request):
    """Refresh accessibility metadata without capturing a screenshot or dimensions."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    ui_text, elements_raw = await device.get_ui_state()
    elements = [
        Element(
            index=e["index"],
            text=e.get("text", ""),
            className=e.get("className", ""),
            resourceId=e.get("resourceId", ""),
            cx=e["cx"],
            cy=e["cy"],
            contentDescription=e.get("contentDescription", ""),
            bounds=e.get("bounds"),
            clickable=e.get("clickable", False),
            enabled=e.get("enabled", True),
            visible=e.get("visible", True),
            scrollable=e.get("scrollable", False),
            selected=e.get("selected", False),
            checked=e.get("checked", False),
            parentPath=e.get("parentPath", []),
        )
        for e in elements_raw
    ]
    return {"ui_text": ui_text, "elements": elements}


@router.post("/gesture")
async def execute_recorder_gesture(req: ActionRequest, request: Request):
    """Low-latency mouse gesture path: capture pre-action semantics, execute,
    append the event, and leave screenshot refresh to the live preview."""
    await _authorize_device(request, req.device_id)
    if req.action not in {"tap", "swipe"}:
        raise HTTPException(status_code=400, detail="仅支持 tap / swipe 手势")
    from core.device_scheduler import locks
    lock = locks.setdefault(req.device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行任务，请先取消任务或等待完成")
    async with lock:
        device = _get_device(req.device_id)
        args = req.args
        # Capture the exact pre-action frame inside the same per-device lock,
        # then dispatch the gesture. This keeps the single event frame aligned
        # with the coordinates and prevents a later screen from replacing it.
        import base64
        before_screenshot_b64 = None
        before_screenshot_captured_at_ms = None
        try:
            before_screenshot_b64 = base64.b64encode(await device.screenshot()).decode()
            before_screenshot_captured_at_ms = int(time.time() * 1000)
        except Exception:
            # Screenshot failure must not prevent the physical gesture.
            pass
        try:
            coordinate_space = args.get("coordinate_space") or args.get("coordinateSpace")
            # Capture accessibility semantics before the gesture so the event can
            # retain the control that occupied the touched coordinates.
            try:
                pre_ui_text, pre_elements = await device.get_ui_state()
            except Exception:
                pre_ui_text, pre_elements = "", []
            if req.action == "tap":
                if coordinate_space == "device_px":
                    result = await device.tap_absolute(args["x"], args["y"])
                else:
                    result = await device.tap(args["x"], args["y"])
            else:
                coords = (args["x1"], args["y1"], args["x2"], args["y2"])
                if coordinate_space == "device_px":
                    result = await device.swipe_absolute(*coords, duration_ms=args.get("duration_ms", 450))
                else:
                    result = await device.swipe(*coords, duration_ms=args.get("duration_ms", 450))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        description = _step_description(req.action, args, [])
        target_element = None
        if req.action == "tap":
            tap_x, tap_y = args.get("x"), args.get("y")
            if isinstance(tap_x, (int, float)) and isinstance(tap_y, (int, float)):
                target_element = next((element for element in reversed(pre_elements)
                    if isinstance(element.get("bounds"), dict)
                    and element["bounds"].get("left", 0) <= tap_x <= element["bounds"].get("right", 0)
                    and element["bounds"].get("top", 0) <= tap_y <= element["bounds"].get("bottom", 0)), None)
        event_payload = {"kind": req.action, **args, "description": description,
                         "coordinates": {
                             "space": coordinate_space or "model_image_px",
                             **({k: args[k] for k in ("x", "y") if k in args}
                                if req.action == "tap" else
                                {k: args[k] for k in ("x1", "y1", "x2", "y2") if k in args}),
                         },
                         "before": {
                             "ui_text": pre_ui_text,
                             "elements": pre_elements,
                             "screen": {"width": device.screen_width, "height": device.screen_height},
                             "captured_at_ms": int(time.time() * 1000),
                         }}
        if target_element:
            event_payload.update({
                "text": target_element.get("text", ""),
                "contentDescription": target_element.get("contentDescription", ""),
                "resourceId": target_element.get("resourceId", ""),
                "className": target_element.get("className", ""),
                "target": target_element,
            })
        try:
            after_ui_text, after_elements = await device.get_ui_state()
            after_captured_at_ms = int(time.time() * 1000)
            event_payload["after"] = {
                "ui_text": after_ui_text,
                "elements": after_elements,
                "screen": {"width": device.screen_width, "height": device.screen_height},
                "captured_at_ms": after_captured_at_ms,
            }
        except Exception:
            after_captured_at_ms = int(time.time() * 1000)
        # Keep an exact post-action frame as well as the pre-action frame.
        # The save endpoint moves both images to case-scoped assets.
        try:
            event_payload["screenshot_b64_after"] = base64.b64encode(await device.screenshot()).decode()
            event_payload["screenshot_after_captured_at_ms"] = int(time.time() * 1000)
        except Exception:
            pass
        event = recorder_engine.append(req.device_id, event_payload)
        return {"ok": True, "result": result, "description": description,
                "event": event, "target": target_element,
                "before_screenshot_b64": before_screenshot_b64,
                "screenshot_captured_at_ms": before_screenshot_captured_at_ms}


@router.post("/action", response_model=ActionResponse)
async def execute_action(req: ActionRequest, request: Request):
    """Execute one action while holding the same device lock as scheduled runs."""
    await _authorize_device(request, req.device_id)
    from core.device_scheduler import locks
    lock = locks.setdefault(req.device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行任务，请先取消任务或等待完成")
    async with lock:
        return await _execute_action(req)


async def _execute_action(req: ActionRequest):
    device = _get_device(req.device_id)

    # Pre-fetch elements for description generation (before action changes the screen)
    try:
        pre_ui_text, pre_elements = await device.get_ui_state()
    except Exception:
        pre_ui_text, pre_elements = "", []

    # Execute the action
    action = req.action
    args = req.args
    try:
        if action == "tap_element":
            result = await device.tap_element(args["index"])
        elif action == "tap":
            if (args.get("coordinate_space") or args.get("coordinateSpace")) == "device_px":
                result = await device.tap_absolute(args["x"], args["y"])
            else:
                result = await device.tap(args["x"], args["y"])
        elif action == "input_text":
            result = await device.input_text(args["text"], args.get("clear", False))
        elif action == "scroll":
            result = await device.scroll(args["direction"], args.get("distance", "medium"))
        elif action == "swipe":
            if (args.get("coordinate_space") or args.get("coordinateSpace")) == "device_px":
                result = await device.swipe_absolute(
                    args["x1"], args["y1"], args["x2"], args["y2"],
                    args.get("duration_ms", 500),
                )
            else:
                result = await device.swipe(
                    args["x1"], args["y1"], args["x2"], args["y2"],
                    args.get("duration_ms", 500),
                )
        elif action == "global_action":
            result = await device.global_action(args["action"])
        elif action == "press_key":
            result = await device.press_key(args["key"])
        elif action == "start_app":
            result = await device.start_app(args["package"], args.get("activity", ""))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Post-action delay so screen settles before screenshot
    _POST_DELAYS = {"start_app": 2.5, "global_action": 1.2}
    _FAST = {"input_text", "press_key", "scroll", "swipe"}
    delay = _POST_DELAYS.get(action, 0.3 if action in _FAST else 1.0)
    await asyncio.sleep(delay)

    description = _step_description(action, args, pre_elements)
    # The recorder engine is the common event sink for browser actions and
    # external adapters. If no recording session is active this is a no-op.
    event_payload = {
        "kind": action, **args, "description": description,
        "before": {
            "ui_text": pre_ui_text,
            "elements": pre_elements,
            "screen": {"width": device.screen_width, "height": device.screen_height},
            "captured_at_ms": int(time.time() * 1000),
        },
    }
    # Enrich element-based browser actions with the element metadata and its
    # center point from the pre-action UI tree. Coordinates stay in the
    # screenshot/model-image space used by this recorder surface.
    if action == "tap_element":
        target_element = next(
            (element for element in pre_elements if element.get("index") == args.get("index")),
            None,
        )
    elif action == "tap":
        tap_x, tap_y = args.get("x"), args.get("y")
        target_element = None
        if isinstance(tap_x, (int, float)) and isinstance(tap_y, (int, float)):
            target_element = next((element for element in reversed(pre_elements)
                if isinstance(element.get("bounds"), dict)
                and element["bounds"].get("left", 0) <= tap_x <= element["bounds"].get("right", 0)
                and element["bounds"].get("top", 0) <= tap_y <= element["bounds"].get("bottom", 0)), None)
    else:
        target_element = None
    if target_element:
        target_label = target_element.get("text") or target_element.get("contentDescription") or target_element.get("resourceId")
        if target_label:
            description = f'点击「{target_label}」'
            event_payload["description"] = description
        event_payload.update({
            "text": target_element.get("text", ""),
            "contentDescription": target_element.get("contentDescription", ""),
            "resourceId": target_element.get("resourceId", ""),
            "className": target_element.get("className", ""),
            "target": target_element,
        })
        if action == "tap_element":
            event_payload.update({
                "x": target_element.get("cx"),
                "y": target_element.get("cy"),
                "coordinate_space": "device_px",
            })
    # A visible accessibility-tree delta is evidence of UI response, not proof
    # that the business operation passed.
    snap = await _snapshot(device)
    def _state_signature(items: List[Any]) -> tuple:
        def value(item: Any, key: str, default: Any = None) -> Any:
            return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
        return tuple((
            value(item, "resourceId", ""), value(item, "text", ""),
            value(item, "className", ""),
            (value(item, "bounds", None) or {}).get("left"),
            (value(item, "bounds", None) or {}).get("top"),
            (value(item, "bounds", None) or {}).get("right"),
            (value(item, "bounds", None) or {}).get("bottom"),
            value(item, "enabled", True), value(item, "visible", True),
            value(item, "selected", False), value(item, "checked", False),
        ) for item in items)
    state_changed = _state_signature(pre_elements) != _state_signature(snap.elements)
    if action in {"tap", "tap_element", "input_text", "scroll", "swipe"}:
        event_payload["verification"] = {
            "action_dispatched": True,
            "ui_state_changed": state_changed,
            "status": "effect_observed" if state_changed else "no_visible_change",
            "scope": "accessibility_tree",
            "note": "UI 状态变化仅代表观察到界面响应，不代表业务结果成功"
                    if state_changed else
                    "未观察到可访问性树变化；不等于操作必然失败，可能存在无障碍树不可见的响应",
        }
    event_payload["after"] = {
        "ui_text": snap.ui_text,
        "elements": [element.dict() for element in snap.elements],
        "screen": {"width": snap.screen_width, "height": snap.screen_height},
        "captured_at_ms": int(time.time() * 1000),
    }
    recorded_event = recorder_engine.append(req.device_id, event_payload)

    return ActionResponse(
        result=result,
        screenshot_b64=snap.screenshot_b64,
        ui_text=snap.ui_text,
        elements=snap.elements,
        description=description,
        event=recorded_event,
    )


@router.post("/save", response_model=SaveResponse, status_code=201)
async def save_recording(req: SaveRequest, request: Request):
    """Convert a recorded step list into a TestSuite + TestCase."""
    if not req.steps:
        raise HTTPException(status_code=400, detail="No steps recorded")
    if not req.suite_name.strip():
        raise HTTPException(status_code=400, detail="suite_name is required")
    if not req.expected.strip():
        raise HTTPException(status_code=400, detail="expected is required")

    # Keep the user-defined case title separate from the executable step list.
    # Steps and evidence live in replay_steps / recorder_events, not in TestCase.path.
    case_name = (req.case_name or "").strip()
    path = case_name or f"录制用例（{len(req.steps)} 步）"

    account_id = request.state.account_id
    async with AsyncSessionLocal() as db:
        suite = TestSuite(
            name=req.suite_name.strip(),
            source_format="recorded",
            account_id=account_id,
        )
        db.add(suite)
        await db.flush()

        case = TestCase(
            suite_id=suite.id,
            path=path,
            expected=req.expected.strip(),
            order=0,
            account_id=account_id,
            replay_steps=_to_replay_steps(req.steps, req.events or []),
            recorder_events="",
            lifecycle_actions_json=json.dumps({
                "pre_actions": req.pre_actions,
                "post_actions": req.post_actions,
            }, ensure_ascii=False),
        )
        db.add(case)
        await db.flush()
        # Store screenshots outside the database; event JSON keeps only
        # generated, case-scoped asset names and integrity metadata.
        from pathlib import Path
        asset_dir = Path(__file__).resolve().parents[1] / "data" / "recordings" / case.id
        stored_events = sanitize_events_for_storage(req.events or [], str(asset_dir))
        case.recorder_events = json.dumps(stored_events, ensure_ascii=False)
        await db.commit()
        await db.refresh(suite)
        await db.refresh(case)

    logger.info("Saved recorded test '%s' (%d steps)", req.suite_name, len(req.steps))
    return SaveResponse(suite_id=suite.id, case_id=case.id)


@router.get("/cases/{case_id}/events")
async def get_recorded_case_events(case_id: str, request: Request):
    """Read back the durable event/UI evidence for a recorded test case."""
    account_id = request.state.account_id
    async with AsyncSessionLocal() as db:
        case = (await db.execute(select(TestCase).where(
            TestCase.id == case_id,
            TestCase.account_id == account_id,
        ))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Recorded test case not found")
    try:
        events = json.loads(case.recorder_events or "[]")
    except json.JSONDecodeError:
        logger.exception("Invalid recorder_events JSON for case %s", case_id)
        raise HTTPException(status_code=500, detail="Stored recorder evidence is invalid")
    # Materialize screenshots only when the event inspector requests evidence.
    import base64
    from pathlib import Path
    asset_root = (Path(__file__).resolve().parents[1] / "data" / "recordings" / case.id).resolve()
    for event in events:
        evidence = event.get("screenshot_evidence") if isinstance(event, dict) else None
        assets = evidence.get("assets", {}) if isinstance(evidence, dict) else {}
        for label, filename in assets.items():
            if not isinstance(filename, str) or Path(filename).name != filename:
                continue
            asset_path = (asset_root / filename).resolve()
            if asset_path.parent != asset_root or not asset_path.is_file():
                continue
            encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
            if label == "post_action":
                event["screenshot_b64_after"] = encoded
            else:
                event["screenshot_b64"] = encoded
    return {"case_id": case.id, "events": events}


@router.get("/raw-state")
async def raw_state(device_id: str, request: Request):
    """DEBUG: return raw Portal state RPC response (unprocessed a11y tree)."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    return await device._rpc("state", {"filter": True}, timeout=30.0)
