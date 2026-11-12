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
from agent import local_adb
from ws.video import VideoSession, sessions

logger = logging.getLogger(__name__)
_local_adb_streams: set[str] = set()
_local_adb_sessions: dict[str, object] = {}
_local_adb_websockets: dict[str, WebSocket] = {}
_local_adb_claim_locks: dict[str, asyncio.Lock] = {}
_LOCAL_ADB_TAKEOVER_TIMEOUT = 4.0

class _H264AnnexBStream:
    """Frame NAL units from arbitrary TCP reads and retain a decodable GOP.

    A NAL is complete only when the next Annex-B start code arrives, or when
    the socket has been idle long enough for the final NAL to be flushed.
    """
    MAX_GOP_BYTES = 32 * 1024 * 1024
    MAX_PENDING_BYTES = 4 * 1024 * 1024

    def __init__(self) -> None:
        self.buffer = b""
        self.sps = b""
        self.pps = b""
        self.gop = bytearray()
        self.idr_count = 0

    @staticmethod
    def _marks(data: bytes):
        marks = []
        i = 0
        while i + 3 <= len(data):
            if i + 4 <= len(data) and data[i:i + 4] == b"\x00\x00\x00\x01":
                marks.append((i, 4))
                i += 4
            elif data[i:i + 3] == b"\x00\x00\x01":
                marks.append((i, 3))
                i += 3
            else:
                i += 1
        return marks

    @property
    def bootstrap(self) -> bytes:
        return bytes(self.gop)

    def _consume(self, nal: bytes) -> bytes:
        if not nal:
            return b""
        kind = nal[0] & 31
        unit = b"\x00\x00\x00\x01" + nal
        if kind == 7:
            if nal != self.sps:
                self.sps = nal
                self.gop.clear()
            return unit
        if kind == 8:
            if nal != self.pps:
                self.pps = nal
                self.gop.clear()
            return unit
        if kind == 5:
            first_slice = len(nal) > 1 and bool(nal[1] & 0x80)
            if first_slice:
                self.gop = bytearray()
                if self.sps and self.pps:
                    self.gop.extend(b"\x00\x00\x00\x01" + self.sps)
                    self.gop.extend(b"\x00\x00\x00\x01" + self.pps)
                    self.gop.extend(unit)
                    self.idr_count += 1
            elif self.gop:
                self.gop.extend(unit)
        elif self.gop:
            self.gop.extend(unit)

        if len(self.gop) > self.MAX_GOP_BYTES:
            logger.warning("[H264] GOP cache exceeded limit; waiting for next IDR (bytes=%d)", len(self.gop))
            self.gop.clear()
        return unit

    def feed(self, chunk: bytes) -> bytes:
        data = self.buffer + chunk
        marks = self._marks(data)
        units = []
        for index in range(len(marks) - 1):
            start, prefix = marks[index]
            end = marks[index + 1][0]
            nal = data[start + prefix:end].rstrip(b"\x00")
            unit = self._consume(nal)
            if unit:
                units.append(unit)
        self.buffer = data[marks[-1][0]:] if marks else data
        if len(self.buffer) > self.MAX_PENDING_BYTES:
            logger.warning("[H264] Pending NAL exceeded limit; dropping incomplete NAL (%d bytes)", len(self.buffer))
            self.buffer = b""
        return b"".join(units)

    def flush(self) -> bytes:
        """Finish the last NAL after a socket idle timeout."""
        if not self.buffer:
            return b""
        marks = self._marks(self.buffer)
        if not marks:
            self.buffer = b""
            return b""
        start, prefix = marks[0]
        nal = self.buffer[start + prefix:].rstrip(b"\x00")
        self.buffer = b""
        return self._consume(nal)


# One Scrcpy producer per local ADB device, with an independent bounded queue
# for every browser viewer. A slow viewer is disconnected without affecting
# the other viewers or the underlying device capture.
_local_adb_hubs: dict[str, dict] = {}
_local_adb_hub_locks: dict[str, asyncio.Lock] = {}


