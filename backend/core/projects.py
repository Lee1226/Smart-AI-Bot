"""Project Profiles — optional pointers to an external project's own resources
(knowledge base, skills, source) so the agent can test it better.

This is distinct from the local ``test_knowledge/`` (our own test KB). A profile
references the *target app team's* resources by absolute path — nothing is copied
into this repo. Everything is optional: with no matching profile (or missing
paths) the agent runs exactly as before.

Stored in ``backend/data/projects.json`` (gitignored — paths are machine-local).
"""
from __future__ import annotations

import json
import os
import shlex
import uuid
from pathlib import Path
from typing import Optional

PROJECTS_PATH = Path(os.getenv(
    "SAB_PROJECTS_PATH",
    str(Path(os.getenv("SAB_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "projects.json"),
))


def validate_kb_search_cmd(command: str) -> str:
    """Accept only operator-configured commands; user input never selects a binary."""
    command = (command or "").strip()
    if not command:
        return ""
    allowed_raw = os.getenv("SAB_KB_SEARCH_COMMAND_ALLOWLIST", "")
    try:
        candidate = tuple(shlex.split(command))
        allowed = {tuple(shlex.split(item.strip())) for item in allowed_raw.split(",") if item.strip()}
    except ValueError as exc:
        raise ValueError(f"Invalid knowledge-search command: {exc}") from exc
    if candidate not in allowed:
        raise ValueError("Knowledge-search command is not in the server allowlist")
    return shlex.join(candidate)


def normalize_project(data: dict) -> dict:
    result = dict(data)
    ids = {k: (v or "").strip() for k, v in (data.get("app_ids") or {}).items()
           if k in {"android", "harmony", "ios"}}
    if "android" not in ids:
        ids["android"] = (data.get("app_package") or "").strip()
    result["app_ids"] = ids
    result["app_package"] = ids.get("android", "")
    return result


def get_project(project_id: str) -> Optional[dict]:
    return next((p for p in load_projects() if p["id"] == project_id), None)


def suite_project(suite) -> Optional[dict]:
    return get_project(suite.project_id) if suite.project_id else match_project(suite.app_package)


def target_for_suite(suite, platform: str) -> str:
    project = suite_project(suite)
    if suite.project_id and not project:
        raise ValueError("所属项目已删除，请重新选择项目")
    if project:
        target = project["app_ids"].get(platform, "")
        if not target:
            raise ValueError(f"项目「{project['name']}」未配置 {platform} 应用标识，请到设置页配置")
        return target
    if suite.app_package and platform != "android":
        raise ValueError("旧版包名仅适用于 Android，请选择项目并配置对应平台应用标识")
    return suite.app_package or ""


def load_projects() -> list[dict]:
    try:
        PROJECTS_PATH.chmod(0o600)
        return [normalize_project(p) for p in json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))]
    except Exception:
        return []


def save_projects(projects: list[dict]) -> None:
    PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PROJECTS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(projects, indent=2, ensure_ascii=False))
    PROJECTS_PATH.chmod(0o600)


def new_project(data: dict) -> dict:
    return normalize_project({
        "app_ids": data.get("app_ids") or {},
        "id": uuid.uuid4().hex,
        "name": (data.get("name") or "").strip() or "Untitled project",
        "app_package": (data.get("app_package") or "").strip(),
        "kb_path": (data.get("kb_path") or "").strip(),
        "source_root": (data.get("source_root") or "").strip(),
        # The project's own knowledge-search CLI (whatever its KB/skill exposes).
        # When set, it's used for retrieval instead of keyword search over kb_path.
        "kb_search_cmd": validate_kb_search_cmd(data.get("kb_search_cmd") or ""),
    })


def match_project(app_package: str) -> Optional[dict]:
    """The profile whose app_package matches (exact). None if no package / match."""
    if not app_package:
        return None
    for p in load_projects():
        if app_package in p.get("app_ids", {}).values():
            return p
    return None


def resolve_path(path: str) -> str:
    """Expand ~ and make absolute. Relative paths resolve against the backend
    process CWD (discouraged — prefer an absolute path or ~/...)."""
    if not path:
        return ""
    return os.path.abspath(os.path.expanduser(path.strip()))


def kb_roots_for(app_package: str) -> list[str]:
    """Existing KB directories for the matched project (empty if none/missing)."""
    p = get_project(app_package) or match_project(app_package)
    if not p:
        return []
    kb = resolve_path(p.get("kb_path") or "")
    return [kb] if kb and os.path.isdir(kb) else []


def source_root_for(app_package: str) -> str:
    """Existing source root for the matched project ('' if none/missing)."""
    p = get_project(app_package) or match_project(app_package)
    if not p:
        return ""
    src = resolve_path(p.get("source_root") or "")
    return src if src and os.path.isdir(src) else ""


def kb_search_cmd_for(app_package: str) -> str:
    """The matched project's knowledge-search CLI ('' if none)."""
    p = get_project(app_package) or match_project(app_package)
    return (p.get("kb_search_cmd") or "").strip() if p else ""
