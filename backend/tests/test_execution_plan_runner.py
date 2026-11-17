"""Unit tests for versioned execution-plan step dispatch helpers."""
import pytest

from core.execution_plan_runner import build_replay_step, verify_plan_condition


def test_build_replay_step_maps_semantic_locator_and_legacy_coordinate():
    step = {
        "action": "tap",
        "args": {},
        "locator": {"resource_id": "com.demo:id/search", "text": "搜索"},
        "fallback": {"coordinates": {"x": 120, "y": 240}, "coordinate_space": "device_px"},
    }
    replay = build_replay_step(step)
    assert replay["kind"] == "tap"
    assert replay["resourceId"] == "com.demo:id/search"
    assert replay["text"] == "搜索"
    assert replay["x"] == 120 and replay["y"] == 240
    assert replay["coordinate_space"] == "device_px"


def test_build_replay_step_does_not_change_unspecified_coordinate_space():
    replay = build_replay_step({
        "action": "tap",
        "args": {},
        "locator": {},
        "fallback": {"coordinates": {"x": 10, "y": 20}},
    })
    assert replay["x"] == 10 and replay["y"] == 20
    assert "coordinate_space" not in replay


def test_build_replay_step_normalizes_system_launch_arguments():
    replay = build_replay_step({
        "action": "start_app",
        "args": {"package_name": "com.demo.app", "activity": ".Main"},
    })
    assert replay["kind"] == "start_app"
    assert replay["package"] == "com.demo.app"
    assert replay["activity"] == ".Main"


class FakeDevice:
    async def get_ui_state(self):
        return "", [
            {"text": "搜索结果", "visible": True},
            {"text": "隐藏文字", "visible": False},
        ]

    async def current_package(self):
        return "com.demo.app"


@pytest.mark.asyncio
async def test_text_present_verification_uses_visible_ui_elements():
    assert await verify_plan_condition(
        FakeDevice(), {"type": "text_present", "text": "搜索结果"}
    ) is True
    assert await verify_plan_condition(
        FakeDevice(), {"type": "text_present", "text": "不存在"}
    ) is False


@pytest.mark.asyncio
async def test_package_foreground_verification():
    assert await verify_plan_condition(
        FakeDevice(), {"type": "package_foreground", "package": "com.demo.app"}
    ) is True
