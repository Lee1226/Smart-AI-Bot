"""
Self knowledge base — conversational notes the user dictates about the app
under test, organized by an LLM into structured entries and stored as readable
markdown under the persistent data dir (``backend/data/notes/`` →
``/app/data/notes`` in Docker, inside the mounted volume).

This is project-agnostic plumbing: the *feature* (dictate → organize → store →
query) ships to OSS, but the notes themselves are per-user runtime data — they
live under ``data/`` (gitignored and excluded from the OSS sync), so each
user's content stays private and survives container rebuilds.

Notes are a queryable reference library — they are NOT auto-injected into the
agent at test time (unlike ``test_knowledge/features/``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.llm import ModelTarget, resilient_completion
from core.i18n import lang_directive

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────
# Notes live in the persistent data dir (alongside settings.json). In Docker
# this resolves to /app/data/notes, inside the mounted `backend-data` volume,
# so they survive `docker compose up --build`. They previously lived one level
# up under test_knowledge/notes (= /test_knowledge/notes in the container),
# OUTSIDE the volume — every rebuild silently wiped them.
NOTES_DIR = Path(__file__).resolve().parent.parent / "data" / "notes"
_LEGACY_NOTES_DIR = Path(__file__).resolve().parent.parent.parent / "test_knowledge" / "notes"


def _migrate_legacy_notes() -> None:
    """One-time move of notes from the old test_knowledge/notes location into the
    persistent data dir. Idempotent and best-effort — never blocks startup.

    Helps existing local installs upgrade without losing notes. (In a rebuilt
    container the old dir is already gone, so this is a no-op there.)"""
    try:
        if _LEGACY_NOTES_DIR == NOTES_DIR or not _LEGACY_NOTES_DIR.is_dir():
            return
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        for p in _LEGACY_NOTES_DIR.glob("*.md"):
            dest = NOTES_DIR / p.name
            if not dest.exists():
                p.replace(dest)
                logger.info("Migrated note %s → %s", p.name, NOTES_DIR)
    except Exception:
        logger.warning("Legacy notes migration skipped", exc_info=True)


_migrate_legacy_notes()

# A note file embeds its full metadata as JSON inside an HTML comment so the
# markdown stays human-readable while parsing stays dependency-free (no YAML).
_META_RE = re.compile(r"<!--\s*smartbot-note\s*(\{.*?\})\s*-->", re.DOTALL)


# ── Model resolution (reuse the operator account's configured model) ───────

async def _resolve_operator_model() -> tuple[str, str, str, str, str]:
    """(provider, model, api_key, api_base, language) from the operator settings.

    The knowledge base is operator-only (see routers/knowledge.py), so its LLM
    runs on the operator's configured keys, not a per-tenant account.
    """
    from core.settings_store import (
        apply_aws_env,
        load_settings,
        operator_account_id,
        resolve_api_base,
        resolve_api_key,
        resolve_language,
        resolve_provider_model,
    )
    from db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        op = await operator_account_id(db)
    settings = await load_settings(op)
    provider, model = resolve_provider_model(settings)
    api_key = resolve_api_key(settings, provider)
    api_base = resolve_api_base(settings, provider)
    language = resolve_language(settings)
    if provider.lower() == "bedrock":
        apply_aws_env(settings)
    return provider, model, api_key, api_base, language


# ── Storage ────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    """A short filesystem-safe slug from a title (keeps CJK)."""
    s = re.sub(r"[^\w一-鿿]+", "-", text.strip()).strip("-")
    return (s[:32] or "note").lower()


def _note_path(note: dict) -> Path:
    return NOTES_DIR / f"{note['created_at'][:10]}-{_slug(note['title'])}-{note['id'][:6]}.md"


def _write_note(note: dict) -> None:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    meta = json.dumps(note, ensure_ascii=False)
    kws = " · ".join(note.get("keywords") or [])
    aliases = " · ".join(note.get("aliases") or [])
    lines = [
        f"<!-- smartbot-note {meta} -->",
        "",
        f"# {note['title']}",
        "",
        note.get("body", "").strip(),
        "",
        "---",
        f"> 关键词 / keywords: {kws}" if kws else "",
        f"> 别名 / aliases: {aliases}" if aliases else "",
        f"> 原始输入 / raw: {note.get('raw_input', '')}",
        f"> 录入 / created: {note['created_at']}",
        "",
    ]
    _note_path(note).write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")


def _parse_note(path: Path) -> Optional[dict]:
    try:
        m = _META_RE.search(path.read_text(encoding="utf-8"))
        if not m:
            return None
        note = json.loads(m.group(1))
        note["_file"] = str(path)
        return note
    except Exception as exc:
        logger.warning("Skipping unparseable note %s: %s", path.name, exc)
        return None


def list_notes() -> list[dict]:
    """All notes, newest first."""
    if not NOTES_DIR.exists():
        return []
    notes = [n for p in NOTES_DIR.glob("*.md") if (n := _parse_note(p))]
    notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    return notes


def search_notes(query: str) -> list[dict]:
    """Keyword filter over title / body / keywords / aliases / raw input."""
    q = (query or "").strip().lower()
    if not q:
        return list_notes()
    tokens = [t for t in re.findall(r"[\w一-鿿]+", q) if t]
    out = []
    for n in list_notes():
        hay = " ".join([
            n.get("title", ""), n.get("body", ""), n.get("raw_input", ""),
            " ".join(n.get("keywords") or []), " ".join(n.get("aliases") or []),
        ]).lower()
        if all(t in hay for t in tokens):
            out.append(n)
    return out


def get_note(note_id: str) -> Optional[dict]:
    return next((n for n in list_notes() if n["id"] == note_id), None)


def notes_for_search() -> list[dict]:
    """Lightweight records for the agent's KB retrieval (title/body/keywords/
    aliases). Every note participates — there is no per-note opt-out."""
    return [
        {
            "title": n["title"],
            "body": n.get("body", ""),
            "keywords": n.get("keywords") or [],
            "aliases": n.get("aliases") or [],
        }
        for n in list_notes()
    ]


def delete_note(note_id: str) -> bool:
    n = get_note(note_id)
    if not n:
        return False
    try:
        Path(n["_file"]).unlink(missing_ok=True)
        return True
    except Exception:
        return False


def update_note(note_id: str, fields: dict) -> Optional[dict]:
    """Edit a note's structured fields (title/body/keywords/aliases)."""
    n = get_note(note_id)
    if not n:
        return None
    old_path = Path(n["_file"])
    for k in ("title", "body", "keywords", "aliases"):
        if k in fields and fields[k] is not None:
            n[k] = fields[k]
    n["updated_at"] = datetime.now().isoformat(timespec="seconds")
    n.pop("_file", None)
    old_path.unlink(missing_ok=True)  # title may change → path changes; rewrite fresh
    _write_note(n)
    return _parse_note(_note_path(n))


