from pathlib import Path

from core.skill_validator import validate_skill_structure


def _skill():
    return {
        "platform": "android",
        "app_package": "com.example",
        "steps": [{
            "action": "tap", "args": {"x": 100, "y": 200}, "wait_after_ms": 500,
            "verification": {"available": True, "type": "page_snapshot"},
        }],
        "recognition": {"initial_ui_text_excerpt": "首页", "initial_resource_ids": []},
        "postconditions": [{"type": "text_present", "target": "设置"}],
        "safety": {"execution_enabled": False, "never_reuse_permission_grants": True},
        "evidence": [{"path": "evidence/001.jpg"}],
    }


def test_complete_skill_structure_passes_without_touching_device(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "001.jpg").write_bytes(b"fake-image")
    result = validate_skill_structure(_skill(), tmp_path)
    assert result["status"] == "passed"
    assert result["device_touched"] is False
    assert result["summary"]["error_count"] == 0


def test_missing_locator_and_recognition_are_errors():
    skill = _skill()
    skill["steps"][0]["args"] = {}
    skill["recognition"] = {}
    result = validate_skill_structure(skill)
    assert result["status"] == "failed"
    assert {x["code"] for x in result["findings"]} >= {
        "tap.locator_missing", "recognition.missing"
    }


def test_manual_assertions_and_redacted_input_require_review():
    skill = _skill()
    skill["steps"][0] = {
        "action": "input_text", "args": {"text": "[已隐藏：输入内容不写入探索记录]"},
        "verification": {"available": False, "type": "manual_review"},
    }
    skill["postconditions"] = [{"type": "manual_review"}]
    result = validate_skill_structure(skill)
    assert result["status"] == "needs_review"
    assert result["summary"]["review_count"] >= 2


def test_evidence_path_traversal_is_rejected(tmp_path):
    skill = _skill()
    skill["evidence"] = [{"path": "../../outside.jpg"}]
    result = validate_skill_structure(skill, tmp_path)
    assert result["status"] == "failed"
    assert any(x["code"] == "evidence.path_invalid" for x in result["findings"])
