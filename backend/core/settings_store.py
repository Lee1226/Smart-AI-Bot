"""Per-account settings store — replaces the legacy global settings.json.

Each account owns its own settings blob (LLM keys, default provider/model,
language, webhook).  Reads/writes go through the ``account_settings`` table.

The legacy ``settings.json`` is only used once as a seed: on first boot the
operator account (see ``auth.access.operator_account_id``) inherits it.  Other
accounts start empty and configure their own keys via the Settings page.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import AccountSettings

logger = logging.getLogger(__name__)

# Legacy global settings file (seed-only).  Kept read-only for migration; the
# live per-account data lives in the DB.
LEGACY_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

# Defaults for an account that has never configured anything.  Key fields are
# blank — a key-less account cannot run AI tests until it configures its own key.
DEFAULT_SETTINGS = {
    "openai_api_key": "",
    "anthropic_api_key": "",
    "anthropic_base_url": "",
    "gemini_api_key": "",
    "zhipu_api_key": "",
    "groq_api_key": "",
    "ollama_base_url": "http://localhost:11434",
    "aws_access_key_id": "",
    "aws_secret_access_key": "",
    "aws_region_name": "us-east-1",
    "default_provider": "openai",
    "default_model": "gpt-4o",
    "verifier_provider": "",
    "verifier_model": "",
    "webhook_url": "",
    "webhook_type": "",
    "language": "zh",
    "ark_api_key": "",
    "ark_base_url": "https://ark.cn-beijing.volces.com/api/v3",
}

# Provider → settings key field for the API key.
_KEY_MAP = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google": "gemini_api_key",
    "gemini": "gemini_api_key",
    "zhipuai": "zhipu_api_key",
    "zhipu": "zhipu_api_key",
    "groq": "groq_api_key",
    "ark": "ark_api_key",
    "ollama": "",  # no key needed
}

_BASE_MAP = {
    "anthropic": "anthropic_base_url",
    "ark": "ark_base_url",
    "ollama": "ollama_base_url",
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


# ── Resolution helpers (pure functions over a settings dict) ─────────────────
# These replace the file-reading helpers that used to live in test_runner.py,
# knowledge.py, webhook.py and testruns.py, so every consumer shares one source
# of truth instead of re-parsing settings.json with subtly different maps.

def resolve_api_key(settings: dict, provider: str) -> str:
    field = _KEY_MAP.get((provider or "").lower(), f"{(provider or '').lower()}_api_key")
    return settings.get(field, "") if field else ""


def resolve_api_base(settings: dict, provider: str) -> str:
    field = _BASE_MAP.get((provider or "").lower(), "")
    return settings.get(field, "") if field else ""


def resolve_provider_model(settings: dict) -> tuple[str, str]:
    return (
        settings.get("default_provider") or "openai",
        settings.get("default_model") or "gpt-4o",
    )


def resolve_verifier(settings: dict) -> tuple[str, str, str, str]:
    """(verifier_provider, verifier_model, verifier_api_key, verifier_api_base)."""
    v_provider = settings.get("verifier_provider", "") or ""
    v_model = settings.get("verifier_model", "") or ""
    v_key = resolve_api_key(settings, v_provider) if v_provider else ""
    v_base = resolve_api_base(settings, v_provider) if v_provider else ""
    return v_provider, v_model, v_key, v_base


def resolve_language(settings: dict) -> str:
    lang = (settings.get("language") or "").strip()
    return "en" if lang == "en" else "zh"


def resolve_webhook(settings: dict) -> tuple[str, str]:
    return settings.get("webhook_url", "") or "", settings.get("webhook_type", "") or ""


def apply_aws_env(settings: dict) -> None:
    """Export AWS Bedrock creds into the process env (litellm/boto3 read them)."""
    import os

    ak = settings.get("aws_access_key_id", "") or ""
    sk = settings.get("aws_secret_access_key", "") or ""
    region = settings.get("aws_region_name", "") or "us-east-1"
    if ak and sk:
        os.environ["AWS_ACCESS_KEY_ID"] = ak
        os.environ["AWS_SECRET_ACCESS_KEY"] = sk
        os.environ["AWS_REGION_NAME"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_REGION"] = region


def build_fallback_chain(settings: dict, primary_provider: str, primary_model: str) -> list:
    """Ordered list of ModelTarget fallbacks from any other key'd provider."""
    from agent.llm import ModelTarget

    candidates = [
        ("openai", "openai_api_key", "gpt-4o"),
        ("anthropic", "anthropic_api_key", "claude-3-5-sonnet-20241022"),
        ("gemini", "gemini_api_key", "gemini-1.5-pro"),
        ("zhipuai", "zhipu_api_key", "glm-4v"),
        ("groq", "groq_api_key", "llama-3.2-90b-vision-preview"),
    ]
    chain: list = []
    for provider, key_field, default_model in candidates:
        if provider.lower() == (primary_provider or "").lower():
            continue
        key = settings.get(key_field, "")
        if not key:
            continue
        chain.append(ModelTarget(
            provider=provider, model=default_model,
            api_key=key, api_base=resolve_api_base(settings, provider),
        ))
    return chain
