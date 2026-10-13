"""Dedicated H.264 transport. Stream grants belong to a live device connection.

A bounded queue fails slow viewers rather than dropping reference frames and
silently corrupting H.264. Device tokens never appear in URLs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from ws.portal_ws import DeviceConnection, connected_devices


@dataclass
class VideoSession:
    connection: DeviceConnection
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    provider: WebSocket | None = None


sessions: dict[str, VideoSession] = {}


async def provider_video_endpoint(websocket: WebSocket):
    token = websocket.headers.get('authorization', '').removeprefix('Bearer ')
    stream_id = websocket.headers.get('x-stream-id', '')
    session = sessions.get(stream_id)
    await websocket.accept()
    if (session is None or not token or token != session.connection.token
            or connected_devices.get(session.connection.device_id) is not session.connection
            or not session.connection.is_connected or session.provider is not None):
        await websocket.close(code=4403)
        return
    session.provider = websocket
    session.ready.set()
    try:
        while True:
            chunk = await websocket.receive_bytes()
            if not chunk or len(chunk) > 256 * 1024:
                await websocket.close(code=4400)
                return
            if sessions.get(stream_id) is not session:
                return
            try:
                session.queue.put_nowait(chunk)
            except asyncio.QueueFull:
                await websocket.close(code=4408, reason='Video consumer too slow')
                return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        # Discard an incomplete backlog on disconnect: the viewer must reconnect
        # with a fresh codec header and IDR, never continue a broken GOP.
        while not session.queue.empty():
            session.queue.get_nowait()
        session.queue.put_nowait(None)
