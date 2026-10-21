"""Live device screen.

- `GET /{id}/screenshot.jpg` — a single JPEG frame (the web UI polls this ~1/s).
  Short requests that complete cleanly, so they never hog a browser connection
  the way a long-lived MJPEG stream does.
- WS `/v1/devices/{id}/live` — H.264 from an authorized remote App / ADB bridge.

Blocking work (adb subprocess, Pillow encode) is pushed to threads so it never
stalls the asyncio event loop (which would hang every other request)."""
from __future__ import annotations

import asyncio
import anyio
import hashlib
import io
import logging
import os
import shutil
import subprocess
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.ws_device import WebSocketDevice
from auth.access import operator_account_id
from auth.jwt_handler import decode_token
from db.database import AsyncSessionLocal
from db.models import ApiKey, Device, User
from ws.portal_ws import connected_devices, send_rpc
from ws.video import VideoSession, sessions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/devices", tags=["live"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _local_adb_allowed(request: Request, db: AsyncSession) -> bool:
    return (
        request.state.account_id == await operator_account_id(db)
        and request.state.role in {"owner", "admin"}
    )


async def _websocket_identity(websocket: WebSocket) -> tuple[str, str] | None:
    header = websocket.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else websocket.query_params.get("token", "")
    if not token:
        return None
    async with AsyncSessionLocal() as db:
        if token.startswith("sk-"):
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            api_key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))).scalar_one_or_none()
            if not api_key:
                return None
            creator = await db.get(User, api_key.created_by) if api_key.created_by else None
            role = creator.role if creator and creator.account_id == api_key.account_id else ""
            return api_key.account_id, role
        payload = decode_token(token)
        if not payload:
            return None
        user = await db.get(User, payload.get("user_id"))
        if not user or user.account_id != payload.get("account_id"):
            return None
        return user.account_id, user.role


def _adb_path() -> Optional[str]:
    found = shutil.which("adb")
    if found:
        return found
    fallback = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    return fallback if os.path.exists(fallback) else None


def _adb_serials() -> List[str]:
    """Serials of devices in `adb devices` state == 'device'. Blocking — call via
    asyncio.to_thread from async code."""
    adb = _adb_path()
    if not adb:
        return []
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _to_jpeg(raw: bytes, max_w: int = 540, quality: int = 70) -> bytes:
    """PNG/JPEG bytes → downscaled JPEG. CPU-bound — call via asyncio.to_thread."""
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


