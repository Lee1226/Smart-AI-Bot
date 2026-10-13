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


async def init_db():
    # Import models so every table is registered on Base.metadata before
    # create_all runs — independent of import order at the call site.
    from db import models  # noqa: F401
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
        await _ensure_columns("test_cases", {"replay_steps": "TEXT DEFAULT ''"})

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
        await _ensure_columns("devices", {"account_id": "VARCHAR", "connection_type": "VARCHAR DEFAULT 'portal'"})
        await _ensure_columns("test_suites", {"account_id": "VARCHAR"})
        await _ensure_columns("test_suites", {"tag": "VARCHAR DEFAULT ''"})
        await _ensure_columns("test_cases", {"account_id": "VARCHAR"})
        await _ensure_columns("step_nodes", {"account_id": "VARCHAR"})
        await _ensure_columns("test_runs", {"account_id": "VARCHAR"})
        await _ensure_columns("test_runs", {"webhook_url": "VARCHAR DEFAULT ''",
            "test_account_id": "VARCHAR DEFAULT ''", "test_account_name": "VARCHAR DEFAULT ''"})
        await _ensure_columns("test_results", {"account_id": "VARCHAR"})
        await _ensure_columns("test_step_logs", {"account_id": "VARCHAR"})

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
