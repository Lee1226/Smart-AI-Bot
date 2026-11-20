import pytest

from core import lifecycle_actions


class FakeDevice:
    async def stop_app(self, package):
        return f"stopped {package}"


@pytest.mark.asyncio
async def test_lifecycle_action_waits_after_success(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(lifecycle_actions.asyncio, "sleep", fake_sleep)
    logs = []

    async def emit(message):
        logs.append(message)

    await lifecycle_actions.run_lifecycle_actions(
        FakeDevice(),
        [{
            "action": "stop_app",
            "args": {"package_name": "com.demo"},
            "enabled": True,
            "wait_after_ms": 1250,
        }],
        "执行前处理",
        emit,
    )

    assert sleeps == [1.25]
    assert any("操作后等待 1250 ms 完成" in message for message in logs)


@pytest.mark.asyncio
async def test_lifecycle_action_without_wait_keeps_legacy_behavior(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(lifecycle_actions.asyncio, "sleep", fake_sleep)

    async def emit(_message):
        pass

    await lifecycle_actions.run_lifecycle_actions(
        FakeDevice(),
        [{"action": "stop_app", "args": {"package_name": "com.demo"}}],
        "执行前处理",
        emit,
    )

    assert sleeps == []


@pytest.mark.asyncio
async def test_failed_lifecycle_action_does_not_apply_post_wait(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(lifecycle_actions.asyncio, "sleep", fake_sleep)

    class BrokenDevice:
        async def stop_app(self, _package):
            raise RuntimeError("device error")

    async def emit(_message):
        pass

    await lifecycle_actions.run_lifecycle_actions(
        BrokenDevice(),
        [{
            "action": "stop_app",
            "args": {"package_name": "com.demo"},
            "on_failure": "continue",
            "wait_after_ms": 1000,
        }],
        "执行前处理",
        emit,
    )

    assert sleeps == []
