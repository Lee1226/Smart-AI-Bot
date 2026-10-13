import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from core import regression
from core.step_tree import RunTarget, ChainItem

class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_reset_launch_in_order_and_remove_download_grant(self):
        target=SimpleNamespace(id='target')
        config={'suite_snapshot':{'suite':[{'node_id':'leaf','steps':[{'action':'open','expected':'visible'}]}]},'environment':'dev','artifact_id':'artifact','artifact_sha256':'a'*64,'download_base':'http://server','app_package':'com.test.app','clear_data':True}
        ctx=(target,SimpleNamespace(config_json=json.dumps(config)),SimpleNamespace(suite_id='suite',device_id='device'))
        session=AsyncMock();session.__aenter__.return_value=session
        device=AsyncMock();device.start_app.return_value='Launched com.test.app'
        calls=[]
        async def rpc(method,params,**kw):
            calls.append(method)
            if method=='app/install':
                grant=params['download_url'].split('/')[-1]
                self.assertEqual('artifact',regression.grants[grant][0])
            return {'verified':True}
        device._rpc.side_effect=rpc
        with patch.object(regression,'context',new=AsyncMock(return_value=ctx)),patch.object(regression,'AsyncSessionLocal',return_value=session),patch('core.test_runner.node_targets_for_suite',new=AsyncMock(return_value=[RunTarget('leaf',[ChainItem('open','visible')])])),patch.object(regression,'WebSocketDevice',return_value=device):
            await regression.prepare_run('run',AsyncMock())
        self.assertEqual(['app/install','app/reset'],calls)
        device.start_app.assert_awaited_once_with('com.test.app')
        self.assertFalse(regression.grants)

    async def test_changed_actions_abort_before_device_commands(self):
        ctx=(SimpleNamespace(id='target'),SimpleNamespace(config_json=json.dumps({'suite_snapshot':{'suite':[]}})),SimpleNamespace(suite_id='suite',device_id='device'))
        session=AsyncMock();session.__aenter__.return_value=session
        with patch.object(regression,'context',new=AsyncMock(return_value=ctx)),patch.object(regression,'AsyncSessionLocal',return_value=session),patch('core.test_runner.node_targets_for_suite',new=AsyncMock(return_value=[RunTarget('leaf',[ChainItem('changed')])])),patch.object(regression,'WebSocketDevice') as device:
            with self.assertRaisesRegex(RuntimeError,'发生变更'):await regression.prepare_run('run',AsyncMock())
            device.assert_not_called()
