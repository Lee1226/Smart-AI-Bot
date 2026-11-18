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



@pytest.mark.asyncio
async def test_replay_step_ignores_class_name_only_and_uses_coordinates():
    from core.test_runner import _replay_step

    class Device:
        def __init__(self):
            self.tapped = None

        async def tap(self, x, y):
            self.tapped = ("image", x, y)

        async def tap_absolute(self, x, y):
            self.tapped = ("device", x, y)

        async def tap_element_by_class(self, class_name):
            raise AssertionError("ClassName must not be used as a standalone tap locator")

    logs = []
    async def emit(message):
        logs.append(message)

    device = Device()
    result = await _replay_step(
        device,
        {"kind": "tap", "className": "LinearLayout", "x": 625, "y": 2485, "coordinate_space": "device_px"},
        emit,
    )
    assert device.tapped == ("device", 625, 2485)
    assert result == "tap (625,2485)"
    assert any("忽略 ClassName=LinearLayout" in line for line in logs)
    assert any("使用坐标兜底" in line for line in logs)


@pytest.mark.asyncio
async def test_replay_step_uses_text_after_ambiguous_resource_id():
    from core.test_runner import _replay_step

    class Device:
        def __init__(self):
            self.calls = []

        async def tap_element_by_id(self, resource_id):
            self.calls.append(("id", resource_id))
            raise ValueError("Ambiguous resourceId locator: 3 visible candidates")

        async def tap_element_by_text(self, text):
            self.calls.append(("text", text))
            return "Tapped 千问"

    logs = []
    async def emit(message):
        logs.append(message)

    device = Device()
    result = await _replay_step(
        device,
        {"kind": "tap", "resourceId": "tab_item", "text": "千问", "x": 625, "y": 2485},
        emit,
    )
    assert result == "Tapped 千问"
    assert device.calls == [("id", "tab_item"), ("text", "千问")]
    assert any("Resource ID 定位未采用" in line for line in logs)



def test_build_replay_step_preserves_force_coordinate_flag():
    replay = build_replay_step({
        "action": "tap",
        "force_coordinate": True,
        "locator": {"resource_id": "com.demo:id/button"},
        "fallback": {"coordinates": {"x": 30, "y": 60}, "coordinate_space": "device_px"},
    })
    assert replay["force_coordinate"] is True
    assert replay["x"] == 30 and replay["y"] == 60


@pytest.mark.asyncio
async def test_replay_step_force_coordinate_skips_semantic_locator():
    from core.test_runner import _replay_step

    class Device:
        def __init__(self):
            self.tapped = None

        async def tap(self, x, y):
            self.tapped = ("image", x, y)

        async def tap_absolute(self, x, y):
            self.tapped = ("device", x, y)

        async def tap_element_by_id(self, resource_id):
            raise AssertionError("semantic lookup must be skipped")

        async def tap_element_by_text(self, text):
            raise AssertionError("semantic lookup must be skipped")

    logs = []
    async def emit(message):
        logs.append(message)

    device = Device()
    result = await _replay_step(device, {
        "kind": "tap", "force_coordinate": True,
        "resourceId": "com.demo:id/button", "text": "继续",
        "x": 30, "y": 60, "coordinate_space": "device_px",
    }, emit)
    assert device.tapped == ("device", 30, 60)
    assert result == "tap coordinate (30,60)"
    assert any("强制坐标点击" in line for line in logs)


@pytest.mark.asyncio
async def test_replay_step_force_coordinate_requires_coordinates():
    from core.test_runner import _replay_step

    class Device:
        async def tap_element_by_id(self, resource_id):
            raise AssertionError("must not fall back to UI lookup")

    async def emit(_message):
        pass

    with pytest.raises(ValueError, match="强制坐标点击"):
        await _replay_step(Device(), {
            "kind": "tap", "force_coordinate": True,
            "resourceId": "com.demo:id/button",
        }, emit)
