"""Tests for model-repository-only run configuration resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import test_runner


class FakeRunSession:
    def __init__(self, run):
        self.run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, run_id):
        return self.run


@pytest.mark.asyncio
async def test_run_without_model_id_is_rejected(monkeypatch):
    monkeypatch.setattr(
        test_runner,
        "AsyncSessionLocal",
        lambda: FakeRunSession(SimpleNamespace(model_id="")),
    )
    resolve = AsyncMock()
    monkeypatch.setattr("core.model_resolver.resolve_model_config", resolve)

    with pytest.raises(RuntimeError, match="未关联有效的模型仓库配置"):
        await test_runner._resolve_run_model("old-run", "account-1")

    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_model_is_resolved_only_by_model_id(monkeypatch):
    config = {
        "provider": "openai",
        "model": "gpt-test",
        "api_key": "secret",
        "api_base": "https://example.test/v1",
        "provider_kwargs": {},
    }
    monkeypatch.setattr(
        test_runner,
        "AsyncSessionLocal",
        lambda: FakeRunSession(SimpleNamespace(model_id="model-current")),
    )
    resolve = AsyncMock(return_value=config)
    monkeypatch.setattr("core.model_resolver.resolve_model_config", resolve)

    actual = await test_runner._resolve_run_model("run-1", "account-1")

    assert actual is config
    resolve.assert_awaited_once()
    args = resolve.await_args.args
    assert args[1:] == ("account-1", "model-current")


@pytest.mark.asyncio
async def test_disabled_or_deleted_run_model_error_is_not_suppressed(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        test_runner,
        "AsyncSessionLocal",
        lambda: FakeRunSession(SimpleNamespace(model_id="model-disabled")),
    )
    resolve = AsyncMock(side_effect=HTTPException(status_code=422, detail="模型不存在或已停用"))
    monkeypatch.setattr("core.model_resolver.resolve_model_config", resolve)

    with pytest.raises(HTTPException) as exc:
        await test_runner._resolve_run_model("run-2", "account-1")

    assert exc.value.status_code == 422
    assert "停用" in exc.value.detail