# ── LLM organize ────────────────────────────────────────────────────────────

_ORGANIZE_PROMPT = (
    "You organize a tester's free-form note about an app under test into a "
    "structured knowledge entry. The note may be jargon/slang ('X means Y'), a "
    "feature explanation, a navigation path, or a known pitfall.\n"
    "Return ONLY a JSON object with these fields:\n"
    '  "title":   a short title (<= 20 chars)\n'
    '  "body":    a clear, tidy explanation in markdown (keep it faithful to the input; do NOT invent facts)\n'
    '  "keywords": string[] — search terms, INCLUDING any proper nouns / slang / formal names mentioned\n'
    '  "aliases":  string[] — equivalent names or ways to refer to the same thing\n'
    "Do not wrap the JSON in prose or code fences.\n"
    'IMPORTANT: inside any string value do NOT use the double-quote character ("); '
    "if you need to quote a term use 「」 or '' instead, so the JSON stays valid."
)


def _parse_structured(content: str, raw_input: str) -> dict:
    """Parse the model's JSON, tolerating the common failure where a string
    value contains unescaped double-quotes. Strict parse first (best: body is
    cleaned too); on failure, salvage title/keywords/aliases by regex and fall
    back to the raw text for body so nothing is lost or truncated."""
    content = re.sub(r"^```[a-z]*\n?", "", content.strip()).rstrip("` \n")
    m = re.search(r"\{.*\}", content, re.DOTALL)
    blob = m.group() if m else content

    title = body = None
    keywords = aliases = None
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            title, body = data.get("title"), data.get("body")
            keywords, aliases = data.get("keywords"), data.get("aliases")
    except (ValueError, TypeError):
        def _arr(name: str) -> list:
            mm = re.search(rf'"{name}"\s*:\s*\[(.*?)\]', blob, re.DOTALL)
            return re.findall(r'"([^"\n]+)"', mm.group(1)) if mm else []
        tm = re.search(r'"title"\s*:\s*"([^"\n]+)"', blob)
        title = tm.group(1) if tm else None
        keywords, aliases = _arr("keywords"), _arr("aliases")
        # body is unreliable to salvage (unescaped quotes truncate it) → use raw

    return {
        "title": (title or raw_input.strip()[:20] or "未命名").strip()[:40],
        "body": (body or raw_input).strip(),
        "keywords": [str(k).strip() for k in (keywords or []) if str(k).strip()],
        "aliases": [str(a).strip() for a in (aliases or []) if str(a).strip()],
    }


async def organize(raw_input: str) -> dict:
    """Turn a free-form note into a structured entry via the default model.

    Always returns a usable dict; on any failure it falls back to storing the
    raw text verbatim so the user never loses input."""
    provider, model, api_key, api_base, language = await _resolve_operator_model()
    fallback = {
        "title": raw_input.strip()[:20] or "未命名",
        "body": raw_input.strip(),
        "keywords": [],
        "aliases": [],
    }
    try:
        response = await resilient_completion(
            primary=ModelTarget(provider, model, api_key, api_base),
            base_kwargs={
                "messages": [
                    {"role": "system", "content": _ORGANIZE_PROMPT + lang_directive(language)},
                    {"role": "user", "content": raw_input.strip()},
                ],
                "temperature": 0.2,
                "max_tokens": 600,
            },
            timeout=30.0,
        )
        content = (response.choices[0].message.content or "").strip()
        return _parse_structured(content, raw_input)
    except Exception as exc:
        logger.warning("Knowledge organize failed (%s) — storing raw", exc)
        return fallback


async def add_note(raw_input: str) -> dict:
    """Organize the input and persist it. Returns the stored note."""
    structured = await organize(raw_input)
    now = datetime.now().isoformat(timespec="seconds")
    note = {
        "id": uuid.uuid4().hex,
        "title": structured["title"],
        "body": structured["body"],
        "keywords": structured["keywords"],
        "aliases": structured["aliases"],
        "raw_input": raw_input.strip(),
        "created_at": now,
        "updated_at": now,
    }
    _write_note(note)
    logger.info("Knowledge note added: %s", note["title"])
    return _parse_note(_note_path(note))