def _local_adb_hub_lock(device_id: str) -> asyncio.Lock:
    lock = _local_adb_hub_locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _local_adb_hub_locks[device_id] = lock
    return lock


async def _run_local_adb_hub(device_id: str, serial: str, hub: dict) -> None:
    from agent.scrcpy_video import ScrcpyVideoSession
    from agent.webrtc_video import webrtc_enabled

    hub["webrtc_enabled"] = webrtc_enabled()
    session = ScrcpyVideoSession(_adb_path(), serial)
    hub["session"] = session
    stream = hub["stream"]
    publisher = None
    if hub.get("webrtc_enabled"):
        try:
            from agent.webrtc_video import FFmpegRtspPublisher, mediamtx_manager
            await asyncio.to_thread(mediamtx_manager.ensure_started)
            stream_path = f"smart_ai_{uuid.uuid4().hex}"
            publisher = FFmpegRtspPublisher(stream_path, session.max_fps)
            await asyncio.to_thread(publisher.start)
            hub["webrtc_publisher"] = publisher
            hub["webrtc_path"] = stream_path
            logger.info("[WEBRTC] publisher ready: device_id=%s path=%s", device_id, stream_path)
        except Exception as exc:
            logger.warning("[WEBRTC] disabled for device_id=%s: %s", device_id, exc)
            publisher = None
    lock = _local_adb_hub_lock(device_id)
    total_bytes = 0
    total_nals = 0
    last_stats = asyncio.get_running_loop().time()

    async def publish(data: bytes, idle_flush: bool = False) -> None:
        nonlocal total_nals, last_stats
        if not data and not idle_flush:
            return
        close_viewers = []
        async with lock:
            # Parser/cache update and subscriber fan-out share one lock. This
            # makes the bootstrap snapshot and following live bytes contiguous.
            batch = stream.feed(data) if data else stream.flush()
            if not batch and not idle_flush:
                return
            total_nals += batch.count(b"\x00\x00\x00\x01")
            bootstrap = stream.bootstrap
            # WebRTC receives a complete decodable GOP once, then only live
            # Annex-B batches. Publisher writes are non-blocking and isolated
            # from the Scrcpy reader / WebSocket fan-out.
            if publisher is not None and not publisher.failed:
                if not hub.get("webrtc_bootstrap_sent"):
                    if bootstrap:
                        if publisher.write(bootstrap):
                            hub["webrtc_bootstrap_sent"] = True
                elif batch:
                    publisher.write(batch)
            for viewer_id, queue in list(hub["subscribers"].items()):
                try:
                    if not hub["bootstrap_sent"].get(viewer_id, False):
                        if not bootstrap:
                            continue
                        while not queue.empty():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        queue.put_nowait(bootstrap + b"\x00\x00\x00\x01")
                        hub["bootstrap_sent"][viewer_id] = True
                        logger.info(
                            "[LIVE HUB] viewer bootstrapped: device_id=%s viewer=%s gop_bytes=%d idr_count=%d",
                            device_id, viewer_id, len(bootstrap), stream.idr_count,
                        )
                    elif batch:
                        queue.put_nowait(batch + b"\x00\x00\x00\x01")

                    # A text control message tells the browser to flush its last
                    # assembled access unit. Do this only on socket idle, not at
                    # every arbitrary TCP read (a frame may contain many slices).
                    if idle_flush and hub["bootstrap_sent"].get(viewer_id, False):
                        queue.put_nowait(None)
                except asyncio.QueueFull:
                    hub["subscribers"].pop(viewer_id, None)
                    ws = hub["websockets"].pop(viewer_id, None)
                    hub["bootstrap_sent"].pop(viewer_id, None)
                    if ws is not None:
                        close_viewers.append(ws)
                    logger.warning("[LIVE HUB] slow viewer dropped: device_id=%s viewer=%s", device_id, viewer_id)

            now = asyncio.get_running_loop().time()
            if now - last_stats >= 5:
                logger.info(
                    "[H264] stream stats: device_id=%s bytes=%d nals=%d idr=%d gop_bytes=%d idle_flush=%s viewers=%d",
                    device_id, total_bytes, total_nals, stream.idr_count,
                    len(bootstrap), idle_flush, len(hub["subscribers"]),
                )
                last_stats = now
        for ws in close_viewers:
            try:
                await ws.close(code=4408, reason="Viewer too slow; reconnect for a fresh keyframe")
            except Exception:
                pass

    try:
        await session.start()
        hub["ready"].set()
        logger.info("[LIVE HUB] producer ready: device_id=%s serial=%s", device_id, serial)
        while True:
            chunk = await session.read(64 * 1024)
            if chunk is None:
                # A socket idle timeout means the final Annex-B NAL can now be
                # completed even when the phone screen remains unchanged.
                await publish(b"", idle_flush=True)
                continue
            if not chunk:
                raise RuntimeError("Scrcpy video stream ended")
            total_bytes += len(chunk)
            await publish(chunk)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        hub["error"] = str(exc)
        logger.warning("Local ADB video producer stopped: device_id=%s error=%s", device_id, exc)
    finally:
        hub["ready"].set()
        if publisher is not None:
            try:
                await asyncio.to_thread(publisher.stop)
            except Exception:
                logger.debug("FFmpeg publisher stop failed: device_id=%s", device_id, exc_info=True)
        try:
            await session.stop()
        except Exception:
            logger.debug("Scrcpy stop failed: device_id=%s", device_id, exc_info=True)
        async with lock:
            if _local_adb_hubs.get(device_id) is hub:
                _local_adb_hubs.pop(device_id, None)
            viewers = list(hub["websockets"].values())
            hub["subscribers"].clear()
            hub["websockets"].clear()
            hub["bootstrap_sent"].clear()
        for ws in viewers:
            try:
                await ws.close(code=4404, reason="Device video producer stopped")
            except Exception:
                pass


