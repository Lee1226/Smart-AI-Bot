"""Tests for exploration UI change summaries."""
from core.exploration_runner import _image_extension, _summarize_page_change


def test_page_change_reports_added_removed_and_changed_controls():
    before = {
        "ui_text": "首页\n按钮: 设置",
        "elements": [
            {"resourceId": "app:id/settings", "text": "设置", "className": "Button", "clickable": True},
            {"resourceId": "app:id/old", "text": "旧入口", "className": "TextView"},
        ],
    }
    after = {
        "ui_text": "设置页\n按钮: 保存",
        "elements": [
            {"resourceId": "app:id/settings", "text": "设置选项", "className": "Button", "clickable": True},
            {"resourceId": "app:id/save", "text": "保存", "className": "Button"},
        ],
    }

    diff = _summarize_page_change(before, after)

    assert diff["has_changes"] is True
    assert diff["added_count"] == 1
    assert diff["removed_count"] == 1
    assert diff["changed_count"] == 1
    assert diff["added"][0]["resourceId"] == "app:id/save"
    assert diff["removed"][0]["resourceId"] == "app:id/old"
    assert diff["changed"][0]["identity"] == "id:app:id/settings"
    assert "首页" in diff["removed_ui_lines"]
    assert "设置页" in diff["added_ui_lines"]


def test_page_change_uses_semantic_fallback_identity_without_resource_id():
    before = {"elements": [{"className": "Button", "text": "继续", "bounds": [0, 0, 10, 10]}]}
    after = {"elements": [{"className": "Button", "text": "继续", "bounds": [0, 0, 20, 20]}]}

    diff = _summarize_page_change(before, after)

    assert diff["changed_count"] == 1
    assert diff["changed"][0]["fields"] == ["bounds"]


def test_page_change_handles_missing_snapshots():
    diff = _summarize_page_change(None, {"ui_text": "", "elements": []})

    assert diff["has_changes"] is False
    assert diff["label"] == "相邻两次观察未发现明显 UI 结构变化"


def test_image_extension_matches_screenshot_bytes():
    assert _image_extension(b"\xff\xd8\xffpayload") == "jpg"
    assert _image_extension(b"RIFFxxxxWEBPpayload") == "webp"
    assert _image_extension(b"\x89PNG\r\n\x1a\npayload") == "png"
