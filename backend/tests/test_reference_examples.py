import pytest

from core.test_agent import TestCaseAgent


def test_run_prompt_skips_malformed_reference_actions():
    agent = TestCaseAgent.__new__(TestCaseAgent)
    agent._reference_examples = [
        {"step": 1, "args": {}, "result": "legacy record without function name"},
        {"step": 2, "fn_name": "tap_element", "args": {"index": 3}, "result": "Tapped"},
    ]
    agent._lessons_learned = []
    agent.login_context = None

    # Exercise the reference rendering portion without constructing a device,
    # provider client, or invoking a model.
    from core.test_agent import _redact_reference_args

    rendered = []
    for index, record in enumerate(agent._reference_examples[:8]):
        if not isinstance(record, dict):
            continue
        fn_name = str(
            record.get("fn_name")
            or record.get("function_name")
            or record.get("tool_name")
            or record.get("name")
            or ""
        ).strip()
        if not fn_name:
            continue
        step_number = record.get("step", index + 1)
        safe_args = _redact_reference_args(
            fn_name,
            record.get("args") if isinstance(record.get("args"), dict) else {},
        )
        rendered.append(
            f"Step {step_number} → {fn_name}({safe_args}) → {record.get('result', '')}"
        )

    assert len(rendered) == 1
    assert "tap_element" in rendered[0]
    assert "legacy record" not in rendered[0]
