"""Resolve a run's selected model from the account-scoped model repository."""
from core.llm_crypto import decrypt_secret
from fastapi import HTTPException
from sqlalchemy import select

from db.models import LLMConnection, LLMModel

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
        "api_key": decrypt_secret(connection.api_key_enc),
        "api_base": connection.base_url or "",
        "aws_access_key_id": decrypt_secret(connection.aws_access_key_id_enc),
        "aws_secret_access_key": decrypt_secret(connection.aws_secret_access_key_enc),
        "aws_region_name": connection.aws_region_name or "us-east-1",
        "model_id": model.id,
        "connection_id": connection.id,
        "provider_kwargs": (
            {
                **(
                    {
                        "aws_access_key_id": decrypt_secret(connection.aws_access_key_id_enc),
                        "aws_secret_access_key": decrypt_secret(connection.aws_secret_access_key_enc),
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


async def resolve_model_fallbacks(db, account_id: str, model_id: str):
    """Resolve only the selected model's direct fallback list (never recursively)."""
    import json
    import logging

    from agent.llm import ModelTarget

    logger = logging.getLogger(__name__)
    model, _ = await validate_model_selection(db, account_id, model_id)
    try:
        fallback_ids = json.loads(model.fallback_model_ids_json or "[]")
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed fallback model list for model %s", model_id)
        return []
    if not isinstance(fallback_ids, list):
        logger.warning("Ignoring non-list fallback model config for model %s", model_id)
        return []

    targets = []
    seen = {model_id}
    for fallback_id in fallback_ids:
        if not isinstance(fallback_id, str) or not fallback_id or fallback_id in seen:
            continue
        seen.add(fallback_id)
        try:
            fallback_model, connection = await validate_model_selection(
                db, account_id, fallback_id
            )
        except HTTPException:
            logger.warning(
                "Skipping unavailable fallback model %s configured by %s",
                fallback_id, model_id,
            )
            continue
        provider_kwargs = {}
        if (connection.provider or "").lower() == "bedrock":
            if connection.aws_access_key_id_enc and connection.aws_secret_access_key_enc:
                provider_kwargs.update({
                    "aws_access_key_id": decrypt_secret(connection.aws_access_key_id_enc),
                    "aws_secret_access_key": decrypt_secret(connection.aws_secret_access_key_enc),
                })
            provider_kwargs["aws_region_name"] = connection.aws_region_name or "us-east-1"
        targets.append(ModelTarget(
            provider=connection.provider,
            model=fallback_model.model_name,
            api_key=decrypt_secret(connection.api_key_enc),
            api_base=connection.base_url or "",
            provider_kwargs=provider_kwargs,
        ))
    return targets
