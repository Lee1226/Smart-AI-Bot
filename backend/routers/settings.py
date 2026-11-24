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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id, require_role
from core.settings_store import load_settings, save_settings
from core.run_recorder import recording_directory, cleanup_expired_recordings
from db.database import AsyncSessionLocal
from db.models import TestRun, LLMProvider, LLMProviderCatalogState

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
import json
from datetime import datetime
from cryptography.fernet import Fernet
from sqlalchemy import func
from db.models import LLMConnection, LLMModel


def _llm_cipher() -> Fernet:
    # Keep LLM credential encryption independent from JWT signing-key rotation.
    # The key is local to this installation and persists beside the application DB.
    configured = os.getenv("SAB_LLM_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("SAB_LLM_ENCRYPTION_KEY must be a valid Fernet key") from exc

    data_dir = Path(os.getenv("SAB_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    key_path = data_dir / ".llm_encryption_key"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        generated = Fernet.generate_key()
        try:
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(generated)
            key = generated
        except FileExistsError:
            key = key_path.read_bytes()
    try:
        return Fernet(key.strip())
    except ValueError as exc:
        raise RuntimeError(f"Invalid LLM encryption key file: {key_path}") from exc


def _encrypt_secret(value: str) -> str:
    return _llm_cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def _connection_dict(row: LLMConnection, model_count: int = 0) -> dict:
    return {
        "id": row.id, "name": row.name, "provider": row.provider, "provider_catalog_id": row.provider_catalog_id,
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



class LLMProviderInput(BaseModel):
    provider_id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=120)
    protocol_type: str = "openai-compatible"
    adapter_id: str = "openai-compatible"
    default_base_url: str = ""
    auth_type: str = "api_key"
    enabled: bool = True
    legacy_api_key_field: str = ""
    legacy_base_url_field: str = ""
    model_presets: list[str] = Field(default_factory=list)


def _provider_dict(row: LLMProvider) -> dict:
    try:
        presets = json.loads(row.model_presets_json or "[]")
    except (TypeError, ValueError):
        presets = []
    return {
        "id": row.id, "provider_id": row.provider_id, "name": row.name,
        "protocol_type": row.protocol_type, "adapter_id": row.adapter_id,
        "default_base_url": row.default_base_url, "auth_type": row.auth_type,
        "enabled": bool(row.enabled), "legacy_api_key_field": row.legacy_api_key_field,
        "legacy_base_url_field": row.legacy_base_url_field, "model_presets": presets,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/llm-providers")
async def list_llm_providers(request: Request, include_disabled: bool = True, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    query = select(LLMProvider).where(LLMProvider.account_id == require_account_id(request))
    if not include_disabled:
        query = query.where(LLMProvider.enabled.is_(True))
    rows = (await db.execute(query.order_by(LLMProvider.name.asc()))).scalars().all()
    return [_provider_dict(row) for row in rows]


@router.post("/llm-providers")
async def create_llm_provider(payload: LLMProviderInput, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    # adapter_id names an implemented protocol adapter, not a provider option.
    supported_adapters = {"openai", "anthropic", "google", "dashscope", "ark", "zhipuai", "groq", "ollama", "bedrock", "openai-compatible"}
    if payload.adapter_id not in supported_adapters:
        raise HTTPException(status_code=422, detail="当前协议适配器尚未实现")
    row = LLMProvider(account_id=account_id, provider_id=payload.provider_id.strip().lower(),
        name=payload.name.strip(), protocol_type=payload.protocol_type.strip(),
        adapter_id=payload.adapter_id, default_base_url=payload.default_base_url.strip(),
        auth_type=payload.auth_type.strip(), enabled=payload.enabled,
        legacy_api_key_field=payload.legacy_api_key_field.strip(),
        legacy_base_url_field=payload.legacy_base_url_field.strip(),
        model_presets_json=json.dumps(payload.model_presets, ensure_ascii=False))
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="供应商标识已存在")
    await db.refresh(row)
    return _provider_dict(row)


@router.put("/llm-providers/{provider_id}")
async def update_llm_provider(provider_id: str, payload: LLMProviderInput, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    row = await db.get(LLMProvider, provider_id)
    if row is None or row.account_id != account_id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if payload.provider_id.strip().lower() != row.provider_id:
        used = await db.scalar(select(func.count(LLMConnection.id)).where(LLMConnection.provider_catalog_id == row.id))
        if used:
            raise HTTPException(status_code=409, detail="该供应商已有连接，不能修改唯一标识")
    supported_adapters = {"openai", "anthropic", "google", "dashscope", "ark", "zhipuai", "groq", "ollama", "bedrock", "openai-compatible"}
    if payload.adapter_id not in supported_adapters:
        raise HTTPException(status_code=422, detail="当前协议适配器尚未实现")
    row.provider_id = payload.provider_id.strip().lower()
    row.name = payload.name.strip()
    row.protocol_type = payload.protocol_type.strip()
    row.adapter_id = payload.adapter_id
    row.default_base_url = payload.default_base_url.strip()
    row.auth_type = payload.auth_type.strip()
    row.enabled = payload.enabled
    row.legacy_api_key_field = payload.legacy_api_key_field.strip()
    row.legacy_base_url_field = payload.legacy_base_url_field.strip()
    row.model_presets_json = json.dumps(payload.model_presets, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="供应商标识已存在")
    return _provider_dict(row)


@router.delete("/llm-providers/{provider_id}")
async def delete_llm_provider(provider_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    row = await db.get(LLMProvider, provider_id)
    if row is None or row.account_id != account_id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    count = await db.scalar(select(func.count(LLMConnection.id)).where(LLMConnection.provider_catalog_id == row.id))
    if count:
        raise HTTPException(status_code=409, detail="该供应商仍有关联连接，请先删除或迁移这些连接")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


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
    provider_row = (await db.execute(select(LLMProvider).where(
        LLMProvider.account_id == account_id, LLMProvider.provider_id == payload.provider.strip().lower(),
        LLMProvider.enabled.is_(True)
    ))).scalar_one_or_none()
    if provider_row is None:
        raise HTTPException(status_code=422, detail="请选择一个已启用的供应商")
    if provider_row.auth_type == "api_key" and not payload.api_key:
        raise HTTPException(status_code=422, detail="该供应商需要 API Key")
    if provider_row.auth_type == "aws" and not (payload.aws_access_key_id and payload.aws_secret_access_key):
        raise HTTPException(status_code=422, detail="该供应商需要 AWS 凭据")
    row = LLMConnection(account_id=account_id, name=payload.name.strip(), provider=provider_row.adapter_id,
        provider_catalog_id=provider_row.id,
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
    if "provider" in values and values["provider"] is not None:
        provider_row = (await db.execute(select(LLMProvider).where(
            LLMProvider.account_id == row.account_id,
            LLMProvider.provider_id == str(values["provider"]).strip().lower(),
            LLMProvider.enabled.is_(True)
        ))).scalar_one_or_none()
        if provider_row is None:
            raise HTTPException(status_code=422, detail="请选择一个已启用的供应商")
        values["provider"] = provider_row.adapter_id
        row.provider_catalog_id = provider_row.id
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
    connection = await _owned_connection(db, account_id, connection_id)
    connection.last_test_status = "untested"
    connection.last_test_message = ""
    connection.last_tested_at = None
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
    connection = await _owned_connection(db, account_id, row.connection_id)
    connection.last_test_status = "untested"
    connection.last_test_message = ""
    connection.last_tested_at = None
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
    connection = await _owned_connection(db, row.account_id, row.connection_id)
    connection.last_test_status = "untested"
    connection.last_test_message = ""
    connection.last_tested_at = None
    await db.delete(row); await db.commit()
    return {"ok": True}


@router.post("/llm-connections/import-existing")
async def import_existing_llm_settings(request: Request, db: AsyncSession = Depends(get_db)):
    """Copy configured legacy account providers into the encrypted model repository."""
    require_role(request, "owner", "admin")
    account_id = require_account_id(request)
    settings = await load_settings(account_id)
    imported_connections = 0
    imported_models = 0
    provider_aliases = {"gemini": "google", "zhipu": "zhipuai"}
    default_provider = provider_aliases.get((settings.get("default_provider") or "openai").lower(), (settings.get("default_provider") or "openai").lower())
    default_model = (settings.get("default_model") or "").strip()
    verifier_provider_raw = (settings.get("verifier_provider") or "").lower()
    verifier_provider = provider_aliases.get(verifier_provider_raw, verifier_provider_raw)
    verifier_model = (settings.get("verifier_model") or "").strip()
    provider_rows = (await db.execute(select(LLMProvider).where(LLMProvider.account_id == account_id).order_by(LLMProvider.name.asc()))).scalars().all()
    candidates = []
    for provider_row in provider_rows:
        key_field = provider_row.legacy_api_key_field
        base_field = provider_row.legacy_base_url_field
        api_key = (settings.get(key_field) or "").strip() if key_field else ""
        aws_access = (settings.get("aws_access_key_id") or "").strip() if provider_row.auth_type == "aws" else ""
        aws_secret = (settings.get("aws_secret_access_key") or "").strip() if provider_row.auth_type == "aws" else ""
        configured_base_url = (settings.get(base_field) or "").strip() if base_field else ""
        default_base_url = provider_row.default_base_url
        if provider_row.provider_id == "ollama":
            default_base_url = (settings.get("ollama_base_url") or default_base_url).strip()
        presets = json.loads(provider_row.model_presets_json or "[]")
        candidates.append({"catalog": provider_row, "provider": provider_row.provider_id, "adapter": provider_row.adapter_id, "label": provider_row.name, "base_url": configured_base_url or default_base_url, "api_key": api_key, "aws_access": aws_access, "aws_secret": aws_secret, "aws_region": (settings.get("aws_region_name") or "us-east-1").strip(), "models": presets})
    marker = "[imported-from-platform-settings]"
    for item in candidates:
        provider = item["provider"]
        row = (await db.execute(select(LLMConnection).where(
            LLMConnection.account_id == account_id,
            LLMConnection.provider_catalog_id == item["catalog"].id,
            LLMConnection.notes.contains(marker),
        ))).scalar_one_or_none()
        if row is None:
            base_name = f'{item["label"]}（平台配置导入）'
            connection_name = base_name
            suffix = 2
            while await db.scalar(select(func.count(LLMConnection.id)).where(
                LLMConnection.account_id == account_id,
                LLMConnection.name == connection_name,
            )):
                connection_name = f"{base_name} {suffix}"
                suffix += 1
            row = LLMConnection(
                account_id=account_id, name=connection_name,
                provider=item["adapter"], provider_catalog_id=item["catalog"].id, base_url=item["base_url"],
                api_key_enc=_encrypt_secret(item["api_key"]),
                aws_access_key_id_enc=_encrypt_secret(item["aws_access"]),
                aws_secret_access_key_enc=_encrypt_secret(item["aws_secret"]),
                aws_region_name=item["aws_region"],
                notes=f'{marker} 原平台设置迁移记录；后续修改请在模型配置仓库维护。',
            )
            db.add(row)
            await db.flush()
            imported_connections += 1
        if row.provider_catalog_id is None:
            row.provider_catalog_id = item["catalog"].id
            row.provider = item["adapter"]
        model_names = list(dict.fromkeys(item["models"] + [
            default_model if default_provider == provider else "",
            verifier_model if verifier_provider == provider else "",
        ]))
        for model_name in filter(None, model_names):
            existing_model = (await db.execute(select(LLMModel).where(
                LLMModel.account_id == account_id, LLMModel.connection_id == row.id,
                LLMModel.model_name == model_name,
            ))).scalar_one_or_none()
            if existing_model is not None:
                continue
            is_selected = (
                (default_provider == provider and default_model == model_name)
                or (verifier_provider == provider and verifier_model == model_name)
            )
            db.add(LLMModel(
                account_id=account_id, connection_id=row.id, model_name=model_name,
                description="原平台设置当前使用模型" if is_selected else "平台内置模型预设（默认停用）",
                capabilities_json='["text"]', enabled=is_selected,
            ))
            imported_models += 1
    await db.commit()
    return {
        "imported_connections": imported_connections, "imported_models": imported_models,
        "skipped_providers": [],
        "message": "已按供应商数据库目录导入连接；未配置凭据的连接保留为空，补充凭据后即可测试。当前默认模型和 Verifier 模型已启用，其余预设模型默认停用。",
    }


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