def _new_local_adb_hub(device_id: str, serial: str) -> dict:
    """Create one shared Scrcpy producer; caller must hold the device hub lock."""
    hub = {
        "ready": asyncio.Event(),
        "subscribers": {},
        "websockets": {},
        "session": None,
        "task": None,
        "error": None,
        "stream": _H264AnnexBStream(),
        "bootstrap_sent": {},
        "webrtc_enabled": False,
        "webrtc_publisher": None,
        "webrtc_path": None,
        "webrtc_bootstrap_sent": False,
        "webrtc_viewers": 0,
        "stopping": False,
    }
    _local_adb_hubs[device_id] = hub
    hub["task"] = asyncio.create_task(_run_local_adb_hub(device_id, serial, hub))
    return hub


async def _release_local_adb_webrtc_viewer(device_id: str) -> None:
    lock = _local_adb_hub_lock(device_id)
    task_to_stop = None
    async with lock:
        hub = _local_adb_hubs.get(device_id)
        if hub is None:
            return
        hub["webrtc_viewers"] = max(0, hub.get("webrtc_viewers", 0) - 1)
        if (
            not hub["subscribers"]
            and hub["webrtc_viewers"] == 0
            and _local_adb_hubs.get(device_id) is hub
        ):
            hub["stopping"] = True
            task_to_stop = hub.get("task")
    if task_to_stop and not task_to_stop.done():
        task_to_stop.cancel()
        await asyncio.gather(task_to_stop, return_exceptions=True)


