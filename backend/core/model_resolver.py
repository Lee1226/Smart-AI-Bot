"""Resolve a run's selected model from the account-scoped model repository."""
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from db.models import LLMConnection, LLMModel


def _cipher() -> Fernet:
    configured = os.getenv("SAB_LLM_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("SAB_LLM_ENCRYPTION_KEY must be a valid Fernet key") from exc
    data_dir = Path(os.getenv("SAB_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    key_path = data_dir / ".llm_encryption_key"
    try:
        return Fernet(key_path.read_bytes().strip())
    except FileNotFoundError as exc:
        raise RuntimeError("LLM credential encryption key is missing") from exc
    except ValueError as exc:
        raise RuntimeError("LLM credential encryption key is invalid") from exc


def _decrypt(value: str) -> str:
    return _cipher().decrypt(value.encode("ascii")).decode("utf-8") if value else ""


async def validate_model_selection(db, account_id: str, model_id: str):
    """Return an enabled model and its account-owned connection, or reject it."""
    result = await db.execute(
        select(LLMModel, LLMConnection)
        .join(LLMConnection, LLMConnection.id == LLMModel.connection_id)
        .where(
            LLMModel.id == model_id,
            LLMModel.account_id == account_id,
            LLMModel.enabled.is_(True),
            LLMConnection.account_id == account_id,
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=422, detail="所选模型不存在、已停用或不属于当前账号")
    return row[0], row[1]


async def resolve_model_config(db, account_id: str, model_id: str) -> dict:
    """Resolve provider, model and decrypted credentials for one run."""
    model, connection = await validate_model_selection(db, account_id, model_id)
    return {
        "provider": connection.provider,
        "model": model.model_name,
        "api_key": _decrypt(connection.api_key_enc),
        "api_base": connection.base_url or "",
        "aws_access_key_id": _decrypt(connection.aws_access_key_id_enc),
        "aws_secret_access_key": _decrypt(connection.aws_secret_access_key_enc),
        "aws_region_name": connection.aws_region_name or "us-east-1",
        "model_id": model.id,
        "connection_id": connection.id,
    }


def apply_model_credentials(config: dict) -> None:
    """Apply Bedrock credentials for LiteLLM's boto3-based provider."""
    if config.get("provider") != "bedrock":
        return
    access = config.get("aws_access_key_id") or ""
    secret = config.get("aws_secret_access_key") or ""
    region = config.get("aws_region_name") or "us-east-1"
    if access and secret:
        os.environ["AWS_ACCESS_KEY_ID"] = access
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret
    os.environ["AWS_REGION_NAME"] = region
    os.environ["AWS_DEFAULT_REGION"] = region
    os.environ["AWS_REGION"] = region
