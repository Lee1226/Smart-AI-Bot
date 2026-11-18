"""Settings — per-account LLM keys and model config, persisted to the DB.

Each account owns its own settings blob (isolated from other tenants and from
the operator).  Reading/writing requires owner/admin of that account.
"""
from pathlib import Path
from typing import Optional
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id, require_role
from core.settings_store import load_settings, save_settings
from core.run_recorder import recording_directory, cleanup_expired_recordings
from db.database import AsyncSessionLocal
from db.models import TestRun

router = APIRouter(prefix="/api/settings", tags=["settings"])


class Settings(BaseModel):
    openai_api_key: Optional[str] = ""
    anthropic_api_key: Optional[str] = ""
    anthropic_base_url: Optional[str] = ""   # optional override (e.g. internal LiteLLM proxy URL)
    gemini_api_key: Optional[str] = ""
    zhipu_api_key: Optional[str] = ""
    dashscope_api_key: Optional[str] = ""
    dashscope_base_url: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
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
    recording_auto_enabled: bool = True
    recording_storage_dir: str = str(Path(__file__).resolve().parent.parent / "data" / "recordings")
    recording_retention_days: int = Field(default=30, ge=1, le=3650)
    configured_secrets: list[str] = Field(default_factory=list)


SECRET_FIELDS = {
    "openai_api_key", "anthropic_api_key", "gemini_api_key", "zhipu_api_key",
    "groq_api_key", "ark_api_key", "dashscope_api_key", "aws_access_key_id", "aws_secret_access_key",
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
async def update_settings(new_settings: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    if set(new_settings.clear_secrets) - SECRET_FIELDS:
        raise HTTPException(status_code=422, detail="Unknown secret field to clear")
    current = await load_settings(account_id)
    old_recording_dir = recording_directory(current)
    incoming = new_settings.model_dump(exclude_none=True, exclude_unset=True,
                                      exclude={"configured_secrets", "clear_secrets"})
    # Blank password fields mean "keep the existing value".  The API never
    # sends stored credentials back to the browser.
    for key in SECRET_FIELDS:
        if not incoming.get(key):
            incoming.pop(key, None)
    current.update(incoming)
    if "recording_storage_dir" in incoming:
        try:
            storage_dir = recording_directory(current)
            storage_dir.mkdir(parents=True, exist_ok=True)
            current["recording_storage_dir"] = str(storage_dir)
            if storage_dir != old_recording_dir:
                owned_run_ids = (await db.execute(select(TestRun.id).where(TestRun.account_id == account_id))).scalars().all()
                for run_id in owned_run_ids:
                    old_file = old_recording_dir / f"{run_id}.mp4"
                    new_file = storage_dir / f"{run_id}.mp4"
                    if old_file.is_file():
                        if new_file.exists():
                            raise HTTPException(status_code=409, detail=f"目标目录已存在同名录像文件：{new_file.name}")
                        shutil.move(str(old_file), str(new_file))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"录像保存目录无效：{exc}")
    for key in new_settings.clear_secrets:
        current[key] = ""
    await save_settings(account_id, current)
    return _masked(current)



@router.get("/recording-storage")
async def get_recording_storage(request: Request, db: AsyncSession = Depends(get_db)):
    """Return real disk usage and recording inventory for the current account."""
    require_role(request, "owner", "admin")
    settings = await load_settings(require_account_id(request))
    directory = recording_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    account_id = require_account_id(request)
    owned_run_ids = set((await db.execute(select(TestRun.id).where(TestRun.account_id == account_id))).scalars().all())
    cleanup_expired_recordings(directory, settings.get("recording_retention_days", 30), owned_run_ids)
    usage = shutil.disk_usage(directory)
    files = [p for p in directory.glob("*.mp4") if p.is_file() and p.stem in owned_run_ids]
    total_bytes = sum(p.stat().st_size for p in files)
    return {
        "directory": str(directory),
        "retention_days": int(settings.get("recording_retention_days", 30)),
        "recording_count": len(files),
        "recording_bytes": total_bytes,
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
    }