async def _subscribe_local_adb_stream(websocket: WebSocket, device_id: str, serial: str) -> None:
    viewer_id = uuid.uuid4().hex
    lock = _local_adb_hub_lock(device_id)
    while True:
        wait_for_stopping_hub = None
        async with lock:
            hub = _local_adb_hubs.get(device_id)
            if hub is not None and hub.get("stopping"):
                wait_for_stopping_hub = hub.get("task")
            else:
                if hub is None:
                    hub = _new_local_adb_hub(device_id, serial)
                queue = asyncio.Queue(maxsize=64)
                bootstrap = hub["stream"].bootstrap
                if bootstrap:
                    queue.put_nowait(bootstrap + b"\x00\x00\x00\x01")
                    queue.put_nowait(None)  # flush the final cached access unit
                # If the first IDR has not arrived yet, the producer will replace
                # queued deltas with bootstrap as soon as SPS/PPS + IDR are complete.
                hub["bootstrap_sent"][viewer_id] = bool(bootstrap)
                hub["subscribers"][viewer_id] = queue
                hub["websockets"][viewer_id] = websocket
                logger.info("[LIVE HUB] viewer subscribed: device_id=%s viewer=%s count=%d bootstrap=%s",
                            device_id, viewer_id, len(hub["subscribers"]), bool(bootstrap))
        if wait_for_stopping_hub is None:
            break
        if not wait_for_stopping_hub.done():
            await asyncio.gather(wait_for_stopping_hub, return_exceptions=True)

    try:
        await asyncio.wait_for(hub["ready"].wait(), timeout=15)
        if hub["error"]:
            await websocket.close(code=4404, reason="Scrcpy video unavailable")
            return
        while True:
            chunk = await queue.get()
            if chunk is None:
                await websocket.send_text("h264_flush")
            else:
                await websocket.send_bytes(chunk)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError, OSError) as exc:
        logger.info("[LIVE HUB] viewer send ended: device_id=%s viewer=%s error=%s",
                    device_id, viewer_id, exc)
    except asyncio.TimeoutError:
        try:
            await websocket.close(code=4404, reason="Scrcpy startup timed out")
        except Exception:
            pass
    finally:
        task_to_stop = None
        async with lock:
            hub["subscribers"].pop(viewer_id, None)
            hub["websockets"].pop(viewer_id, None)
            hub.get("bootstrap_sent", {}).pop(viewer_id, None)
            if (
                not hub["subscribers"]
                and hub.get("webrtc_viewers", 0) == 0
                and _local_adb_hubs.get(device_id) is hub
            ):
                hub["stopping"] = True
                task_to_stop = hub["task"]
        if task_to_stop and not task_to_stop.done():
            task_to_stop.cancel()
            await asyncio.gather(task_to_stop, return_exceptions=True)



def _local_adb_claim_lock(device_id: str) -> asyncio.Lock:
    # Called on the event loop; no await occurs while creating the lock.
    lock = _local_adb_claim_locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _local_adb_claim_locks[device_id] = lock
    return lock


async def _claim_local_adb_stream(websocket: WebSocket, device_id: str) -> bool:
    """Take over a previous preview and guarantee one active stream per device."""
    async with _local_adb_claim_lock(device_id):
        old_websocket = _local_adb_websockets.get(device_id)
        if device_id in _local_adb_streams and old_websocket is not None:
            logger.info(
                "[LIVE] Taking over existing preview: device_id=%s old_client=%s new_client=%s",
                device_id, old_websocket.client, websocket.client,
            )
            try:
                await old_websocket.close(code=4410, reason="Preview replaced by a new viewer")
            except Exception:
                logger.debug("Previous preview WebSocket already closed: device_id=%s", device_id)

            # Closing the browser socket alone may not wake a worker thread
            # blocked in Scrcpy recv(); stop the underlying session as well.
            old_session = _local_adb_sessions.get(device_id)
            if old_session is not None:
                try:
                    await old_session.stop()
                except Exception:
                    logger.exception("Failed to stop replaced Scrcpy session: device_id=%s", device_id)

            deadline = asyncio.get_running_loop().time() + _LOCAL_ADB_TAKEOVER_TIMEOUT
            while device_id in _local_adb_streams and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)

        if device_id in _local_adb_streams:
            logger.warning(
                "[LIVE] Preview takeover timed out; existing session still active: device_id=%s",
                device_id,
            )
            return False

        _local_adb_streams.add(device_id)
        _local_adb_websockets[device_id] = websocket
        return True


