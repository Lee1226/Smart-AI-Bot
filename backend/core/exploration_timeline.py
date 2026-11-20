"""Derive a readable scene graph from the ordered exploration event stream."""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _snapshot_identity(snapshot: dict[str, Any]) -> str:
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    stable = []
    for item in elements[:120]:
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("resourceId") or "").strip()
        text = str(item.get("text") or item.get("contentDescription") or "").strip()
        if resource_id or text:
            stable.append((resource_id, text))
    if stable:
        payload = repr(sorted(stable))
    else:
        ui_text = re.sub(r"\s+", " ", str(snapshot.get("ui_text") or "")).strip()
        payload = ui_text[:4000]
    return hashlib.sha1(payload.encode("utf-8")).hexdigest() if payload else ""


def _scene_title(snapshot: dict[str, Any], fallback: str) -> str:
    lines = [line.strip() for line in str(snapshot.get("ui_text") or "").splitlines() if line.strip()]
    for line in lines:
        if len(line) >= 2 and not line.startswith(("resource-id", "class:", "bounds:")):
            return line[:72]
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    for item in elements[:120]:
        if isinstance(item, dict):
            label = str(item.get("text") or item.get("contentDescription") or "").strip()
            if label:
                return label[:72]
    return fallback


def build_scene_timeline(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Group repeated observations into scene visits and connect action transitions.

    This is a derived view only: the original ordered events remain unchanged.
    """
    scenes: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def ensure_scene(snapshot: dict[str, Any], fallback: str) -> dict[str, Any] | None:
        nonlocal current
        identity = _snapshot_identity(snapshot)
        if not identity:
            return current
        if current is None or current["identity"] != identity:
            current = {
                "id": f"scene-{len(scenes) + 1}",
                "title": _scene_title(snapshot, fallback),
                "identity": identity,
                "first_sequence": 0,
                "last_sequence": 0,
                "observation_count": 0,
                "event_ids": [],
                "actions": [],
                "screenshot": "",
            }
            scenes.append(current)
        return current

    for step in sorted(steps, key=lambda item: int(item.get("sequence") or 0)):
        event_type = step.get("event_type")
        sequence = int(step.get("sequence") or 0)
        if event_type == "observation":
            scene = ensure_scene(step.get("page_after") or {}, f"页面 {len(scenes) + 1}")
            if scene:
                scene["observation_count"] += 1
                scene["screenshot"] = (step.get("evidence") or {}).get("screenshot", "") or scene["screenshot"]
        elif event_type == "action":
            before = step.get("page_before") or {}
            after = step.get("page_after") or {}
            before_identity = _snapshot_identity(before)
            if before_identity and (current is None or current["identity"] != before_identity):
                current = ensure_scene(before, f"操作前页面 {len(scenes) + 1}")
            source = current
            if source and not source.get("screenshot"):
                source["screenshot"] = (step.get("evidence") or {}).get("before_screenshot", "")
            if source:
                source["actions"].append({
                    "step_id": step.get("id"),
                    "sequence": sequence,
                    "title": step.get("title") or step.get("action") or "设备操作",
                    "action": step.get("action") or "",
                    "status": step.get("status") or "recorded",
                    "duration_ms": int(step.get("duration_ms") or 0),
                })
            after_identity = _snapshot_identity(after)
            if after_identity and (source is None or source["identity"] != after_identity):
                target = ensure_scene(after, f"操作后页面 {len(scenes) + 1}")
                if target:
                    target["screenshot"] = (step.get("evidence") or {}).get("after_screenshot", "") or target.get("screenshot", "")
                if source and target:
                    transitions.append({
                        "id": f"transition-{len(transitions) + 1}",
                        "from_scene": source["id"],
                        "to_scene": target["id"],
                        "action": step.get("action") or step.get("title") or "设备操作",
                        "sequence": sequence,
                        "status": step.get("status") or "recorded",
                        "change_summary": after.get("change_summary") or {},
                    })
        if current and event_type in {"observation", "decision", "action", "tool_result"}:
            current["event_ids"].append(step.get("id"))
            current["last_sequence"] = sequence
            if not current["first_sequence"]:
                current["first_sequence"] = sequence

    for scene in scenes:
        scene.pop("identity", None)
    return {
        "scenes": scenes,
        "transitions": transitions,
        "scene_count": len(scenes),
        "transition_count": len(transitions),
    }
