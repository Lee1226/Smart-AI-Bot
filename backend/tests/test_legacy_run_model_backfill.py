"""Tests for safe backfilling of legacy TestRun.model_id values."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base, backfill_legacy_run_model_ids
from db.models import (
    Account,
    LLMConnection,
    LLMModel,
    LLMProvider,
    TestRun,
    TestSuite,
)


@pytest.mark.asyncio
async def test_backfill_matches_unique_model_using_shared_adapter():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Account.__table__,
        TestSuite.__table__,
        LLMProvider.__table__,
        LLMConnection.__table__,
        LLMModel.__table__,
        TestRun.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        account = Account(name="backfill-account-unique")
        session.add(account)
        await session.flush()

        suite = TestSuite(
            name="legacy suite",
            source_format="xmind",
            account_id=account.id,
        )
        provider = LLMProvider(
            account_id=account.id,
            provider_id="vendor-a",
            name="Vendor A",
            adapter_id="openai-compatible",
        )
        session.add_all([suite, provider])
        await session.flush()

        connection = LLMConnection(
            account_id=account.id,
            name="Vendor A connection",
            provider="openai-compatible",
            provider_catalog_id=provider.id,
        )
        session.add(connection)
        await session.flush()

        model = LLMModel(
            account_id=account.id,
            connection_id=connection.id,
            model_name="chat-model",
        )
        run = TestRun(
            suite_id=suite.id,
            device_id="device-1",
            account_id=account.id,
            provider="openai-compatible",
            model="chat-model",
            model_id="",
        )
        session.add_all([model, run])
        await session.flush()
        expected_model_id = model.id
        run_id = run.id

        updated = await backfill_legacy_run_model_ids(session)

        assert updated == 1
        refreshed = await session.get(TestRun, run_id)
        assert refreshed.model_id == expected_model_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_leaves_ambiguous_same_name_models_unmatched():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Account.__table__,
        TestSuite.__table__,
        LLMProvider.__table__,
        LLMConnection.__table__,
        LLMModel.__table__,
        TestRun.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        account = Account(name="backfill-account-ambiguous")
        session.add(account)
        await session.flush()

        suite = TestSuite(
            name="legacy suite",
            source_format="xmind",
            account_id=account.id,
        )
        provider = LLMProvider(
            account_id=account.id,
            provider_id="vendor-a",
            name="Vendor A",
            adapter_id="openai-compatible",
        )
        session.add_all([suite, provider])
        await session.flush()

        run = TestRun(
            suite_id=suite.id,
            device_id="device-1",
            account_id=account.id,
            provider="openai-compatible",
            model="chat-model",
            model_id="",
        )
        session.add(run)

        for suffix in ("one", "two"):
            connection = LLMConnection(
                account_id=account.id,
                name=f"Vendor A {suffix}",
                provider="openai-compatible",
                provider_catalog_id=provider.id,
            )
            session.add(connection)
            await session.flush()
            session.add(LLMModel(
                account_id=account.id,
                connection_id=connection.id,
                model_name="chat-model",
            ))

        await session.flush()
        run_id = run.id
        updated = await backfill_legacy_run_model_ids(session)

        assert updated == 0
        refreshed = await session.get(TestRun, run_id)
        assert refreshed.model_id == ""

    await engine.dispose()