async def stop_local_adb_stream(device_id: str) -> None:
    """Stop the shared Scrcpy producer before a local ADB device is detached."""
    lock = _local_adb_hub_lock(device_id)
    async with lock:
        hub = _local_adb_hubs.get(device_id)
        if hub is not None:
            hub["stopping"] = True
            viewers = list(hub["websockets"].values())
            task = hub.get("task")
        else:
            viewers = []
            task = None
    # Keep the stopping hub registered until its producer has released Scrcpy
    # and the ADB forward. Concurrent subscribers wait on this task instead of
    # accidentally starting a second producer for the same device.
    for ws in viewers:
        try:
            await ws.close(code=4404, reason="Device detached")
        except Exception:
            pass
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with lock:
        if hub is not None and _local_adb_hubs.get(device_id) is hub:
            _local_adb_hubs.pop(device_id, None)
    # Retain cleanup for the legacy single-viewer fallback path.
    session = _local_adb_sessions.get(device_id)
    if session is not None:
        try:
            await session.stop()
        except Exception:
            logger.exception("Failed to stop legacy Scrcpy session during detach: device_id=%s", device_id)

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
    return local_adb.adb_path()


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
    raw = await local_adb._exec(
        "-s", serial, "exec-out", "screencap", "-p", timeout=15, binary=True
    )
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


