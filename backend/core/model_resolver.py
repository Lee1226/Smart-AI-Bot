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
        "provider_kwargs": (
            {
                **(
                    {
                        "aws_access_key_id": _decrypt(connection.aws_access_key_id_enc),
                        "aws_secret_access_key": _decrypt(connection.aws_secret_access_key_enc),
                    }
                    if connection.aws_access_key_id_enc and connection.aws_secret_access_key_enc
                    else {}
                ),
                "aws_region_name": connection.aws_region_name or "us-east-1",
            }
            if (connection.provider or "").lower() == "bedrock"
            else {}
        ),
    }
