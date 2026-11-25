import os
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = Path(os.getenv("SAB_DB_PATH", str(Path(os.getenv("SAB_DATA_DIR", str(_DEFAULT_DATA_DIR))) / "db.sqlite3")))
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def backfill_legacy_run_model_ids(session) -> int:
    """Attach legacy runs to a repository model only when the match is unambiguous.

    Matching is account-scoped and requires an exact model name plus the
    historical provider identity. Generic adapter IDs are not treated as
    provider identities because multiple providers may share one adapter.
    Disabled models are included so historical identity is preserved.
    """
    from sqlalchemy import or_, select
    from db.models import LLMConnection, LLMModel, LLMProvider, TestRun, TestSuite

    unresolved = (await session.execute(
        select(TestRun).where(
            or_(TestRun.model_id.is_(None), TestRun.model_id == "")
        )
    )).scalars().all()
    updated = 0

    for run in unresolved:
        provider = (run.provider or "").strip()
        model_name = (run.model or "").strip()
        if not provider or not model_name:
            continue

        account_id = run.account_id
        suite = None
        if not account_id and run.suite_id:
            suite = await session.get(TestSuite, run.suite_id)
            account_id = suite.account_id if suite else None
        if not account_id:
            continue

        # If the historical value is a shared adapter ID (for example,
        # openai-compatible), do not use it as a provider fallback.
        shared_adapter = await session.scalar(
            select(LLMProvider.id).where(
                LLMProvider.account_id == account_id,
                LLMProvider.adapter_id == provider,
                LLMProvider.provider_id != provider,
            ).limit(1)
        )

        provider_match = [LLMProvider.provider_id == provider]
        if not shared_adapter:
            provider_match.append(LLMConnection.provider == provider)

        candidates = (await session.execute(
            select(LLMModel.id)
            .join(LLMConnection, LLMConnection.id == LLMModel.connection_id)
            .outerjoin(LLMProvider, LLMProvider.id == LLMConnection.provider_catalog_id)
            .where(
                LLMModel.account_id == account_id,
                LLMConnection.account_id == account_id,
                LLMModel.model_name == model_name,
                or_(*provider_match),
            )
            .distinct()
        )).scalars().all()

        if len(candidates) != 1:
            continue

        run.model_id = candidates[0]
        if not run.account_id:
            run.account_id = account_id
        updated += 1

    if updated:
        await session.commit()
    return updated


