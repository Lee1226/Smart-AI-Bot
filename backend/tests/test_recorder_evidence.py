"""Tests for durable recorder event evidence and backward compatibility."""
import json

from routers.recorder import RecordedStep, SaveRequest, _to_replay_steps
from core.recorder_engine import sanitize_events_for_storage
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from db.database import Base
from db.models import StepNode, TestCase, TestSuite
from db.migrate_step_tree import migrate_suite_to_step_tree


def test_save_request_keeps_legacy_clients_compatible():
    request = SaveRequest(
        device_id="device-1",
        suite_name="recorded",
        expected="screen updated",
        steps=[RecordedStep(action="tap", args={"x": 12, "y": 34}, description="tap")],
    )
    assert request.events is None


def test_recorder_event_storage_strips_screenshot_bytes_but_keeps_metadata():
    events = [{
        "event_id": "event-1",
        "target": {"text": "登录", "resource_id": "app:id/login"},
        "screenshot_b64": "large-base64-payload",
        "screenshot_captured_at_ms": 1234,
        "before": {"elements": [{"text": "登录", "clickable": True}]},
    }]
    stored = sanitize_events_for_storage(events)
    assert "screenshot_b64" not in stored[0]
    assert stored[0]["screenshot_evidence"] == {
        "captured": True, "captured_at_ms": 1234, "storage": "workbench_only"
    }
    assert stored[0]["target"]["resource_id"] == "app:id/login"
    assert stored[0]["before"]["elements"][0]["clickable"] is True


def test_replay_step_keeps_semantic_locator_and_coordinate_metadata():
    steps = [RecordedStep(
        action="tap",
        args={"x": 100, "y": 200},
        description="tap login",
    )]
    events = [{
        "event_id": "event-42",
        "target": {
            "text": "登录", "resource_id": "app:id/login",
            "class_name": "android.widget.Button", "clickable": True,
        },
        "coordinates": {"space": "device_px", "x": 100, "y": 200},
        "before": {"screen": {"width": 1080, "height": 2400}},
    }]
    replay = json.loads(_to_replay_steps(steps, events))
    assert replay[0]["source_event_id"] == "event-42"
    assert replay[0]["locator"]["resource_id"] == "app:id/login"
    assert replay[0]["locator"]["text"] == "登录"
    assert replay[0]["coordinate_space"] == "device_px"
    assert replay[0]["screen_size"] == {"width": 1080, "height": 2400}


def test_replay_does_not_fabricate_screen_dimensions():
    steps = [RecordedStep(action="tap", args={"x": 1, "y": 2}, description="tap")]
    replay = json.loads(_to_replay_steps(steps, [{"coordinates": {"space": "device_px"}}]))
    assert "screen_size" not in replay[0]



def test_replay_uses_nearby_observed_target_and_keeps_gap_as_timing_metadata():
    steps = [RecordedStep(
        action="tap", args={"x": 30, "y": 40, "delay_ms": 850}, description="tap",
    )]
    events = [{
        "event_id": "portal-event-7",
        "target": {"text": "", "resource_id": "", "class_name": ""},
        "observed_target": {
            "text": "继续", "resourceId": "app:id/continue",
            "className": "android.widget.Button", "clickable": True,
        },
        "ui_observation": {"screen": {"width": 720, "height": 1600}},
    }]
    replay = json.loads(_to_replay_steps(steps, events))
    assert replay[0]["locator"]["resource_id"] == "app:id/continue"
    assert replay[0]["source_event_id"] == "portal-event-7"
    assert replay[0]["screen_size"] == {"width": 720, "height": 1600}
    assert replay[0]["inter_action_gap_ms"] == 850



@pytest.mark.asyncio
async def test_test_case_persists_recorder_event_json():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        suite = TestSuite(name="recorded", source_format="recorded", account_id="acct")
        session.add(suite)
        await session.flush()
        case = TestCase(
            suite_id=suite.id,
            path="点击登录",
            expected="进入首页",
            account_id="acct",
            replay_steps='[{"kind":"tap","x":1,"y":2}]',
            recorder_events=json.dumps([{"event_id": "e1", "target": {"text": "登录"}}], ensure_ascii=False),
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        assert json.loads(case.recorder_events)[0]["target"]["text"] == "登录"
    await engine.dispose()



def test_replay_locator_prefers_clickable_ancestor_target():
    steps = [RecordedStep(action="tap", args={"x": 8, "y": 9}, description="tap icon")]
    events = [{
        "target": {
            "text": "icon", "resource_id": "app:id/icon",
            "tap_target": {
                "text": "打开设置", "resourceId": "app:id/settings_row",
                "className": "android.view.ViewGroup",
            },
        },
    }]
    replay = json.loads(_to_replay_steps(steps, events))
    assert replay[0]["locator"]["text"] == "打开设置"
    assert replay[0]["locator"]["resource_id"] == "app:id/settings_row"


@pytest.mark.asyncio
async def test_recorded_case_title_is_not_migrated_into_step_tree():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        suite = TestSuite(name="recorded", source_format="recorded", account_id="acct")
        session.add(suite)
        await session.flush()
        case = TestCase(
            suite_id=suite.id, path="录制用例标题", expected="进入首页",
            account_id="acct", replay_steps='[{"kind":"tap","x":1,"y":2}]',
        )
        session.add(case)
        await session.commit()
        assert await migrate_suite_to_step_tree(session, suite.id) == 0
        nodes = (await session.execute(select(StepNode).where(StepNode.suite_id == suite.id))).scalars().all()
        assert nodes == []
    await engine.dispose()
