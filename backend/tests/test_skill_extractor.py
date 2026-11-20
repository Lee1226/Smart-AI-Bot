from core.skill_extractor import extract_skill_draft


def _event(**overrides):
    base = {
        "id": "evt-1", "sequence": 1, "event_type": "action",
        "title": "点击设置", "detail": "", "action": "tap", "status": "completed",
        "action_args": {"x": 100, "y": 200},
        "page_before": {"ui_text": "首页", "elements": [{"resourceId": "app:id/home"}]},
        "page_after": {"ui_text": "设置页", "elements": [{"resourceId": "app:id/settings"}],
                       "change_summary": {"has_changes": True, "label": "进入设置页"}},
        "evidence": {"screenshot": "evidence/001.jpg"},
    }
    base.update(overrides)
    return base


def test_extracts_successful_actions_and_preserves_evidence_and_scope():
    draft = extract_skill_draft(
        {"title": "进入设置", "goal": "打开设置页", "expected_result": "显示设置项",
         "platform": "android", "app_package": "com.example"},
        [_event()],
    )
    assert draft["name"] == "进入设置"
    assert draft["platform"] == "android"
    assert draft["app_package"] == "com.example"
    assert draft["steps"][0]["action"] == "tap"
    assert draft["steps"][0]["args"] == {"x": 100, "y": 200}
    assert draft["steps"][0]["verification"]["available"] is True
    assert draft["evidence"][0]["path"] == "evidence/001.jpg"
    assert draft["status"] == "draft"
    assert draft["safety"]["execution_enabled"] is False


def test_preserves_prompt_and_extracts_public_decisions_observations_and_failures():
    draft = extract_skill_draft(
        {"title": "完成翻译", "goal": "打开翻译并启动面对面翻译",
         "expected_result": "显示正在翻译界面", "platform": "android"},
        [
            _event(id="obs-1", sequence=1, event_type="observation", title="观察首页",
                   action="", page_after={"ui_text": "首页\n随身翻译"},
                   evidence={"screenshot": "evidence/observation_0001.jpg"}),
            _event(id="decision-1", sequence=2, event_type="decision", action="tap",
                   title="AI 选择操作：tap", detail="页面出现随身翻译入口，因此选择进入该功能。",
                   status="recorded", action_args={}),
            _event(id="action-1", sequence=3, action="tap", title="点击随身翻译"),
            _event(id="failed-1", sequence=4, event_type="tool_result", action="tap",
                   status="failed", detail="ERROR: element not clickable"),
        ],
    )
    teaching = draft["recognition"]["teaching"]
    assert teaching["source_prompt"] == "打开翻译并启动面对面翻译"
    assert teaching["expected_outcome"] == "显示正在翻译界面"
    assert teaching["observations"][0]["visible_text"] == "首页\n随身翻译"
    assert teaching["decision_records"][0]["summary"] == "页面出现随身翻译入口，因此选择进入该功能。"
    assert teaching["recovery_experiences"][0]["result"] == "ERROR: element not clickable"
    assert draft["steps"][0]["decision_summary"] == "页面出现随身翻译入口，因此选择进入该功能。"
    assert any("失败的工具调用" in warning for warning in draft["warnings"])


def test_does_not_convert_failed_or_unsupported_actions_into_steps():
    draft = extract_skill_draft(
        {"goal": "探索页面"},
        [
            _event(status="failed"),
            _event(id="evt-2", sequence=2, action="mystery_action"),
        ],
    )
    assert draft["steps"] == []
    assert any("暂不支持" in warning for warning in draft["warnings"])
    assert any("没有可提炼" in warning for warning in draft["warnings"])


def test_flags_sensitive_action_and_missing_post_action_snapshot():
    draft = extract_skill_draft(
        {"goal": "发送消息"},
        [_event(title="点击发送消息", page_after={})],
    )
    assert draft["safety"]["sensitive_action_detected"] is True
    assert draft["safety"]["requires_human_review"] is True
    assert any("人工确认" in warning for warning in draft["warnings"])
    assert any("缺少操作后页面快照" in warning for warning in draft["warnings"])


def test_missing_initial_page_is_explicitly_flagged():
    draft = extract_skill_draft(
        {"goal": "完成流程"},
        [_event(page_before={}, page_after={})],
    )
    assert any("起始页面快照" in warning for warning in draft["warnings"])
    assert draft["steps"][0]["verification"]["available"] is False