async def init_db():
    # Import models so every table is registered on Base.metadata before
    # create_all runs — independent of import order at the call site.
    from db import models  # noqa: F401
    from db import exploration_models  # noqa: F401
    from db import skill_models  # noqa: F401
    from db import skill_validation_models  # noqa: F401
    from db import audio_models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ── Auto-migrate: add new columns to existing tables ─────────────
        # SQLite supports ADD COLUMN but not DROP/RENAME, so we just add
        # missing columns idempotently.

        async def _ensure_columns(table: str, columns: dict[str, str]):
            """Add missing columns to an existing table. columns = {name: DDL_type}"""
            existing = {
                row[1]
                for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            }
            for col, ddl in columns.items():
                if col not in existing:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

        await _ensure_columns("accounts", {
            "is_operator": "BOOLEAN DEFAULT 0",
        })
        await _ensure_columns("llm_connections", {"provider_catalog_id": "VARCHAR"})
        await _ensure_columns("explore_sessions", {"model_id": "VARCHAR DEFAULT ''"})

        await _ensure_columns("project_test_accounts", {"verification_code_enc": "TEXT DEFAULT ''"})
        await _ensure_columns("test_results", {
            "is_starred": "BOOLEAN DEFAULT 0",
            "action_history_json": "TEXT DEFAULT ''",
            "total_tokens": "INTEGER DEFAULT 0",
        })

        await _ensure_columns("test_step_logs", {
            "prompt_tokens": "INTEGER DEFAULT 0",
            "completion_tokens": "INTEGER DEFAULT 0",
            "total_tokens": "INTEGER DEFAULT 0",
            "perception_ms": "INTEGER DEFAULT 0",
            "llm_ms": "INTEGER DEFAULT 0",
            "action_ms": "INTEGER DEFAULT 0",
            "subgoal_index": "INTEGER",
            "subgoal_desc": "TEXT DEFAULT ''",
        })

        await _ensure_columns("test_cases", {
            "parameters": "TEXT DEFAULT ''",
            "checkpoints": "TEXT DEFAULT ''",
            "loop_task": "BOOLEAN DEFAULT 0",
        })
        await _ensure_columns("test_cases", {
            "replay_steps": "TEXT DEFAULT ''",
            "recorder_events": "TEXT DEFAULT ''",
            "lifecycle_actions_json": "TEXT DEFAULT '{}'",
        })

        await _ensure_columns("step_nodes", {
            "source_id": "VARCHAR",
            "ref_id": "VARCHAR",
        })

        migrate_project_ids = "project_id" not in {
            row[1] for row in (await conn.execute(text("PRAGMA table_info(test_suites)"))).fetchall()
        }
        await _ensure_columns("test_suites", {
            "app_package": "VARCHAR DEFAULT ''",
            "project_id": "VARCHAR DEFAULT ''",
        })

        # suite_id + task_keyword were added to LessonLearned after the table
        # first shipped. Without these, the lesson load/save queries reference
        # non-existent columns and silently fail — i.e. NO cross-run lessons at
        # all on pre-existing databases.
        await _ensure_columns("lessons_learned", {
            "suite_id": "VARCHAR",
            "task_keyword": "VARCHAR DEFAULT ''",
            "account_id": "VARCHAR",
        })

        # ── Multi-tenancy: account_id on every resource table ──────────
        await _ensure_columns("devices", {"account_id": "VARCHAR", "connection_type": "VARCHAR DEFAULT 'portal'", "adb_serial": "VARCHAR", "adb_auto_connect": "BOOLEAN DEFAULT 1"})
        await _ensure_columns("test_suites", {"account_id": "VARCHAR"})
        await _ensure_columns("test_suites", {"tag": "VARCHAR DEFAULT ''"})
        await _ensure_columns("test_cases", {"account_id": "VARCHAR"})
        await _ensure_columns("step_nodes", {"account_id": "VARCHAR"})
        await _ensure_columns("test_runs", {"account_id": "VARCHAR", "model_id": "VARCHAR DEFAULT ''"})
        await _ensure_columns("test_runs", {"webhook_url": "VARCHAR DEFAULT ''",
            "test_account_id": "VARCHAR DEFAULT ''", "test_account_name": "VARCHAR DEFAULT ''"})
        await _ensure_columns("test_results", {"account_id": "VARCHAR"})
        await _ensure_columns("test_step_logs", {"account_id": "VARCHAR"})

    # ── Seed the account-scoped LLM provider catalog exactly once ───────────
    from db.models import Account, LLMProvider, LLMProviderCatalogState
    from db.seed_llm_providers import DEFAULT_LLM_PROVIDERS
    from sqlalchemy import select as _select
    async with AsyncSessionLocal() as provider_session:
        account_ids = (await provider_session.execute(_select(Account.id))).scalars().all()
        for account_id in account_ids:
            state = await provider_session.get(LLMProviderCatalogState, account_id)
            if state is None:
                existing_provider_ids = set((await provider_session.execute(_select(LLMProvider.provider_id).where(LLMProvider.account_id == account_id))).scalars().all())
                for item in DEFAULT_LLM_PROVIDERS:
                    if item["provider_id"] not in existing_provider_ids:
                        provider_session.add(LLMProvider(account_id=account_id, **item))
                provider_session.add(LLMProviderCatalogState(account_id=account_id))
                await provider_session.flush()
            catalog_rows = (await provider_session.execute(_select(LLMProvider).where(LLMProvider.account_id == account_id))).scalars().all()
            connections = (await provider_session.execute(_select(models.LLMConnection).where(models.LLMConnection.account_id == account_id, models.LLMConnection.provider_catalog_id.is_(None)))).scalars().all()
            for connection in connections:
                match = next((item for item in catalog_rows if item.provider_id == connection.provider or item.adapter_id == connection.provider), None)
                if match is not None:
                    connection.provider_catalog_id = match.id
                    connection.provider = match.adapter_id
        await provider_session.commit()

    # Backfill historical task model IDs only after provider identities have
    # been normalized and linked to the provider catalog.
    async with AsyncSessionLocal() as run_session:
        await backfill_legacy_run_model_ids(run_session)

    from sqlalchemy import select as _select
    # ── Migrate legacy per-device app metadata into shared catalog tables ──
    # Safe to run on every startup; old rows remain available for rollback.
    from db.models import DeviceApp, AppCatalog, DeviceAppInstallation
    async with AsyncSessionLocal() as session:
        legacy_apps = (await session.execute(_select(DeviceApp))).scalars().all()
        for legacy in legacy_apps:
            catalog = (await session.execute(_select(AppCatalog).where(
                AppCatalog.account_id == legacy.account_id, AppCatalog.package_name == legacy.package_name
            ))).scalar_one_or_none()
            if not catalog:
                catalog = AppCatalog(account_id=legacy.account_id, package_name=legacy.package_name,
                    app_name=legacy.app_name or "", is_test_app=bool(legacy.is_test_app))
                session.add(catalog)
            else:
                if not catalog.app_name and legacy.app_name:
                    catalog.app_name = legacy.app_name
                catalog.is_test_app = bool(catalog.is_test_app or legacy.is_test_app)
            install = (await session.execute(_select(DeviceAppInstallation).where(
                DeviceAppInstallation.account_id == legacy.account_id,
                DeviceAppInstallation.device_id == legacy.device_id,
                DeviceAppInstallation.package_name == legacy.package_name
            ))).scalar_one_or_none()
            if not install:
                session.add(DeviceAppInstallation(account_id=legacy.account_id,
                    device_id=legacy.device_id, package_name=legacy.package_name,
                    installed=bool(legacy.installed), first_seen_at=legacy.first_seen_at,
                    last_seen_at=legacy.last_seen_at))
            else:
                install.installed = bool(install.installed or legacy.installed)
        await session.commit()

    # ── One-time migration: flat cases -> step-tree (idempotent per suite) ──
    from db.migrate_step_tree import migrate_suite_to_step_tree
    from db.models import TestSuite
    from sqlalchemy import select as _select
    async with AsyncSessionLocal() as _s:
        suite_ids = (await _s.execute(_select(TestSuite.id))).scalars().all()
        for sid in suite_ids:
            await migrate_suite_to_step_tree(_s, sid)

    if not migrate_project_ids:
        return

    # Backfill legacy suites only for the operator who owns these local profiles.
    from auth.access import operator_account_id
    from core.projects import match_project
    async with AsyncSessionLocal() as session:
        owner = await operator_account_id(session)
        suites = (await session.execute(_select(TestSuite).where(
            TestSuite.account_id == owner, TestSuite.project_id == ""
        ))).scalars().all()
        for suite in suites:
            project = match_project(suite.app_package)
            if project:
                suite.project_id = project["id"]
        await session.commit()
