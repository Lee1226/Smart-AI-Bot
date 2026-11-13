"""
Recorder API — interactive remote-control + step recording.

Flow:
  1. GET  /api/recorder/snapshot   → current screenshot + a11y elements
  2. POST /api/recorder/action     → execute one action, return new snapshot
  3. POST /api/recorder/save       → convert step list into TestSuite + TestCase

The saved TestCase uses `path` as a human-readable step description so the
LLM agent has clear context when replaying:

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
from pydantic import BaseModel
from sqlalchemy import select

from agent.ws_device import WebSocketDevice
from auth.access import require_device
from db.database import AsyncSessionLocal
from db.models import TestCase, TestSuite
from agent import local_adb
from core.recorder_engine import recorder_engine

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


def _to_replay_steps(steps: List["RecordedStep"]) -> str:
    """Convert recorded steps into a deterministic replay plan (JSON string).

    Each entry carries the action + its coordinates/args + delay_ms (time to wait
    BEFORE executing it). An empty step list → "". Replay runs execute these
    verbatim, skipping per-step AI decisions.
    """
    replay: List[dict] = []
    for s in steps:
        args = s.args or {}
        step: dict = {"kind": s.action, "delay_ms": int(args.get("delay_ms", 0))}
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


class SnapshotResponse(BaseModel):
    screenshot_b64: str
    ui_text: str
    elements: List[Element]


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


class RecordedStep(BaseModel):
    action: str
    args: Dict[str, Any]
    description: str


class SaveRequest(BaseModel):
    device_id: str
    suite_name: str
    expected: str
    steps: List[RecordedStep]


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
        )
        for e in elements_raw
    ]
    return SnapshotResponse(
        screenshot_b64=screenshot_b64,
        ui_text=ui_text,
        elements=elements,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

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
        _, pre_elements = await device.get_ui_state()
    except Exception:
        pre_elements = []

    # Execute the action
    action = req.action
    args = req.args
    try:
        if action == "tap_element":
            result = await device.tap_element(args["index"])
        elif action == "tap":
            result = await device.tap(args["x"], args["y"])
        elif action == "input_text":
            result = await device.input_text(args["text"], args.get("clear", False))
        elif action == "scroll":
            result = await device.scroll(args["direction"], args.get("distance", "medium"))
        elif action == "swipe":
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
    snap = await _snapshot(device)

    return ActionResponse(
        result=result,
        screenshot_b64=snap.screenshot_b64,
        ui_text=snap.ui_text,
        elements=snap.elements,
        description=description,
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

    # Build path from step descriptions
    steps_text = "\n".join(f"{i + 1}. {s.description}" for i, s in enumerate(req.steps))
    path = (
        f"[录制步骤]\n"
        f"{steps_text}\n"
        f"请重新执行以上步骤并验证期望结果。"
    )

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
            replay_steps=_to_replay_steps(req.steps),
        )
        db.add(case)
        await db.commit()
        await db.refresh(suite)
        await db.refresh(case)

    logger.info("Saved recorded test '%s' (%d steps)", req.suite_name, len(req.steps))
    return SaveResponse(suite_id=suite.id, case_id=case.id)


@router.get("/raw-state")
async def raw_state(device_id: str, request: Request):
    """DEBUG: return raw Portal state RPC response (unprocessed a11y tree)."""
    await _authorize_device(request, device_id)
    device = _get_device(device_id)
    return await device._rpc("state", {"filter": True}, timeout=30.0)
