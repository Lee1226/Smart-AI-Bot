"""
WebSocket endpoint — Portal Android app connects here (reverse connection).
Portal → Server: Bearer token auth, then sends JSON-RPC responses.
Server → Portal: JSON-RPC requests to control the device.
"""
import asyncio
import anyio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from db.database import AsyncSessionLocal
from db.models import Device

logger = logging.getLogger(__name__)

connected_devices: Dict[str, "DeviceConnection"] = {}


@dataclass
class DeviceConnection:
    ws: WebSocket
    device_id: str
    device_name: str
    token: str
    connection_type: str = "portal"
    pending: Dict[str, asyncio.Future] = field(default_factory=dict)
    is_connected: bool = True
    video_available: bool = False

    @property
    def platform(self):
        return "harmony" if self.connection_type == "hdc_bridge" else "android"


def _header_utf8(value: str) -> str:
    """Recover a UTF-8 header value that Starlette decoded as latin-1.

    HTTP headers are latin-1/ASCII on the wire, so a UTF-8 name (e.g. Chinese)
    arrives mojibaked. Best-effort re-decode; returns the original on failure
    (and stays mangled if the client already lost the bytes to '?')."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


async def portal_websocket_endpoint(websocket: WebSocket):
    headers = dict(websocket.headers)
    authorization = headers.get("authorization", "")
    device_name = _header_utf8(headers.get("x-device-name", "Unknown"))

    if not authorization.startswith("Bearer "):
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    token = authorization.removeprefix("Bearer ").strip()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Device).where(Device.token == token))
        device_row = result.scalar_one_or_none()

    if device_row is None or device_row.connection_type != "portal":
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    await serve_device_connection(websocket, device_row, token, device_name)


async def browser_usb_websocket_endpoint(websocket: WebSocket):
    # Browser cannot send Authorization headers. Authenticate in the first frame,
    # never put a persistent device credential in URLs/logs.
    await websocket.accept()
    try:
        hello = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
        if not isinstance(hello, dict) or hello.get("type") != "auth":
            raise ValueError("Invalid handshake")
        token = hello.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing token")
        async with AsyncSessionLocal() as session:
            device_row = (await session.execute(select(Device).where(
                Device.token == token, Device.connection_type == "browser_usb"
            ))).scalar_one_or_none()
        if device_row is None:
            raise ValueError("Unauthorized")
    except (ValueError, asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await serve_device_connection(websocket, device_row, token, device_row.name, browser=True)


async def adb_bridge_websocket_endpoint(websocket: WebSocket):
    authorization = websocket.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    async with AsyncSessionLocal() as session:
        device = (await session.execute(select(Device).where(
            Device.token == token, Device.connection_type == "adb_bridge"
        ))).scalar_one_or_none() if token else None
    await websocket.accept()
    if device is None:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await serve_device_connection(websocket, device, token, device.name, browser=True)


async def hdc_bridge_websocket_endpoint(websocket: WebSocket):
    authorization = websocket.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    async with AsyncSessionLocal() as session:
        device = (await session.execute(select(Device).where(
            Device.token == token, Device.connection_type == "hdc_bridge"
        ))).scalar_one_or_none() if token else None
    await websocket.accept()
    if device is None:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await serve_device_connection(websocket, device, token, device.name, browser=True)


async def serve_device_connection(websocket: WebSocket, device_row: Device,
                                  token: str, device_name: str, browser: bool = False):
    db_device_id = device_row.id
    previous = connected_devices.get(db_device_id)
    if previous:
        if browser:
            await websocket.close(code=4009, reason="Device already connected")
            return
        # Preserve Portal reconnect semantics, but explicitly close the old
        # transport so its outstanding actions cannot linger.
        connected_devices.pop(db_device_id, None)
        await previous.ws.close(code=4000, reason="Reconnected")
    conn = DeviceConnection(ws=websocket, device_id=db_device_id,
                            device_name=device_name, token=token, connection_type=device_row.connection_type)
    connected_devices[db_device_id] = conn

    ping_fail_count = 0
    try:
        # The device name is owned by the backend (set when the device is created /
        # renamed in the web UI). Never overwrite a real DB name with the header
        # value — it travels over latin-1 HTTP headers and mangles non-ASCII names
        # to "??". Only fall back to the header for a device that has no real name.
        has_real_name = bool(device_row.name) and device_row.name != "Unknown"
        resolved_name = device_row.name if has_real_name else (device_name or device_row.name)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Device).where(Device.token == token)
                .values(status="online", last_seen=datetime.utcnow(), name=resolved_name)
            )
            await session.commit()

        logger.info("Device connected: %s (%s)", db_device_id, device_name)

        if browser:
            await websocket.send_json({"type": "ready", "device_id": db_device_id})
        while True:
            # Receive with a long-ish timeout to allow slow RPCs (e.g. 25s
            # screenshot on Unity pages) to complete without us triggering a
            # spurious liveness check.
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                _handle_message(conn, raw)
            except asyncio.TimeoutError:
                # Silent for 60s. If device is mid-RPC, skip the ping —
                # pending RPCs prove the device is alive (we're waiting on it).
                if conn.pending:
                    logger.debug(
                        "Skipping ping for %s — %d pending RPC(s)",
                        db_device_id, len(conn.pending),
                    )
                    continue
                # No pending RPCs — device is idle. Send app-level ping.
                try:
                    await asyncio.wait_for(
                        websocket.send_text('{"id":"__ping__","method":"ping","params":{}}'),
                        timeout=5.0,
                    )
                    ping_fail_count = 0
                except Exception:
                    ping_fail_count += 1
                    logger.warning(
                        "Ping %d failed for %s", ping_fail_count, db_device_id
                    )
                    # Only disconnect after 2 consecutive ping failures (~120s silence)
                    if ping_fail_count >= 2:
                        logger.warning("Disconnecting %s after %d failed pings",
                                       db_device_id, ping_fail_count)
                        break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Portal WS error for %s: %s", db_device_id, e)
    finally:
        conn.is_connected = False
        is_current = connected_devices.get(db_device_id) is conn
        if is_current:
            connected_devices.pop(db_device_id, None)
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Device disconnected"))
        conn.pending.clear()
        with anyio.CancelScope(shield=True):
            if is_current:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(Device).where(Device.token == token)
                        .values(status="offline", last_seen=datetime.utcnow())
                    )
                    await session.commit()
        logger.info("Device disconnected: %s", db_device_id)


def _handle_message(conn: DeviceConnection, raw: str):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    if not isinstance(msg, dict):
        return

    # Optional capability announcement; old clients remain screenshot-only.
    if msg.get("type") == "capabilities":
        conn.video_available = msg.get("video") is True
        return
    # Record-event push from the Portal (NOT a JSON-RPC response — no "id").
    # The app streams real user interactions (tap/scroll/input + coordinates)
    # while the recorder is active; route them to the recorder session.
    if msg.get("type") == "record_event":
        try:
            from routers.recorder import handle_record_event
            handle_record_event(conn.device_id, msg.get("event") or {})
        except Exception as e:
            logger.warning("record_event handler failed: %s", e)
        return

    msg_id = str(msg.get("id", ""))
    if not msg_id:
        return

    future = conn.pending.pop(msg_id, None)
    if future is None or future.done():
        return

    # Portal reports failures as {"status": "error", "result": "<message>"} —
    # NOT as a JSON-RPC "error" field. Honour the status flag first, otherwise a
    # device-side error string is delivered as a successful result and crashes
    # downstream (e.g. base64-decoding "Screenshot failed — …").
    if msg.get("status") == "error":
        err = msg.get("result") or msg.get("error") or "device reported an error"
        if isinstance(err, dict):
            err = err.get("message", str(err))
        future.set_exception(RuntimeError(str(err)))
    elif "result" in msg:
        future.set_result(msg["result"])
    elif "error" in msg:
        err = msg["error"]
        if isinstance(err, dict):
            err = err.get("message", str(err))
        future.set_exception(RuntimeError(str(err)))
    else:
        future.set_result(None)


async def send_rpc(conn: DeviceConnection, method: str,
                   params: Optional[Dict[str, Any]] = None,
                   timeout: float = 30.0) -> Any:
    call_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    conn.pending[call_id] = future

    payload = json.dumps({"id": call_id, "method": method, "params": params or {}})
    try:
        # Wrap send_text in a timeout: a silent TCP half-open state can cause
        # send_text to hang for several minutes waiting for kernel retransmission.
        await asyncio.wait_for(conn.ws.send_text(payload), timeout=10.0)
    except asyncio.TimeoutError:
        conn.pending.pop(call_id, None)
        conn.is_connected = False
        raise RuntimeError(f"RPC '{method}' send timeout — connection appears dead")
    except Exception as e:
        conn.pending.pop(call_id, None)
        raise RuntimeError(f"Failed to send RPC '{method}': {e}") from e

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"RPC '{method}' timed out after {timeout}s")
    finally:
        conn.pending.pop(call_id, None)
