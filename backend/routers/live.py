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
_H264_BOOTSTRAP_START = object()
_H264_BOOTSTRAP_END = object()

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


async def subscribe_local_adb_recorder(device_id: str, serial: str):
    """Subscribe a task-owned recorder to timestamped audio/video packets.

    Recorder subscriptions are independent of browser H.264 queues. Releasing
    this subscription never closes a preview; the producer stops only after
    the final preview and recorder consumer leaves.
    """
    consumer_id = "rec-" + uuid.uuid4().hex
    lock = _local_adb_hub_lock(device_id)
    # Bound backlog to about 4.3s of 30fps video and 5.1s of 20ms audio.
    # Overflow fails only this recording; it never blocks preview or capture.
    video_queue = asyncio.Queue(maxsize=128)
    audio_queue = asyncio.Queue(maxsize=256)

    while True:
        wait_for_stopping_hub = None
        async with lock:
            hub = _local_adb_hubs.get(device_id)
            if hub is not None and hub.get("stopping"):
                wait_for_stopping_hub = hub.get("task")
            else:
                if hub is None:
                    hub = {
                        "ready": asyncio.Event(),
                        "subscribers": {},
                        "websockets": {},
                        "recorders": {},
                        "session": None,
                        "task": None,
                        "error": None,
                        "stream": _H264AnnexBStream(),
                        "bootstrap_sent": {},
                        "stopping": False,
                    }
                    _local_adb_hubs[device_id] = hub
                    hub["task"] = asyncio.create_task(_run_local_adb_hub(device_id, serial, hub))

                # Register atomically with hub lookup/creation so a concurrent
                # final-viewer disconnect cannot stop the producer in between.
                hub["recorders"][consumer_id] = {
                    "video": video_queue,
                    "audio": audio_queue,
                    "failed": False,
                }
        if wait_for_stopping_hub is None:
            break
        if not wait_for_stopping_hub.done():
            await asyncio.gather(wait_for_stopping_hub, return_exceptions=True)

    try:
        await asyncio.wait_for(hub["ready"].wait(), timeout=20)
        if hub["error"]:
            raise RuntimeError(f"Scrcpy media unavailable: {hub['error']}")
    except Exception:
        await release_local_adb_recorder(device_id, hub, consumer_id)
        raise

    async def release():
        await release_local_adb_recorder(device_id, hub, consumer_id)

    logger.info("[LIVE HUB] recorder subscribed: device_id=%s consumer=%s", device_id, consumer_id)
    return video_queue, audio_queue, hub.get("media_metadata", {}), release


async def release_local_adb_recorder(device_id: str, hub: dict, consumer_id: str) -> None:
    lock = _local_adb_hub_lock(device_id)
    task_to_stop = None
    async with lock:
        hub["subscribers"].pop(consumer_id, None)
        hub.get("websockets", {}).pop(consumer_id, None)
        hub.get("bootstrap_sent", {}).pop(consumer_id, None)
        hub.get("recorders", {}).pop(consumer_id, None)
        if not hub["subscribers"] and not hub.get("recorders") and _local_adb_hubs.get(device_id) is hub:
            hub["stopping"] = True
            task_to_stop = hub["task"]
    if task_to_stop and not task_to_stop.done():
        task_to_stop.cancel()
        await asyncio.gather(task_to_stop, return_exceptions=True)


