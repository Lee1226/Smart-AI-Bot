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


def test_scene_timeline_groups_repeated_observations_and_links_page_transition():
    from core.exploration_timeline import build_scene_timeline

    steps = [
        {
            "id": "o1", "sequence": 1, "event_type": "observation",
            "page_after": {"ui_text": "首页", "elements": [{"resourceId": "app:id/home", "text": "首页"}]},
        },
        {
            "id": "d1", "sequence": 2, "event_type": "decision",
            "title": "AI 选择操作：swipe", "action": "swipe",
        },
        {
            "id": "o2", "sequence": 3, "event_type": "observation",
            "page_after": {"ui_text": "首页", "elements": [{"resourceId": "app:id/home", "text": "首页"}]},
        },
        {
            "id": "a1", "sequence": 4, "event_type": "action", "action": "tap",
            "title": "tap", "status": "completed", "duration_ms": 120,
            "page_before": {"ui_text": "首页", "elements": [{"resourceId": "app:id/home", "text": "首页"}]},
            "page_after": {"ui_text": "设置页", "elements": [{"resourceId": "app:id/settings", "text": "设置"}],
                           "change_summary": {"label": "新增控件 1 个"}},
        },
        {
            "id": "o3", "sequence": 5, "event_type": "observation",
            "page_after": {"ui_text": "设置页", "elements": [{"resourceId": "app:id/settings", "text": "设置"}]},
        },
    ]

    timeline = build_scene_timeline(steps)

    assert timeline["scene_count"] == 2
    assert timeline["transition_count"] == 1
    assert timeline["scenes"][0]["observation_count"] == 2
    assert timeline["scenes"][0]["event_ids"] == ["o1", "d1", "o2"]
    assert timeline["scenes"][0]["actions"][0]["action"] == "tap"
    assert timeline["transitions"][0]["from_scene"] == "scene-1"
    assert timeline["transitions"][0]["to_scene"] == "scene-2"
    assert timeline["transitions"][0]["change_summary"]["label"] == "新增控件 1 个"


def test_scene_timeline_keeps_unknown_state_events_without_inventing_scene():
    from core.exploration_timeline import build_scene_timeline

    timeline = build_scene_timeline([
        {"id": "d1", "sequence": 1, "event_type": "decision", "title": "未观察页面"},
        {"id": "r1", "sequence": 2, "event_type": "tool_result", "title": "结果"},
    ])

    assert timeline["scenes"] == []
    assert timeline["transitions"] == []
    assert timeline["scene_count"] == 0
