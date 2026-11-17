"""Tests for importing legacy replay data into reviewable execution plans."""
import json
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.execution_plan import legacy_replay_to_plan
from db.database import Base
from db.models import ExecutionPlan, TestCase, TestSuite


def test_import_preserves_semantic_locator_and_coordinates():
    raw = json.dumps([{
        "kind": "tap", "x": 120, "y": 240, "delay_ms": 300,
        "resourceId": "com.example:id/search",
        "text": "搜索", "className": "android.widget.EditText",
        "coordinate_space": "device_px",
    }], ensure_ascii=False)

    steps, warnings = legacy_replay_to_plan(raw)

    assert warnings == []
    step = steps[0]
    assert step["executor"] == "deterministic"
    assert step["locator"]["resource_id"] == "com.example:id/search"
    assert step["locator"]["text"] == "搜索"
    assert step["fallback"]["coordinates"] == {"x": 120, "y": 240}
    assert step["fallback"]["coordinate_space"] == "device_px"
    assert step["wait_condition"] is None
    assert step["timing"]["inter_action_gap_ms"] == 300


def test_coordinate_only_tap_is_hybrid_and_warned():
    steps, warnings = legacy_replay_to_plan('[{"kind":"tap","x":10,"y":20}]')

    assert steps[0]["executor"] == "hybrid"
    assert steps[0]["fallback"]["coordinates"] == {"x": 10, "y": 20}
    assert steps[0]["import_status"] == "needs_locator"
    assert warnings


def test_unknown_action_is_not_silently_marked_ready():
    steps, warnings = legacy_replay_to_plan('[{"kind":"launch_magic"}]')

    assert steps[0]["import_status"] == "unsupported"
    assert steps[0]["action"] == "launch_magic"
    assert warnings


def test_invalid_or_empty_legacy_data_is_rejected():
    with pytest.raises(ValueError):
        legacy_replay_to_plan("")
    with pytest.raises(ValueError):
        legacy_replay_to_plan('{"kind":"tap"}')


@pytest.mark.asyncio
async def test_execution_plan_model_persists_versioned_draft():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        suite = TestSuite(name="recorded", source_format="recorded", account_id="acct")
        session.add(suite)
        await session.flush()
        case = TestCase(
            suite_id=suite.id, path="点击搜索", expected="进入结果页",
            replay_steps='[{"kind":"tap","x":1,"y":2}]', account_id="acct",
        )
        session.add(case)
        await session.flush()
        plan = ExecutionPlan(
            test_case_id=case.id, suite_id=suite.id, version=1,
            source="legacy_import", status="draft", account_id="acct",
            steps_json='[{"sequence":1}]',
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        assert plan.version == 1
        assert plan.status == "draft"
        assert json.loads(plan.steps_json) == [{"sequence": 1}]
    await engine.dispose()
