"""Tests for durable recorder event evidence and backward compatibility."""
import json

from routers.recorder import RecordedStep, SaveRequest, _to_replay_steps
from core.recorder_engine import sanitize_events_for_storage


def test_save_request_keeps_legacy_clients_compatible():
    request = SaveRequest(
        device_id="device-1",
        suite_name="recorded",
        expected="screen updated",
        steps=[RecordedStep(action="tap", args={"x": 12, "y": 34}, description="tap")],
    )
    assert request.events is None


def test_recorder_event_storage_strips_screenshot_bytes_but_keeps_metadata():
    events = [{
        "event_id": "event-1",
        "target": {"text": "登录", "resource_id": "app:id/login"},
        "screenshot_b64": "large-base64-payload",
        "screenshot_captured_at_ms": 1234,
        "before": {"elements": [{"text": "登录", "clickable": True}]},
    }]
    stored = sanitize_events_for_storage(events)
    assert "screenshot_b64" not in stored[0]
    assert stored[0]["screenshot_evidence"] == {
        "captured": True, "captured_at_ms": 1234, "storage": "workbench_only"
    }
    assert stored[0]["target"]["resource_id"] == "app:id/login"
    assert stored[0]["before"]["elements"][0]["clickable"] is True


def test_replay_step_keeps_semantic_locator_and_coordinate_metadata():
    steps = [RecordedStep(
        action="tap",
        args={"x": 100, "y": 200},
        description="tap login",
    )]
    events = [{
        "event_id": "event-42",
        "target": {
            "text": "登录", "resource_id": "app:id/login",
            "class_name": "android.widget.Button", "clickable": True,
        },
        "coordinates": {"space": "device_px", "x": 100, "y": 200},
        "before": {"screen": {"width": 1080, "height": 2400}},
    }]
    replay = json.loads(_to_replay_steps(steps, events))
    assert replay[0]["source_event_id"] == "event-42"
    assert replay[0]["locator"]["resource_id"] == "app:id/login"
    assert replay[0]["locator"]["text"] == "登录"
    assert replay[0]["coordinate_space"] == "device_px"
    assert replay[0]["screen_size"] == {"width": 1080, "height": 2400}


def test_replay_does_not_fabricate_screen_dimensions():
    steps = [RecordedStep(action="tap", args={"x": 1, "y": 2}, description="tap")]
    replay = json.loads(_to_replay_steps(steps, [{"coordinates": {"space": "device_px"}}]))
    assert "screen_size" not in replay[0]
