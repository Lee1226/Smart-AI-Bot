"""Settings — per-account LLM keys and model config, persisted to the DB.

Each account owns its own settings blob (isolated from other tenants and from
the operator).  Reading/writing requires owner/admin of that account.
"""
from pathlib import Path
import os
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


# ── LLM connection repository ────────────────────────────────────────────────
import base64
import hashlib
import json
from datetime import datetime
from cryptography.fernet import Fernet
from sqlalchemy import func
from db.models import LLMConnection, LLMModel


def _llm_cipher() -> Fernet:
    # Reuse the installation's persisted secret so encrypted credentials remain
    # readable after restart; never expose this key through an API response.
    from auth.jwt_handler import JWT_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256((JWT_SECRET + ":llm-connections:v1").encode()).digest())
    return Fernet(key)


def _encrypt_secret(value: str) -> str:
    return _llm_cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def _connection_dict(row: LLMConnection, model_count: int = 0) -> dict:
    return {
        "id": row.id, "name": row.name, "provider": row.provider,
        "base_url": row.base_url, "has_api_key": bool(row.api_key_enc),
        "has_aws_credentials": bool(row.aws_access_key_id_enc and row.aws_secret_access_key_enc),
        "aws_region_name": row.aws_region_name, "notes": row.notes,
        "last_test_status": row.last_test_status, "last_test_message": row.last_test_message,
        "last_tested_at": row.last_tested_at.isoformat() if row.last_tested_at else None,
        "model_count": model_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _model_dict(row: LLMModel) -> dict:
    try:
        capabilities = json.loads(row.capabilities_json or '["text"]')
    except (TypeError, ValueError):
        capabilities = ["text"]
    return {"id": row.id, "connection_id": row.connection_id, "model_name": row.model_name,
            "description": row.description, "capabilities": capabilities, "enabled": bool(row.enabled),
            "created_at": row.created_at.isoformat() if row.created_at else None}


class LLMConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=40)
    base_url: str = ""
    api_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region_name: str = "us-east-1"
    notes: str = ""


class LLMConnectionUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    clear_aws_credentials: bool = False
    aws_region_name: Optional[str] = None
    notes: Optional[str] = None


class LLMModelInput(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    description: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["text"])
    enabled: bool = True


async def _owned_connection(db: AsyncSession, account_id: str, connection_id: str):
    row = await db.get(LLMConnection, connection_id)
    if row is None or row.account_id != account_id:
        raise HTTPException(status_code=404, detail="Model connection not found")
    return row


