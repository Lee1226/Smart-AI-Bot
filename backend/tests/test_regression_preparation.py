import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from core import regression
from core.step_tree import RunTarget, ChainItem


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_validates_snapshot_and_launches_suite_app(self):
        target = SimpleNamespace(id='target')
        config = {
            'suite_snapshot': {'suite': [{'node_id': 'leaf', 'steps': [{'action': 'open', 'expected': 'visible'}]}]},
            'suite_apps': {'suite': {'android': 'com.test.app'}},
        }
        ctx = (target, SimpleNamespace(config_json=json.dumps(config)),
               SimpleNamespace(suite_id='suite', device_id='device'))
        session = AsyncMock()
        session.__aenter__.return_value = session
        device = AsyncMock()
        device.platform = 'android'
        device.start_app.return_value = 'Launched com.test.app'
        with patch.object(regression, 'context', new=AsyncMock(return_value=ctx)), \
             patch.object(regression, 'AsyncSessionLocal', return_value=session), \
             patch('core.test_runner.node_targets_for_suite', new=AsyncMock(return_value=[
                 RunTarget('leaf', [ChainItem('open', 'visible')])
             ])), \
             patch('core.test_runner._suite_app_package', new=AsyncMock(return_value='com.test.app')), \
             patch.object(regression, 'WebSocketDevice', return_value=device):
            await regression.prepare_run('run', AsyncMock())
        device.start_app.assert_awaited_once_with('com.test.app')

    async def test_changed_actions_abort_before_device_commands(self):
        ctx = (SimpleNamespace(id='target'),
               SimpleNamespace(config_json=json.dumps({'suite_snapshot': {'suite': []}, 'suite_apps': {}})),
               SimpleNamespace(suite_id='suite', device_id='device'))
        session = AsyncMock()
        session.__aenter__.return_value = session
        with patch.object(regression, 'context', new=AsyncMock(return_value=ctx)), \
             patch.object(regression, 'AsyncSessionLocal', return_value=session), \
             patch('core.test_runner.node_targets_for_suite', new=AsyncMock(return_value=[
                 RunTarget('leaf', [ChainItem('changed')])
             ])), \
             patch.object(regression, 'WebSocketDevice') as device:
            with self.assertRaisesRegex(RuntimeError, '发生变更'):
                await regression.prepare_run('run', AsyncMock())
            device.assert_not_called()
