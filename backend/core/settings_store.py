"""Per-account settings store — replaces the legacy global settings.json.

Each account owns its own settings blob (model selections, language, and
webhook settings). Reads/writes go through the ``account_settings`` table.

The legacy ``settings.json`` is only used once as a seed: on first boot the
operator account (see ``auth.access.operator_account_id``) inherits it.  Other
accounts start empty and configure their own keys via the Settings page.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import AccountSettings

logger = logging.getLogger(__name__)

# Legacy global settings file (seed-only).  Kept read-only for migration; the
# live per-account data lives in the DB.
LEGACY_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

# Defaults for account-level preferences. Model credentials live exclusively
# in encrypted model-repository connections.
DEFAULT_SETTINGS = {
    "default_model_id": "",
    "verifier_model_id": "",
    "webhook_url": "",
    "webhook_type": "",
    "language": "zh",
    "recording_auto_enabled": True,
    "recording_storage_dir": str(Path(__file__).resolve().parent.parent / "data" / "recordings"),
    "recording_retention_days": 30,
}

async def operator_account_id(db: AsyncSession) -> str:
    """Resolve the operator account id (env pin → is_operator flag → oldest)."""
    from auth.access import operator_account_id as _op
    return await _op(db)


async def _seed_operator_from_legacy() -> None:
    """One-time migration: give the operator the legacy settings.json content."""
    if not LEGACY_SETTINGS_PATH.exists():
        return
    try:
        legacy = json.loads(LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Legacy settings.json unreadable during seed: %s", exc)
        return
    if not legacy:
        return

    async with AsyncSessionLocal() as db:
        op = await operator_account_id(db)
        if not op:
            return
        existing = await db.get(AccountSettings, op)
        if existing is not None:
            return
        legacy_model_keys = {
            "openai_api_key", "anthropic_api_key", "anthropic_base_url",
            "gemini_api_key", "zhipu_api_key", "dashscope_api_key",
            "dashscope_base_url", "groq_api_key", "ollama_base_url",
            "ark_api_key", "ark_base_url", "aws_access_key_id",
            "aws_secret_access_key", "aws_region_name",
            "default_provider", "default_model", "verifier_provider", "verifier_model",
        }
        legacy = {key: value for key, value in legacy.items() if key not in legacy_model_keys}
        db.add(AccountSettings(account_id=op, data=json.dumps(legacy, ensure_ascii=False)))
        await db.commit()
        logger.info("Seeded operator settings for account %s", op)


async def ensure_seeded() -> None:
    """Call once at startup (after init_db)."""
    try:
        await _seed_operator_from_legacy()
    except Exception:
        logger.warning("Settings seed failed", exc_info=True)


async def load_settings(account_id: str) -> dict:
    """Return the settings dict for an account (defaults for a fresh account)."""
    if not account_id:
        return dict(DEFAULT_SETTINGS)
    async with AsyncSessionLocal() as db:
        row = await db.get(AccountSettings, account_id)
        if row is None:
            return dict(DEFAULT_SETTINGS)
        try:
            data = json.loads(row.data or "{}")
        except Exception:
            return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    # Model credentials and selection are exclusively model-repository based.
    obsolete_model_keys = {
        "openai_api_key", "anthropic_api_key", "anthropic_base_url",
        "gemini_api_key", "zhipu_api_key", "dashscope_api_key",
        "dashscope_base_url", "groq_api_key", "ollama_base_url",
        "ark_api_key", "ark_base_url", "aws_access_key_id",
        "aws_secret_access_key", "aws_region_name",
        "default_provider", "default_model", "verifier_provider", "verifier_model",
    }
    for key in obsolete_model_keys:
        merged.pop(key, None)
    return merged


async def save_settings(account_id: str, data: dict) -> None:
    """Persist an account's settings (merge over current, never partial)."""
    if not account_id:
        raise ValueError("account_id is required")
    async with AsyncSessionLocal() as db:
        row = await db.get(AccountSettings, account_id)
        payload = json.dumps(data, ensure_ascii=False)
        if row is None:
            db.add(AccountSettings(account_id=account_id, data=payload, updated_at=datetime.utcnow()))
        else:
            row.data = payload
            row.updated_at = datetime.utcnow()
        await db.commit()


# ── Account preference helpers ───────────────────────────────────────────────

def resolve_language(settings: dict) -> str:
    lang = (settings.get("language") or "").strip()
    return "en" if lang == "en" else "zh"


def build_fallback_chain(settings: dict, primary_provider: str, primary_model: str) -> list:
    """Automatic provider switching is disabled.

    A configured API key is not consent to use that provider as a backup.
    If explicit per-run fallback configuration is introduced later, it must
    resolve only user-selected model records rather than infer candidates.
    """
    return []
