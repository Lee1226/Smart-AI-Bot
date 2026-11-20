"""Extract teachable, evidence-linked skills from completed exploration traces.

The extractor preserves the user's original task prompt and only records model
decision summaries that were explicitly persisted by the exploration runner.
It never fabricates hidden reasoning, selectors, page states, or assertions.
"""
from __future__ import annotations

from typing import Any

SUPPORTED_ACTIONS = {
    "tap", "click", "swipe", "scroll", "input_text", "press_key",
    "back", "home", "recent", "wait", "launch_app", "start_app",
    "stop_app", "global_action",
}
SENSITIVE_TERMS = (
    "支付", "付款", "购买", "下单", "删除", "发送", "转账", "密码",
    "账号安全", "授权", "允许", "confirm payment", "delete", "send message",
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def extract_skill_draft(session: dict, events: list[dict]) -> dict:
    """Build a teachable draft from the complete event timeline."""
    ordered = sorted(events, key=lambda item: int(item.get("sequence") or 0))
    successful = [
        event for event in ordered
        if event.get("event_type") == "action"
        and _text(event.get("status")).lower() in {"completed", "success", "succeeded"}
    ]
    warnings: list[str] = []
    steps: list[dict] = []
    evidence: list[dict] = []
    observations: list[dict] = []
    decisions: list[dict] = []
    failed_results: list[dict] = []
    sensitive = False
    latest_observation: dict = {}
    decisions_by_action: dict[str, list[dict]] = {}

    for event in ordered:
        event_type = _text(event.get("event_type"))
        event_id = event.get("id")
        sequence = event.get("sequence")
        detail = _text(event.get("detail"))
        event_evidence = _as_dict(event.get("evidence"))
        if event_type == "observation":
            page = _as_dict(event.get("page_after"))
            observation = {
                "source_event_id": event_id,
                "sequence": sequence,
                "description": _text(event.get("title")) or "页面观察",
                "visible_text": _text(page.get("ui_text"))[:3000],
                "change_summary": _as_dict(page.get("change_summary")),
                "screenshot": event_evidence.get("screenshot", ""),
            }
            observations.append(observation)
            latest_observation = observation
            if observation["screenshot"]:
                evidence.append({
                    "event_id": event_id, "sequence": sequence,
                    "kind": "observation", "path": observation["screenshot"],
                })
        elif event_type == "decision":
            action = _text(event.get("action")).lower()
            decision = {
                "source_event_id": event_id,
                "sequence": sequence,
                "action": action,
                "summary": detail[:2000],
                "based_on_observation": latest_observation.get("source_event_id"),
            }
            decisions.append(decision)
            decisions_by_action.setdefault(action, []).append(decision)
        elif event_type == "tool_result" and _text(event.get("status")).lower() in {"failed", "error"}:
            failed_results.append({
                "source_event_id": event_id, "sequence": sequence,
                "action": _text(event.get("action")),
                "result": detail[:2000],
                "handling": "仅记录失败现场；尚未提炼出经验证的自动恢复规则",
            })

    for event in successful:
        action = _text(event.get("action")).lower()
        args = _as_dict(event.get("action_args"))
        title = _text(event.get("title")) or action or "设备操作"
        detail = _text(event.get("detail"))
        event_evidence = _as_dict(event.get("evidence"))
        before = _as_dict(event.get("page_before"))
        after = _as_dict(event.get("page_after"))
        if any(term.lower() in (title + " " + detail + " " + str(args)).lower()
               for term in SENSITIVE_TERMS):
            sensitive = True
        if action not in SUPPORTED_ACTIONS:
            warnings.append(f"第 {event.get('sequence')} 步动作「{action or title}」暂不支持结构化复用，已跳过")
            continue
        if action in {"tap", "click"} and not (
            ("x" in args and "y" in args) or args.get("resource_id") or args.get("text")
        ):
            warnings.append(f"第 {event.get('sequence')} 步点击缺少可复用的控件定位信息")
        if action == "swipe" and not all(k in args for k in ("x1", "y1", "x2", "y2")):
            warnings.append(f"第 {event.get('sequence')} 步滑动参数不完整")

        related_decisions = decisions_by_action.get(action, [])
        decision = related_decisions[-1] if related_decisions else {}
        before_text = _text(before.get("ui_text"))
        after_text = _text(after.get("ui_text"))
        steps.append({
            "source_event_id": event.get("id"),
            "sequence": event.get("sequence"),
            "action": action,
            "args": args,
            "description": title,
            "decision_summary": _text(decision.get("summary")),
            "page_before_excerpt": before_text[:1600],
            "page_after_excerpt": after_text[:1600],
            "expected_transition": _as_dict(after.get("change_summary")),
            "wait_after_ms": 0,
            "verification": {
                "available": bool(after_text or after.get("elements")),
                "type": "page_snapshot" if (after_text or after.get("elements")) else "manual_review",
                "note": "来自真实操作后的页面快照，仍需确认成功断言" if (after_text or after.get("elements")) else "缺少操作后页面快照，不能自动判定成功",
            },
        })
        for key in ("screenshot", "before_screenshot", "after_screenshot"):
            filename = event_evidence.get(key)
            if filename:
                evidence.append({
                    "event_id": event.get("id"), "sequence": event.get("sequence"),
                    "kind": key, "path": filename,
                })

    first_snapshot = next(
        (_as_dict(event.get("page_before")) for event in successful
         if _as_dict(event.get("page_before"))), {}
    )
    first_text = _text(first_snapshot.get("ui_text"))
    first_elements = first_snapshot.get("elements") if isinstance(first_snapshot.get("elements"), list) else []
    recognition = {
        "initial_ui_text_excerpt": first_text[:1200],
        "initial_resource_ids": sorted({
            str(item.get("resourceId")) for item in first_elements
            if isinstance(item, dict) and item.get("resourceId")
        })[:50],
    }
    if not first_text and not recognition["initial_resource_ids"]:
        warnings.append("缺少可识别的起始页面快照，技能前置条件尚不完整")
    if not steps:
        warnings.append("没有可提炼的成功动作；请检查探索事件是否完整")
    if len(successful) != len(steps):
        warnings.append("部分成功动作未能转换为支持的结构化步骤")
    if any(not step["verification"]["available"] for step in steps):
        warnings.append("至少一个步骤缺少操作后页面快照，必须补充结果校验")
    if sensitive:
        warnings.append("检测到可能涉及敏感操作；技能执行前必须逐次人工确认，不得继承探索时的授权")
    if failed_results:
        warnings.append("探索过程中存在失败的工具调用；失败现场已保留，但尚未形成可自动复用的恢复规则")

    goal = _text(session.get("goal"))
    expected_result = _text(session.get("expected_result"))
    name = _text(session.get("title")) or goal or "未命名技能"
    preconditions = []
    if session.get("app_package"):
        preconditions.append({"type": "app_package", "value": session["app_package"]})
    if session.get("platform"):
        preconditions.append({"type": "platform", "value": session["platform"]})
    if first_text or recognition["initial_resource_ids"]:
        preconditions.append({"type": "initial_page", "description": "必须匹配已记录的起始页面特征"})

    # This is the reusable teaching material, not a claim that the skill is
    # already executable. The original prompt remains unchanged.
    teaching = {
        "source_prompt": goal,
        "expected_outcome": expected_result,
        "task_intent": goal,
        "decision_records": decisions,
        "observations": observations,
        "recovery_experiences": failed_results,
        "execution_guidance": [
            "先识别当前页面是否符合技能起始条件，不匹配时停止并重新分析。",
            "按已记录的成功步骤顺序执行，每一步完成后重新识别页面状态。",
            "只有观察到与探索记录一致的页面变化，才继续下一步。",
            "遇到未记录的弹窗、权限请求或敏感操作时暂停并请求人工确认。",
            "最终必须依据预期结果进行验证，不能仅以动作调用成功作为任务完成依据。",
        ],
        "knowledge_completeness": {
            "has_original_prompt": bool(goal),
            "has_expected_outcome": bool(expected_result),
            "has_observations": bool(observations),
            "has_decision_records": bool(decisions),
            "has_failure_experiences": bool(failed_results),
            "has_action_evidence": bool(evidence),
        },
        "notice": "决策摘要来自探索记录中持久化的公开说明，不包含或推测模型内部隐式推理。",
    }
    recognition["teaching"] = teaching

    return {
        "name": name[:120],
        "description": goal,
        "category": "general",
        "platform": _text(session.get("platform")),
        "app_package": _text(session.get("app_package")),
        "preconditions": preconditions,
        "recognition": recognition,
        "steps": steps,
        "branches": [],
        "postconditions": [{
            "type": "manual_review",
            "description": expected_result or "请补充并确认最终结果断言",
        }],
        "safety": {
            "requires_human_review": True,
            "sensitive_action_detected": sensitive,
            "never_reuse_permission_grants": True,
            "execution_enabled": False,
        },
        "evidence": evidence,
        "warnings": list(dict.fromkeys(warnings)),
        "status": "draft",
        "version": 1,
    }
