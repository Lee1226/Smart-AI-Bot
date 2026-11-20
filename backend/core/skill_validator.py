"""Non-invasive structural validation for reusable skills.

This validator never connects to or controls a device. A passed result means
only that the stored skill definition is structurally complete enough to enter
the later device-validation stage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED_ACTIONS = {
    "tap", "click", "swipe", "scroll", "input_text", "press_key",
    "back", "home", "recent", "wait", "launch_app", "start_app",
    "stop_app", "global_action",
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def validate_skill_structure(skill: dict, evidence_root: Path | None = None) -> dict:
    """Return findings without performing any device or network operation."""
    findings: list[dict] = []

    def add(severity: str, code: str, message: str, step: int | None = None):
        item = {"severity": severity, "code": code, "message": message}
        if step is not None:
            item["step"] = step
        findings.append(item)

    steps = _list(skill.get("steps"))
    if not steps:
        add("error", "steps.empty", "技能没有可执行步骤")

    for index, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            add("error", "step.invalid", "步骤结构不是对象", index)
            continue
        action = str(raw.get("action") or "").strip().lower()
        args = _dict(raw.get("args"))
        if action not in SUPPORTED_ACTIONS:
            add("error", "action.unsupported", f"不支持的动作类型：{action or '未填写'}", index)
            continue
        if action in {"tap", "click"}:
            has_xy = all(isinstance(args.get(k), (int, float)) for k in ("x", "y"))
            has_locator = bool(args.get("resource_id") or args.get("text"))
            if not (has_xy or has_locator):
                add("error", "tap.locator_missing", "点击步骤缺少坐标或语义定位信息", index)
        elif action == "swipe":
            if not all(isinstance(args.get(k), (int, float)) for k in ("x1", "y1", "x2", "y2")):
                add("error", "swipe.args_missing", "滑动步骤缺少完整的起止坐标", index)
        elif action == "input_text":
            value = str(args.get("text") or "")
            if not value:
                add("error", "input.text_missing", "输入步骤缺少输入内容", index)
            elif value.startswith("[已隐藏"):
                add("review", "input.redacted", "输入内容已脱敏，需要人工补录后才能进行真机验证", index)
        elif action == "start_app":
            if not str(args.get("package_name") or args.get("package") or "").strip():
                add("error", "app.package_missing", "启动应用步骤缺少 package 标识", index)
        elif action == "scroll":
            if str(args.get("direction") or "down") not in {"up", "down", "left", "right"}:
                add("error", "scroll.direction_invalid", "滚动方向无效", index)
        elif action == "wait":
            try:
                duration = float(args.get("duration_ms", raw.get("wait_after_ms", 0)))
            except (TypeError, ValueError):
                duration = -1
            if duration < 0 or duration > 60000:
                add("error", "wait.duration_invalid", "等待时长必须在 0–60000 毫秒之间", index)

        wait_after = raw.get("wait_after_ms", 0)
        try:
            wait_after = float(wait_after or 0)
        except (TypeError, ValueError):
            wait_after = -1
        if wait_after < 0 or wait_after > 60000:
            add("error", "wait_after.invalid", "操作后等待时长必须在 0–60000 毫秒之间", index)

        verification = _dict(raw.get("verification"))
        if not verification.get("available") or verification.get("type") == "manual_review":
            add("review", "verification.missing", "该步骤缺少可自动判定的操作后结果断言", index)

    recognition = _dict(skill.get("recognition"))
    if not (str(recognition.get("initial_ui_text_excerpt") or "").strip()
            or _list(recognition.get("initial_resource_ids"))):
        add("error", "recognition.missing", "缺少起始页面识别特征")

    if not str(skill.get("platform") or "").strip():
        add("review", "scope.platform_missing", "未限定适用平台")
    if not str(skill.get("app_package") or "").strip():
        add("review", "scope.package_missing", "未限定目标应用包名")

    postconditions = _list(skill.get("postconditions"))
    if not postconditions:
        add("error", "postcondition.missing", "缺少最终结果断言")
    elif all(_dict(item).get("type") == "manual_review" for item in postconditions):
        add("review", "postcondition.manual", "最终结果目前只能人工检查，尚不能自动判定")

    safety = _dict(skill.get("safety"))
    if safety.get("execution_enabled") is not False:
        add("error", "safety.execution_flag", "技能必须保持 execution_enabled=false，结构校验不会启用执行")
    if safety.get("never_reuse_permission_grants") is not True:
        add("error", "safety.permission_policy", "必须明确禁止复用探索时授予的权限")

    evidence = _list(skill.get("evidence"))
    if not evidence:
        add("review", "evidence.empty", "没有关联截图证据")
    elif evidence_root is not None:
        for index, item in enumerate(evidence, 1):
            if not isinstance(item, dict):
                add("error", "evidence.invalid", f"第 {index} 条证据引用格式无效")
                continue
            raw_path = str(item.get("path") or "").replace("\\", "/")
            relative = Path(raw_path)
            if not raw_path or relative.is_absolute() or ".." in relative.parts:
                add("error", "evidence.path_invalid", f"第 {index} 条证据路径无效")
                continue
            target = (evidence_root / relative).resolve()
            root = evidence_root.resolve()
            if target != root and root not in target.parents:
                add("error", "evidence.path_invalid", f"第 {index} 条证据路径越界")
            elif not target.is_file():
                add("review", "evidence.file_missing", f"第 {index} 条截图证据文件不存在：{raw_path}")

    severities = {item["severity"] for item in findings}
    status = "failed" if "error" in severities else "needs_review" if "review" in severities else "passed"
    return {
        "status": status,
        "summary": {
            "error_count": sum(x["severity"] == "error" for x in findings),
            "review_count": sum(x["severity"] == "review" for x in findings),
            "step_count": len(steps),
            "evidence_count": len(evidence),
        },
        "findings": findings,
        "device_touched": False,
        "validation_type": "structural",
    }
