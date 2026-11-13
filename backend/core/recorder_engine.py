"""Unified recorder event/session core.

All recorder adapters (Portal, web actions, UIAutomator, DOM and vision) should
append events through this module. The event envelope is versioned and keeps
legacy Portal fields at the top level so existing clients remain compatible.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1
SUPPORTED_SOURCES = {"portal_touch", "web_action", "uiautomator", "dom", "vision"}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _action_for(kind: str) -> str:
    """Normalize adapter-specific event names without discarding the raw kind."""
    aliases = {
        "click": "tap",
        "touch": "tap",
        "tap_element": "tap",
        "input": "input_text",
        "text": "input_text",
        "type": "input_text",
        "scroll": "scroll",
        "swipe": "swipe",
        "long_press": "long_press",
    }
    return aliases.get((kind or "").lower(), (kind or "unknown").lower())


@dataclass
class RecordingSession:
    device_id: str
    session_id: str
    started_at_ms: int
    source: str
    recording: bool = True
    events: List[Dict[str, Any]] = field(default_factory=list)
    stopped_at_ms: Optional[int] = None


class RecorderEngine:
    """In-memory per-device recorder session manager.

    Device sessions are isolated. A lock protects Portal callbacks and HTTP
    polling from racing while appending or stopping a session.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, RecordingSession] = {}
        self._lock = threading.RLock()

    def start(self, device_id: str, source: str = "portal_touch") -> RecordingSession:
        if source not in SUPPORTED_SOURCES:
            raise ValueError(f"Unsupported recorder source: {source}")
        with self._lock:
            current = self._sessions.get(device_id)
            if current and current.recording:
                raise RuntimeError(f"Recorder already active for device {device_id}")
            session = RecordingSession(
                device_id=device_id,
                session_id=str(uuid.uuid4()),
                started_at_ms=_now_ms(),
                source=source,
            )
            self._sessions[device_id] = session
            return session

    def append(
        self,
        device_id: str,
        payload: Dict[str, Any],
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(device_id)
            if not session or not session.recording:
                return None

            raw = dict(payload or {})
            raw_kind = str(raw.get("kind") or raw.get("action") or raw.get("type") or "unknown")
            action = _action_for(raw_kind)
            seq = len(session.events)
            received_at_ms = _now_ms()
            client_ts = raw.get("ts")
            try:
                timestamp_ms = int(float(client_ts) * 1000) if client_ts is not None else received_at_ms
            except (TypeError, ValueError, OverflowError):
                timestamp_ms = received_at_ms

            target = {
                "text": raw.get("text") or "",
                "content_description": raw.get("contentDescription") or raw.get("content_description") or "",
                "resource_id": raw.get("resourceId") or raw.get("resource_id") or "",
                "class_name": raw.get("className") or raw.get("class_name") or "",
                "role": raw.get("role") or "",
                "css_selector": raw.get("cssSelector") or raw.get("css_selector") or "",
                "test_id": raw.get("testId") or raw.get("test_id") or "",
            }
            coordinates = None
            if any(raw.get(k) is not None for k in ("x", "y", "x1", "y1", "x2", "y2")):
                coordinates = {
                    key: raw[key] for key in ("x", "y", "x1", "y1", "x2", "y2")
                    if raw.get(key) is not None
                }
                default_space = {
                    "portal_touch": "device_px",
                    "uiautomator": "device_px",
                    "web_action": "model_image_px",
                    "dom": "css_px",
                    "vision": "screenshot_px",
                }.get(source or session.source, "unknown")
                coordinates["space"] = (
                    raw.get("coordinateSpace")
                    or raw.get("coordinate_space")
                    or default_space
                )

            # Preserve the old fields (kind/text/x/y/ts/...) for older frontends.
            event = {
                **raw,
                "schema_version": SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "session_id": session.session_id,
                "device_id": device_id,
                "source": source or session.source,
                "sequence": seq,
                "seq": seq,
                "timestamp_ms": timestamp_ms,
                "received_at_ms": received_at_ms,
                "action": action,
                "target": target,
                "coordinates": coordinates,
            }
            session.events.append(event)
            return dict(event)

    def stop(self, device_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(device_id)
            if not session:
                return []
            if session.recording:
                session.recording = False
                session.stopped_at_ms = _now_ms()
            return [dict(event) for event in session.events]

    def events(self, device_id: str, since: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(device_id)
            if not session:
                return []
            return [dict(event) for event in session.events[max(0, since):]]

    def is_recording(self, device_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(device_id)
            return bool(session and session.recording)

    def session_info(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(device_id)
            if not session:
                return None
            return {
                "session_id": session.session_id,
                "source": session.source,
                "started_at_ms": session.started_at_ms,
                "stopped_at_ms": session.stopped_at_ms,
                "recording": session.recording,
                "event_count": len(session.events),
            }


recorder_engine = RecorderEngine()