async def _adb_jpeg(serial: str) -> bytes:
    adb = _adb_path()
    proc = await asyncio.create_subprocess_exec(
        adb, "-s", serial, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    raw, _ = await proc.communicate()
    if not raw:
        raise RuntimeError("screencap returned no data")
    return await asyncio.to_thread(_to_jpeg, raw)


# The live preview is best-effort: reuse any device frame from the last ~2s
# (typically one the agent just captured) rather than triggering a competing
# device screenshot. This keeps the rate-limited accessibility screenshot for
# the agent, which has priority.
_LIVE_FRAME_MAX_AGE = 2.0


async def _screenshot_jpeg(device_id: str) -> bytes:
    raw = await WebSocketDevice(device_id).screenshot(accept_cached_age=_LIVE_FRAME_MAX_AGE)
    return await asyncio.to_thread(_to_jpeg, raw)


def _resolve_adb_serial(serials: List[str], source: str, serial: Optional[str]) -> Optional[str]:
    if source == "adb":
        return serial or (serials[0] if serials else None)
    if source == "auto" and len(serials) == 1:
        return serials[0]
    return None


class Capabilities(BaseModel):
    online: bool
    adb_available: bool
    adb_serials: List[str]
    video_available: bool = False


@router.get("/{device_id}/capabilities", response_model=Capabilities)
async def capabilities(device_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    browser_usb = device is not None and device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"}
    serials = await asyncio.to_thread(_adb_serials) if not browser_usb and await _local_adb_allowed(request, db) else []
    conn = connected_devices.get(device_id)
    return Capabilities(
        online=conn is not None and conn.is_connected,
        adb_available=bool(serials),
        adb_serials=serials,
        video_available=bool(conn and conn.is_connected and conn.video_available),
    )


@router.get("/{device_id}/screenshot.jpg")
async def screenshot_frame(device_id: str, request: Request, source: str = "auto",
                           serial: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """One JPEG frame. source: auto | adb | screenshot."""
    device = await db.get(Device, device_id)
    browser_usb = device is not None and device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"}
    serials = await asyncio.to_thread(_adb_serials) if not browser_usb and await _local_adb_allowed(request, db) else []
    use = None if browser_usb else _resolve_adb_serial(serials, source, serial)
    try:
        if use:
            jpeg = await _adb_jpeg(use)
        else:
            conn = connected_devices.get(device_id)
            if conn is None or not conn.is_connected:
                raise HTTPException(status_code=404, detail="Device offline")
            jpeg = await _screenshot_jpeg(device_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("live frame failed: %s", e)
        raise HTTPException(status_code=503, detail="Frame unavailable")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def stream_h264(websocket: WebSocket, device_id: str):
    """Authenticated preview of this account's device, with legacy local ADB fallback."""
    identity = await _websocket_identity(websocket)
    if not identity:
        await websocket.close(code=4401)
        return
    account_id, role = identity
    async with AsyncSessionLocal() as db:
        device = (await db.execute(select(Device).where(
            Device.id == device_id, Device.account_id == account_id
        ))).scalar_one_or_none()
        is_operator = account_id == await operator_account_id(db) and role in {"owner", "admin"}
    if not device:
        await websocket.close(code=4404)
        return
    conn = connected_devices.get(device_id)
    if conn and conn.is_connected and conn.video_available:
        await websocket.accept()
        await _stream_remote(websocket, conn)
        return
    # A remote video request must never show an unrelated server USB device.
    if websocket.query_params.get("source") == "device":
        await websocket.close(code=4404)
        return
    if device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"} or not is_operator:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    source = websocket.query_params.get("source", "auto")
    serials = await asyncio.to_thread(_adb_serials)
    serial = _resolve_adb_serial(serials, source, websocket.query_params.get("serial"))
    if not serial:
        await websocket.close(code=4404)  # no ADB → client falls back to screenshot
        return

    adb = _adb_path()
    proc = None
    try:
        while True:  # screenrecord caps at 3 min/run; loop to stay continuous
            proc = await asyncio.create_subprocess_exec(
                adb, "-s", serial, "exec-out", "screenrecord",
                "--output-format=h264", "--time-limit", "175", "--bit-rate", "8000000", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            while True:
                chunk = await proc.stdout.read(16384)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
            await proc.wait()
            proc = None
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("h264 stream ended: %s", e)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass


async def _stream_remote(websocket: WebSocket, conn):
    # One preview per device. A second viewer gets a clear error; it must not
    # stop or replace the first viewer's encoder/session.
    if any(s.connection is conn for s in sessions.values()):
        await websocket.close(code=4409, reason="Video already being viewed")
        return
    stream_id = uuid.uuid4().hex
    session = VideoSession(conn)
    sessions[stream_id] = session

    async def watch_disconnect():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    async def relay():
        await send_rpc(conn, "video/start", {"stream_id": stream_id}, timeout=15)
        await asyncio.wait_for(session.ready.wait(), 15)
        first_frame = True
        while conn.is_connected and connected_devices.get(conn.device_id) is conn:
            # Encoders may be silent on a static screen. Only startup has a
            # frame deadline; provider/control disconnects terminate separately.
            chunk = await asyncio.wait_for(session.queue.get(), 15) if first_frame else await session.queue.get()
            if chunk is None:
                return
            first_frame = False
            await asyncio.wait_for(websocket.send_bytes(chunk), 5)

    async def watch_control():
        while conn.is_connected and connected_devices.get(conn.device_id) is conn:
            await asyncio.sleep(1)

    tasks = [asyncio.create_task(watch_disconnect()), asyncio.create_task(relay()), asyncio.create_task(watch_control())]
    close_code = 1000
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError, TimeoutError, asyncio.CancelledError) as error:
        close_code = 4404
        logger.debug("remote video ended: %s", error)
    finally:
        with anyio.CancelScope(shield=True):
            sessions.pop(stream_id, None)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if session.provider:
                try:
                    await session.provider.close()
                except RuntimeError:
                    pass
            if conn.is_connected and connected_devices.get(conn.device_id) is conn:
                try:
                    await send_rpc(conn, "video/stop", {"stream_id": stream_id}, timeout=5)
                except Exception:
                    pass
            try:
                await websocket.close(code=close_code)
            except RuntimeError:
                pass
