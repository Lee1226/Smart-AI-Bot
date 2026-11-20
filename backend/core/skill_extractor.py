"""Conservative rule-based extraction of reviewable skills from exploration traces."""
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


def extract_skill_draft(session: dict, events: list[dict]) -> dict:
    """Build a draft without inventing coordinates, selectors, or assertions."""
    ordered = sorted(events, key=lambda item: int(item.get("sequence") or 0))
    successful = [e for e in ordered if e.get("event_type") == "action"
                  and str(e.get("status") or "").lower() in {"completed", "success", "succeeded"}]
    warnings: list[str] = []
    steps: list[dict] = []
    evidence: list[dict] = []
    sensitive = False

    for event in successful:
        action = str(event.get("action") or "").strip().lower()
        args = _as_dict(event.get("action_args"))
        title = str(event.get("title") or action or "设备操作").strip()
        detail = str(event.get("detail") or "")
        evidence_item = _as_dict(event.get("evidence"))
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
        steps.append({
            "source_event_id": event.get("id"),
            "sequence": event.get("sequence"),
            "action": action,
            "args": args,
            "description": title,
            "expected_transition": _as_dict(after.get("change_summary")),
            "wait_after_ms": 0,
            "verification": {
                "available": bool(after.get("ui_text") or after.get("elements")),
                "type": "page_snapshot" if (after.get("ui_text") or after.get("elements")) else "manual_review",
                "note": "基于探索后的页面快照；需审核确认断言" if (after.get("ui_text") or after.get("elements")) else "探索记录缺少操作后页面快照，不能自动判定成功",
            },
        })
        for key in ("screenshot", "before_screenshot", "after_screenshot"):
            filename = evidence_item.get(key)
            if filename:
                evidence.append({"event_id": event.get("id"), "sequence": event.get("sequence"),
                                 "kind": key, "path": filename})

    first_snapshot = next((_as_dict(e.get("page_before")) for e in successful
                           if _as_dict(e.get("page_before"))), {})
    first_text = str(first_snapshot.get("ui_text") or "").strip()
    first_elements = first_snapshot.get("elements") if isinstance(first_snapshot.get("elements"), list) else []
    recognition = {
        "initial_ui_text_excerpt": first_text[:1200],
        "initial_resource_ids": sorted({str(x.get("resourceId")) for x in first_elements
                                        if isinstance(x, dict) and x.get("resourceId")})[:50],
    }
    if not first_text and not recognition["initial_resource_ids"]:
        warnings.append("缺少可识别的起始页面快照，技能前置条件尚不完整")
    if not steps:
        warnings.append("没有可提炼的成功动作；请检查探索事件是否完整")
    if len(successful) != len(steps):
        warnings.append("部分成功动作未能转换为支持的结构化步骤")
    if any(not s["verification"]["available"] for s in steps):
        warnings.append("至少一个步骤缺少操作后页面快照，必须补充结果校验")
    if sensitive:
        warnings.append("检测到可能涉及敏感操作；技能执行前必须逐次人工确认，不得继承探索时的授权")

    goal = str(session.get("goal") or "").strip()
    name = str(session.get("title") or goal or "未命名技能").strip()[:120]
    preconditions = []
    if session.get("app_package"):
        preconditions.append({"type": "app_package", "value": session["app_package"]})
    if session.get("platform"):
        preconditions.append({"type": "platform", "value": session["platform"]})
    if first_text or recognition["initial_resource_ids"]:
        preconditions.append({"type": "initial_page", "description": "匹配技能起始页面特征后才允许执行"})
    return {
        "name": name,
        "description": goal,
        "category": "general",
        "platform": str(session.get("platform") or ""),
        "app_package": str(session.get("app_package") or ""),
        "preconditions": preconditions,
        "recognition": recognition,
        "steps": steps,
        "branches": [],
        "postconditions": [{"type": "manual_review", "description": str(session.get("expected_result") or "请补充并确认最终结果断言")}],
        "safety": {"requires_human_review": True, "sensitive_action_detected": sensitive,
                   "never_reuse_permission_grants": True, "execution_enabled": False},
        "evidence": evidence,
        "warnings": list(dict.fromkeys(warnings)),
        "status": "draft",
        "version": 1,
    }
