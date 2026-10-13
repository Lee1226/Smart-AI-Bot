"""Settings — per-account LLM keys and model config, persisted to the DB.

Each account owns its own settings blob (isolated from other tenants and from
the operator).  Reading/writing requires owner/admin of that account.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id, require_role
from core.settings_store import load_settings, save_settings
from db.database import AsyncSessionLocal

router = APIRouter(prefix="/api/settings", tags=["settings"])


class Settings(BaseModel):
    openai_api_key: Optional[str] = ""
    anthropic_api_key: Optional[str] = ""
    anthropic_base_url: Optional[str] = ""   # optional override (e.g. internal LiteLLM proxy URL)
    gemini_api_key: Optional[str] = ""
    zhipu_api_key: Optional[str] = ""
    groq_api_key: Optional[str] = ""
    ollama_base_url: Optional[str] = "http://localhost:11434"
    ark_api_key: Optional[str] = ""
    ark_base_url: Optional[str] = "https://ark.cn-beijing.volces.com/api/v3"
    # AWS Bedrock (provider = "bedrock"). Uses AWS credentials, not an API key.
    aws_access_key_id: Optional[str] = ""
    aws_secret_access_key: Optional[str] = ""
    aws_region_name: Optional[str] = "us-east-1"
    default_provider: Optional[str] = "openai"
    default_model: Optional[str] = "gpt-4o"
    # Verifier can use a separate (stronger) model for pass/fail judgment.
    # Leave empty to use the same model as the agent.
    verifier_provider: Optional[str] = ""
    verifier_model: Optional[str] = ""
    # Webhook notification after run completes
    webhook_url: Optional[str] = ""
    webhook_type: Optional[str] = ""  # feishu / dingtalk / slack / custom
    # Main language for AI output (thoughts/reasons/verdicts/plans) + report: zh / en
    language: Optional[str] = "zh"
    configured_secrets: list[str] = Field(default_factory=list)


SECRET_FIELDS = {
    "openai_api_key", "anthropic_api_key", "gemini_api_key", "zhipu_api_key",
    "groq_api_key", "ark_api_key", "aws_access_key_id", "aws_secret_access_key",
    "webhook_url",
}


class SettingsUpdate(Settings):
    # Explicit removal is separate from masked/blank inputs (which keep keys).
    clear_secrets: list[str] = Field(default_factory=list)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _masked(data: dict) -> Settings:
    defaults = Settings().model_dump(exclude={"configured_secrets"})
    values = {key: data.get(key, default) for key, default in defaults.items()}
    configured = sorted(key for key in SECRET_FIELDS if data.get(key))
    for key in SECRET_FIELDS:
        values[key] = ""
    return Settings(**values, configured_secrets=configured)


@router.get("", response_model=Settings)
async def get_settings(request: Request):
    require_role(request, "owner", "admin")
    data = await load_settings(require_account_id(request))
    return _masked(data)


@router.put("", response_model=Settings)
async def update_settings(new_settings: SettingsUpdate, request: Request):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    if set(new_settings.clear_secrets) - SECRET_FIELDS:
        raise HTTPException(status_code=422, detail="Unknown secret field to clear")
    current = await load_settings(account_id)
    incoming = new_settings.model_dump(exclude_none=True, exclude_unset=True,
                                      exclude={"configured_secrets", "clear_secrets"})
    # Blank password fields mean "keep the existing value".  The API never
    # sends stored credentials back to the browser.
    for key in SECRET_FIELDS:
        if not incoming.get(key):
            incoming.pop(key, None)
    current.update(incoming)
    for key in new_settings.clear_secrets:
        current[key] = ""
    await save_settings(account_id, current)
    return _masked(current)