async def _run_local_adb_hub(device_id: str, serial: str, hub: dict) -> None:
    from agent.scrcpy_media import ScrcpyMediaSession

    session = ScrcpyMediaSession(_adb_path(), serial)
    hub["session"] = session
    stream = hub["stream"]
    lock = _local_adb_hub_lock(device_id)
    total_bytes = 0
    total_nals = 0
    last_stats = asyncio.get_running_loop().time()

    def enqueue_recorder_packet_locked(kind: str, packet) -> None:
        """Fan out a packet while the device hub lock is already held."""
        for recorder_id, recorder in list(hub.get("recorders", {}).items()):
            if recorder.get("failed"):
                continue
            queue = recorder[kind]
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                recorder["failed"] = True
                for target in (recorder["video"], recorder["audio"]):
                    while not target.empty():
                        try:
                            target.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    target.put_nowait({"error": f"{kind} recorder queue overflow"})
                logger.warning(
                    "[LIVE HUB] recorder dropped: queue full device_id=%s recorder=%s stream=%s",
                    device_id, recorder_id, kind,
                )

    async def publish(data: bytes, idle_flush: bool = False, recorder_packet=None) -> None:
        nonlocal total_nals, last_stats
        if not data and not idle_flush and recorder_packet is None:
            return
        close_viewers = []
        async with lock:
            # Parser/cache update and subscriber fan-out share one lock. This
            # makes the bootstrap snapshot and following live bytes contiguous.
            batch = stream.feed(data) if data else stream.flush()
            if recorder_packet is not None:
                enqueue_recorder_packet_locked("video", recorder_packet)
            if not batch and not idle_flush:
                return
            total_nals += batch.count(b"\x00\x00\x00\x01")
            # Avoid copying the cached GOP on every packet once all viewers
            # have received their initial keyframe.
            needs_bootstrap = any(
                not hub["bootstrap_sent"].get(viewer_id, False)
                for viewer_id in hub["subscribers"]
            )
            bootstrap = stream.bootstrap if needs_bootstrap else b""
            for viewer_id, queue in list(hub["subscribers"].items()):
                try:
                    just_bootstrapped = False
                    if not hub["bootstrap_sent"].get(viewer_id, False):
                        if not bootstrap:
                            continue
                        while not queue.empty():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        # Send the complete cached GOP, flush its final access unit,
                        # then mark the exact transition to live frames. All three
                        # queue entries are inserted under the hub lock so live data
                        # can never overtake the bootstrap boundary.
                        queue.put_nowait(_H264_BOOTSTRAP_START)
                        queue.put_nowait(bootstrap + b"\x00\x00\x00\x01")
                        queue.put_nowait(None)
                        queue.put_nowait(_H264_BOOTSTRAP_END)
                        hub["bootstrap_sent"][viewer_id] = True
                        just_bootstrapped = True
                        logger.info(
                            "[LIVE HUB] viewer bootstrapped: device_id=%s viewer=%s gop_bytes=%d idr_count=%d",
                            device_id, viewer_id, len(bootstrap), stream.idr_count,
                        )
                    elif batch:
                        queue.put_nowait(batch + b"\x00\x00\x00\x01")

                    # Flush ordinary live access units only at an encoder-idle
                    # boundary. The bootstrap path already inserted its own flush.
                    if idle_flush and hub["bootstrap_sent"].get(viewer_id, False) and not just_bootstrapped:
                        queue.put_nowait(None)
                except asyncio.QueueFull:
                    hub["subscribers"].pop(viewer_id, None)
                    ws = hub["websockets"].pop(viewer_id, None)
                    hub["bootstrap_sent"].pop(viewer_id, None)
                    if ws is not None:
                        close_viewers.append(ws)
                    else:
                        # A slow recorder must not back-pressure previews or
                        # keep a dead subscription attached to the producer.
                        while not queue.empty():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        queue.put_nowait(None)
                    logger.warning("[LIVE HUB] slow consumer dropped: device_id=%s consumer=%s", device_id, viewer_id)

            now = asyncio.get_running_loop().time()
            if now - last_stats >= 5:
                logger.info(
                    "[H264] stream stats: device_id=%s bytes=%d nals=%d idr=%d gop_bytes=%d idle_flush=%s viewers=%d",
                    device_id, total_bytes, total_nals, stream.idr_count,
                    len(stream.gop), idle_flush, len(hub["subscribers"]),
                )
                last_stats = now
        for ws in close_viewers:
            try:
                await ws.close(code=4408, reason="Viewer too slow; reconnect for a fresh keyframe")
            except Exception:
                pass

    async def publish_recorder_packet(kind: str, packet) -> None:
        async with lock:
            enqueue_recorder_packet_locked(kind, packet)

    async def read_video() -> None:
        nonlocal total_bytes
        while True:
            packets = await session.read_video_packets()
            if packets is None:
                await publish(b"", idle_flush=True)
                continue
            if not packets:
                continue
            for packet in packets:
                total_bytes += len(packet.payload)
                # Preview and recorder fan-out share one lock acquisition.
                await publish(packet.payload, recorder_packet=packet)

    media_tasks: set[asyncio.Task] = set()

    async def read_audio() -> None:
        try:
            while True:
                packets = await session.read_audio_packets()
                if packets is None:
                    continue
                if not packets:
                    continue
                for packet in packets:
                    await publish_recorder_packet("audio", packet)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Audio failure is reported to the recorder only. The video
            # producer and browser preview must remain operational.
            logger.warning(
                "[LIVE HUB] audio unavailable; video preview continues: "
                "device_id=%s serial=%s error=%s",
                device_id, serial, exc, exc_info=True,
            )
            async with lock:
                for recorder_id, recorder in list(hub.get("recorders", {}).items()):
                    recorder["failed"] = True
                    for target in (recorder["video"], recorder["audio"]):
                        while not target.empty():
                            try:
                                target.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        try:
                            target.put_nowait({"error": f"audio capture failed: {exc}"})
                        except asyncio.QueueFull:
                            pass

    try:
        await session.start()
        hub["media_metadata"] = {
            "video": session.video_metadata,
            "audio_codec_id": session.audio_codec_id,
            "audio_error": session.audio_error,
        }
        hub["ready"].set()
        logger.info(
            "[LIVE HUB] A/V producer ready: device_id=%s serial=%s video=%s audio_codec=%s audio_error=%s",
            device_id, serial, session.video_metadata, session.audio_codec_id, session.audio_error,
        )
        video_task = asyncio.create_task(read_video(), name=f"scrcpy-video-{device_id}")
        audio_task = asyncio.create_task(read_audio(), name=f"scrcpy-audio-{device_id}")
        media_tasks = {video_task, audio_task}
        done, pending = await asyncio.wait(
            media_tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        for task in done:
            exception = task.exception()
            if exception:
                raise exception
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        hub["error"] = str(exc)
        # Mark the failed producer unavailable before cleanup. New subscribers
        # must wait for this task to finish instead of attaching to a dead hub.
        async with lock:
            hub["stopping"] = True
        process = getattr(session, "server_process", None)
        process_state = None if process is None else {
            "pid": process.pid,
            "returncode": process.poll(),
        }
        socket_state = {
            "video_connected": getattr(session, "video_socket", None) is not None
                and session.video_socket.fileno() >= 0,
            "audio_connected": getattr(session, "audio_socket", None) is not None
                and session.audio_socket.fileno() >= 0,
            "audio_error": getattr(session, "audio_error", None),
            "fallback": getattr(session, "_video_only_fallback", None) is not None,
        }
        recent_server_logs = list(getattr(session, "server_logs", []))[-20:]
        logger.error(
            "Local ADB Scrcpy producer crashed: device_id=%s serial=%s scid=%s "
            "error=%s process=%s sockets=%s server_logs=%s",
            device_id, serial, getattr(session, "scid", None), exc,
            process_state, socket_state, recent_server_logs,
            exc_info=True,
        )
    finally:
        for task in media_tasks:
            if not task.done():
                task.cancel()
        if media_tasks:
            await asyncio.gather(*media_tasks, return_exceptions=True)
        hub["ready"].set()
        async with lock:
            for recorder in hub.get("recorders", {}).values():
                for target in (recorder["video"], recorder["audio"]):
                    while not target.empty():
                        try:
                            target.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    try:
                        target.put_nowait({"error": "Scrcpy media hub stopped before recording drained"})
                    except asyncio.QueueFull:
                        pass
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
            hub.get("recorders", {}).clear()
            hub["bootstrap_sent"].clear()
        # A producer crash is transient from the viewer perspective: close with
        # 1012 so the frontend reconnects and creates a fresh Scrcpy hub.
        close_code = 1012 if hub.get("error") else 4404
        close_reason = "Scrcpy producer stopped; reconnect" if hub.get("error") else "Device video producer stopped"
        for ws in viewers:
            try:
                await ws.close(code=close_code, reason=close_reason)
            except Exception:
                pass


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
                    hub = {
                        "ready": asyncio.Event(),
                        "subscribers": {},
                        "websockets": {},
                        "recorders": {},
                        "session": None,
                        "task": None,
                        "error": None,
                        "stream": _H264AnnexBStream(),
                        "bootstrap_sent": {},
                        "stopping": False,
                    }
                    _local_adb_hubs[device_id] = hub
                    hub["task"] = asyncio.create_task(_run_local_adb_hub(device_id, serial, hub))
                queue = asyncio.Queue(maxsize=64)
                bootstrap = hub["stream"].bootstrap
                if bootstrap:
                    queue.put_nowait(_H264_BOOTSTRAP_START)
                    queue.put_nowait(bootstrap + b"\x00\x00\x00\x01")
                    queue.put_nowait(None)  # flush the final cached access unit
                    queue.put_nowait(_H264_BOOTSTRAP_END)
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
        # Scrcpy startup may spend up to 20s connecting and another 20s
        # waiting for the first framed video packet. Do not reject a healthy
        # but slow start before that startup budget has elapsed.
        await asyncio.wait_for(hub["ready"].wait(), timeout=70)
        if hub["error"]:
            await websocket.close(code=1012, reason="Scrcpy producer failed; reconnect")
            return
        while True:
            chunk = await queue.get()
            if chunk is _H264_BOOTSTRAP_START:
                await websocket.send_text("h264_bootstrap_start")
            elif chunk is _H264_BOOTSTRAP_END:
                await websocket.send_text("h264_bootstrap_end")
            elif chunk is None:
                await websocket.send_text("h264_flush")
            else:
                await websocket.send_bytes(chunk)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError, OSError) as exc:
        logger.info("[LIVE HUB] viewer send ended: device_id=%s viewer=%s error=%s",
                    device_id, viewer_id, exc)
    except asyncio.TimeoutError:
        try:
            await websocket.close(code=1012, reason="Scrcpy startup timed out; reconnect")
        except Exception:
            pass
    finally:
        task_to_stop = None
        async with lock:
            hub["subscribers"].pop(viewer_id, None)
            hub["websockets"].pop(viewer_id, None)
            hub.get("bootstrap_sent", {}).pop(viewer_id, None)
            if not hub["subscribers"] and not hub.get("recorders") and _local_adb_hubs.get(device_id) is hub:
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