@router.post("/{device_id}/webrtc")
async def start_webrtc_playback(
    device_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Acquire a WebRTC viewer reference and return its local WHEP endpoint."""
    from agent.webrtc_video import webrtc_enabled

    device = await db.get(Device, device_id)
    if device is None or device.connection_type != "adb_usb":
        raise HTTPException(status_code=404, detail="仅本地 ADB USB 设备支持 WebRTC PoC")
    if not await _local_adb_allowed(request, db):
        raise HTTPException(status_code=403, detail="仅管理员可访问本地 ADB USB 视频")
    if not webrtc_enabled():
        raise HTTPException(status_code=409, detail="SMART_AI_WEBRTC_ENABLED 未启用")

    serial = local_adb.serial_for(device_id)
    if not serial:
        raise HTTPException(status_code=409, detail="本地 ADB USB 设备当前未连接")

    lock = _local_adb_hub_lock(device_id)
    while True:
        wait_for_stopping_hub = None
        async with lock:
            hub = _local_adb_hubs.get(device_id)
            if hub is not None and hub.get("stopping"):
                wait_for_stopping_hub = hub.get("task")
            else:
                if hub is None:
                    hub = _new_local_adb_hub(device_id, serial)
                hub["webrtc_viewers"] = hub.get("webrtc_viewers", 0) + 1
                break
        if wait_for_stopping_hub is not None and not wait_for_stopping_hub.done():
            await asyncio.gather(wait_for_stopping_hub, return_exceptions=True)

    try:
        await asyncio.wait_for(hub["ready"].wait(), timeout=25)
        if hub.get("error"):
            raise RuntimeError(hub["error"])
        deadline = asyncio.get_running_loop().time() + 20
        while (
            not hub.get("webrtc_bootstrap_sent")
            and hub.get("webrtc_publisher") is not None
            and not hub["webrtc_publisher"].failed
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.1)
        publisher = hub.get("webrtc_publisher")
        stream_path = hub.get("webrtc_path")
        if not publisher or publisher.failed or not stream_path or not hub.get("webrtc_bootstrap_sent"):
            raise RuntimeError("WebRTC 发布流未就绪，请检查 FFmpeg/MediaMTX 和 Scrcpy 视频源")
        port = os.environ.get("SMART_AI_MEDIAMTX_WEBRTC_PORT", "8889")
        return {
            "enabled": True,
            "whep_url": f"http://127.0.0.1:{port}/{stream_path}/whep",
            "stream_path": stream_path,
        }
    except asyncio.TimeoutError as exc:
        await _release_local_adb_webrtc_viewer(device_id)
        raise HTTPException(status_code=504, detail="等待 Scrcpy/WebRTC 视频源超时") from exc
    except Exception as exc:
        await _release_local_adb_webrtc_viewer(device_id)
        acquired = False
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{device_id}/webrtc", status_code=204)
async def stop_webrtc_playback(
    device_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    device = await db.get(Device, device_id)
    if device is None or device.connection_type != "adb_usb":
        raise HTTPException(status_code=404, detail="本地 ADB USB 设备不存在")
    if not await _local_adb_allowed(request, db):
        raise HTTPException(status_code=403, detail="仅管理员可关闭本地 ADB USB 视频")
    await _release_local_adb_webrtc_viewer(device_id)
    return Response(status_code=204)


@router.get("/{device_id}/capabilities", response_model=Capabilities)
async def capabilities(device_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    browser_usb = device is not None and device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge", "adb_usb"}
    serials = await asyncio.to_thread(_adb_serials) if not browser_usb and await _local_adb_allowed(request, db) else []
    conn = connected_devices.get(device_id)
    native_online = device is not None and device.connection_type == "adb_usb" and local_adb.is_bound(device_id)
    return Capabilities(
        online=native_online or (conn is not None and conn.is_connected),
        adb_available=bool(serials),
        adb_serials=serials,
        video_available=native_online or bool(conn and conn.is_connected and conn.video_available),
    )


@router.get("/{device_id}/screenshot.jpg")
async def screenshot_frame(device_id: str, request: Request, source: str = "auto",
                           serial: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """One JPEG frame. source: auto | adb | screenshot."""
    device = await db.get(Device, device_id)
    browser_usb = device is not None and device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge", "adb_usb"}
    serials = await asyncio.to_thread(_adb_serials) if not browser_usb and await _local_adb_allowed(request, db) else []
    native_serial = local_adb.serial_for(device_id) if device is not None and device.connection_type == "adb_usb" else None
    use = native_serial or (None if browser_usb else _resolve_adb_serial(serials, source, serial))
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


async def _stream_local_adb(websocket: WebSocket, serial: str, device_id: str | None = None):
    """Stream a directly attached USB device through an isolated Scrcpy session.

    This path is selected only for connection_type == adb_usb. Portal and
    bridge devices continue through _stream_remote() and their existing
    provider-video transport.
    """
    from agent.scrcpy_video import ScrcpyVideoSession

    adb = _adb_path()
    if not adb:
        await websocket.close(code=4404, reason="ADB executable not found")
        return

    session = ScrcpyVideoSession(adb, serial)
    try:
        try:
            await session.start()
        except Exception as exc:
            logger.warning("Scrcpy startup failed for local ADB device %s: %s", serial, exc)
            await websocket.close(code=4404, reason="Scrcpy video unavailable")
            return

        if device_id:
            _local_adb_sessions[device_id] = session

        # Relay raw H.264 Annex-B bytes and record enough telemetry to
        # distinguish an empty Scrcpy stream from a WebSocket relay failure.
        total_bytes = 0
        first_data_logged = False
        while True:
            chunk = await session.read(64 * 1024)
            if chunk is None:
                # Idle timeout is used by the shared Annex-B parser to flush
                # static-screen NALs; the legacy relay simply keeps waiting.
                continue
            if not chunk:
                logger.warning(
                    "[SCRCPY DEBUG] Stream ended: serial=%s total_bytes=%d",
                    serial,
                    total_bytes,
                )
                break

            total_bytes += len(chunk)
            if not first_data_logged:
                logger.info(
                    "[SCRCPY DEBUG] First video data: serial=%s bytes=%d head=%s",
                    serial,
                    len(chunk),
                    chunk[:32].hex(" "),
                )
                first_data_logged = True

            await websocket.send_bytes(chunk)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError, OSError) as exc:
        logger.debug("Scrcpy video stream ended for %s: %s", serial, exc)
    finally:
        if device_id and _local_adb_sessions.get(device_id) is session:
            _local_adb_sessions.pop(device_id, None)
        await session.stop()


async def stream_h264(websocket: WebSocket, device_id: str):
    """Authenticated preview with an isolated Scrcpy path for local ADB USB."""
    logger.info(
        "[LIVE DEBUG] WebSocket request: device_id=%s client=%s",
        device_id,
        websocket.client,
    )

    identity = await _websocket_identity(websocket)
    if not identity:
        logger.warning("[LIVE DEBUG] Authentication failed: device_id=%s", device_id)
        await websocket.close(code=4401, reason="Authentication failed")
        return

    account_id, role = identity
    async with AsyncSessionLocal() as db:
        device = (await db.execute(select(Device).where(
            Device.id == device_id, Device.account_id == account_id
        ))).scalar_one_or_none()
        is_operator = account_id == await operator_account_id(db) and role in {"owner", "admin"}

    if not device:
        logger.warning(
            "[LIVE DEBUG] Device not found or account mismatch: device_id=%s account_id=%s",
            device_id,
            account_id,
        )
        await websocket.close(code=4404, reason="Device not found")
        return

    conn = connected_devices.get(device_id)
    logger.info(
        "[LIVE DEBUG] Device resolved: id=%s type=%s role=%s is_operator=%s connected=%s",
        device_id,
        device.connection_type,
        role,
        is_operator,
        bool(conn and conn.is_connected),
    )

    # Local ADB USB devices must never fall through to the generic ADB path:
    # that path could select an unrelated device attached to the host.
    if device.connection_type == "adb_usb":
        native_serial = local_adb.serial_for(device_id)
        is_bound = local_adb.is_bound(device_id)
        logger.info(
            "[LIVE DEBUG] Local ADB state: device_id=%s serial=%s is_bound=%s",
            device_id,
            native_serial,
            is_bound,
        )

        # Accept before reporting device-state errors. Closing a Starlette
        # WebSocket before accept is surfaced by browsers as HTTP 403.
        await websocket.accept()

        if not native_serial:
            logger.error(
                "[LIVE DEBUG] adb_usb has no serial binding: device_id=%s",
                device_id,
            )
            await websocket.close(code=4404, reason="Local ADB device is not bound")
            return

        logger.info(
            "[LIVE DEBUG] Subscribing to shared Scrcpy stream: device_id=%s serial=%s",
            device_id,
            native_serial,
        )
        # The local ADB branch accepted this WebSocket above, before checking
        # the serial binding. Do not accept it a second time here.
        await _subscribe_local_adb_stream(websocket, device_id, native_serial)
        return

    if conn and conn.is_connected and conn.video_available:
        logger.info("[LIVE DEBUG] Using remote video stream: device_id=%s", device_id)
        await websocket.accept()
        await _stream_remote(websocket, conn)
        return

    # A remote video request must never show an unrelated server USB device.
    if websocket.query_params.get("source") == "device":
        logger.warning("[LIVE DEBUG] Remote device video unavailable: device_id=%s", device_id)
        await websocket.close(code=4404, reason="Device video unavailable")
        return

    if device.connection_type in {"browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"} or not is_operator:
        logger.warning(
            "[LIVE DEBUG] Video access denied: device_id=%s connection_type=%s is_operator=%s",
            device_id,
            device.connection_type,
            is_operator,
        )
        await websocket.close(code=4403, reason="Video access denied")
        return

    await websocket.accept()
    source = websocket.query_params.get("source", "auto")
    serials = await asyncio.to_thread(_adb_serials)
    serial = _resolve_adb_serial(serials, source, websocket.query_params.get("serial"))
    if not serial:
        logger.warning("[LIVE DEBUG] No available ADB serial: device_id=%s", device_id)
        await websocket.close(code=4404, reason="No ADB device available")
        return

    logger.info(
        "[LIVE DEBUG] Starting fallback ADB stream: device_id=%s serial=%s",
        device_id,
        serial,
    )
    await _stream_local_adb(websocket, serial)


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