@router.get("/llm-connections")
async def list_llm_connections(request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    rows = (await db.execute(select(LLMConnection).where(LLMConnection.account_id == account_id).order_by(LLMConnection.created_at.desc()))).scalars().all()
    result = []
    for row in rows:
        count = await db.scalar(select(func.count(LLMModel.id)).where(LLMModel.connection_id == row.id, LLMModel.account_id == account_id))
        result.append(_connection_dict(row, count or 0))
    return result


@router.post("/llm-connections")
async def create_llm_connection(payload: LLMConnectionCreate, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    if not payload.api_key and payload.provider not in {"ollama", "bedrock"} and not (payload.aws_access_key_id and payload.aws_secret_access_key):
        raise HTTPException(status_code=422, detail="API Key is required for this provider")
    row = LLMConnection(account_id=account_id, name=payload.name.strip(), provider=payload.provider.strip().lower(),
        base_url=payload.base_url.strip(), api_key_enc=_encrypt_secret(payload.api_key),
        aws_access_key_id_enc=_encrypt_secret(payload.aws_access_key_id),
        aws_secret_access_key_enc=_encrypt_secret(payload.aws_secret_access_key),
        aws_region_name=payload.aws_region_name.strip() or "us-east-1", notes=payload.notes.strip())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Connection name already exists")
    await db.refresh(row)
    return _connection_dict(row)


@router.put("/llm-connections/{connection_id}")
async def update_llm_connection(connection_id: str, payload: LLMConnectionUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    row = await _owned_connection(db, require_account_id(request), connection_id)
    values = payload.model_dump(exclude_unset=True)
    if values.pop("clear_api_key", False): row.api_key_enc = ""
    if values.pop("clear_aws_credentials", False):
        row.aws_access_key_id_enc = ""; row.aws_secret_access_key_enc = ""
    if values.get("api_key"):
        row.api_key_enc = _encrypt_secret(values.pop("api_key"))
    else: values.pop("api_key", None)
    if values.get("aws_access_key_id"):
        row.aws_access_key_id_enc = _encrypt_secret(values.pop("aws_access_key_id"))
    else: values.pop("aws_access_key_id", None)
    if values.get("aws_secret_access_key"):
        row.aws_secret_access_key_enc = _encrypt_secret(values.pop("aws_secret_access_key"))
    else: values.pop("aws_secret_access_key", None)
    connection_changed = any(key in values for key in ("provider", "base_url")) or bool(payload.api_key) or bool(payload.aws_access_key_id) or bool(payload.aws_secret_access_key) or payload.clear_api_key or payload.clear_aws_credentials
    for key, value in values.items():
        if value is not None and hasattr(row, key): setattr(row, key, value.strip() if isinstance(value, str) else value)
    if connection_changed:
        row.last_test_status = "untested"
        row.last_test_message = ""
        row.last_tested_at = None
    row.updated_at = datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Connection name already exists")
    await db.refresh(row)
    count = await db.scalar(select(func.count(LLMModel.id)).where(LLMModel.connection_id == row.id))
    return _connection_dict(row, count or 0)


@router.delete("/llm-connections/{connection_id}")
async def delete_llm_connection(connection_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    row = await _owned_connection(db, require_account_id(request), connection_id)
    count = await db.scalar(select(func.count(LLMModel.id)).where(LLMModel.connection_id == row.id, LLMModel.account_id == row.account_id))
    if count:
        raise HTTPException(status_code=409, detail="Delete or move the models under this connection first")
    await db.delete(row); await db.commit()
    return {"ok": True}


@router.get("/llm-connections/{connection_id}/models")
async def list_llm_models(connection_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    await _owned_connection(db, account_id, connection_id)
    rows = (await db.execute(select(LLMModel).where(LLMModel.account_id == account_id, LLMModel.connection_id == connection_id).order_by(LLMModel.created_at.desc()))).scalars().all()
    return [_model_dict(row) for row in rows]


@router.post("/llm-connections/{connection_id}/models")
async def create_llm_model(connection_id: str, payload: LLMModelInput, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    await _owned_connection(db, account_id, connection_id)
    row = LLMModel(account_id=account_id, connection_id=connection_id, model_name=payload.model_name.strip(),
        description=payload.description.strip(), capabilities_json=json.dumps(payload.capabilities), enabled=payload.enabled)
    db.add(row)
    try: await db.commit()
    except IntegrityError:
        await db.rollback(); raise HTTPException(status_code=409, detail="This model already exists on the connection")
    await db.refresh(row)
    return _model_dict(row)


@router.put("/llm-models/{model_id}")
async def update_llm_model(model_id: str, payload: LLMModelInput, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    row = await db.get(LLMModel, model_id)
    if row is None or row.account_id != account_id: raise HTTPException(status_code=404, detail="Model not found")
    row.model_name = payload.model_name.strip(); row.description = payload.description.strip()
    row.capabilities_json = json.dumps(payload.capabilities); row.enabled = payload.enabled
    row.updated_at = datetime.utcnow()
    try: await db.commit()
    except IntegrityError:
        await db.rollback(); raise HTTPException(status_code=409, detail="This model already exists on the connection")
    return _model_dict(row)


@router.delete("/llm-models/{model_id}")
async def delete_llm_model(model_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    row = await db.get(LLMModel, model_id)
    if row is None or row.account_id != require_account_id(request): raise HTTPException(status_code=404, detail="Model not found")
    await db.delete(row); await db.commit()
    return {"ok": True}


@router.post("/llm-connections/{connection_id}/test")
async def test_llm_connection(connection_id: str, request: Request, model_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    row = await _owned_connection(db, account_id, connection_id)
    chosen = None
    if model_id:
        chosen = await db.get(LLMModel, model_id)
        if chosen is None or chosen.account_id != account_id or chosen.connection_id != connection_id:
            raise HTTPException(status_code=404, detail="Model not found on this connection")
    else:
        chosen = (await db.execute(select(LLMModel).where(
            LLMModel.account_id == account_id, LLMModel.connection_id == connection_id,
            LLMModel.enabled.is_(True)
        ).limit(1))).scalar_one_or_none()
    if chosen is None:
        raise HTTPException(status_code=422, detail="Add an enabled model before testing this connection")

    cipher = _llm_cipher()
    api_key = cipher.decrypt(row.api_key_enc.encode()).decode() if row.api_key_enc else ""
    aws_access = cipher.decrypt(row.aws_access_key_id_enc.encode()).decode() if row.aws_access_key_id_enc else ""
    aws_secret = cipher.decrypt(row.aws_secret_access_key_enc.encode()).decode() if row.aws_secret_access_key_enc else ""
    try:
        from litellm import acompletion
        from agent.base import build_model_kwargs
        model_name, extra = build_model_kwargs(row.provider, chosen.model_name, row.base_url)
        kwargs = {
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8,
            "timeout": 20,
            **extra,
        }
        if api_key:
            kwargs["api_key"] = api_key
        old_env = {key: os.environ.get(key) for key in (
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION_NAME",
            "AWS_DEFAULT_REGION", "AWS_REGION"
        )}
        if row.provider == "bedrock":
            if aws_access and aws_secret:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret
            os.environ["AWS_REGION_NAME"] = row.aws_region_name
            os.environ["AWS_DEFAULT_REGION"] = row.aws_region_name
            os.environ["AWS_REGION"] = row.aws_region_name
        try:
            await acompletion(**kwargs)
        finally:
            if row.provider == "bedrock":
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        row.last_test_status = "success"
        row.last_test_message = "模型调用成功"
    except Exception as exc:
        row.last_test_status = "failed"
        error_message = str(exc)[:500]
        for secret in (api_key, aws_access, aws_secret):
            if secret:
                error_message = error_message.replace(secret, "[REDACTED]")
        row.last_test_message = error_message
    row.last_tested_at = datetime.utcnow()
    await db.commit()
    if row.last_test_status == "failed":
        raise HTTPException(status_code=502, detail=row.last_test_message)
    return {
        "ok": True, "message": row.last_test_message,
        "tested_model": chosen.model_name, "tested_at": row.last_tested_at.isoformat()
    }
