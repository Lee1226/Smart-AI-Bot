"""Harmony bridge contracts; simulator proof is separate."""
import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image
from starlette.websockets import WebSocketDisconnect
sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
from hdc_bridge import HdcBridge, main_ability, parse_layout
from agent.ws_device import WebSocketDevice
from agent.prompt import system_prompt_for_platform
from agent.tools import TOOLS, tools_for_platform
from core.kb_platform import allows_document
from tests.test_browser_usb import BrowserUsbTest


class FocusPathTests(unittest.TestCase):
    def test_focused_ancestors_do_not_overwrite_editable_descendant(self):
        import json
        def node(kind,children=(),focused='true'):
            return {'attributes': {'type':kind,'focused':focused,'enabled':'true','bounds':'[0,0][100,200]'},'children':list(children)}
        layout=node('root',[node('Stack',[node('Navigation',[node('TextInput')])])])
        state=parse_layout(json.dumps(layout))
        self.assertEqual('TextInput',state['phone_state']['focusedElement']['className'])
        self.assertTrue(state['phone_state']['isEditable'])
        # A focused container alone is not permission to enter credentials.
        state=parse_layout(json.dumps(node('root',[node('TextInput',focused='false')])))
        self.assertFalse(state['phone_state']['isEditable'])


class HdcCommandTest(unittest.IsolatedAsyncioTestCase):
    def bridge(self):
        b = HdcBridge('hdc', 'http://localhost:8000', '127.0.0.1:5555', 'token')
        b.shell = AsyncMock(return_value='start ability successfully.')
        return b

    async def test_hdc_zero_host_exit_does_not_hide_remote_error(self):
        b = HdcBridge('hdc', 'http://localhost:8000', 'target', 'token')
        b.command = AsyncMock(return_value=b'Illegal argument: text\nSAB_EXIT_fixed:0\n')
        from types import SimpleNamespace
        with patch('hdc_bridge.uuid.uuid4', return_value=SimpleNamespace(hex='fixed')):
            with self.assertRaises(RuntimeError):
                await b.shell('uitest invalid')
            b.command.return_value = b'command failed\nSAB_EXIT_fixed:1\n'
            with self.assertRaises(RuntimeError):
                await b.shell('false')
            b.command.return_value = b'pong\nSAB_EXIT_fixed:0\n'
            self.assertEqual('pong', await b.shell('echo pong'))

    async def test_large_scroll_stays_inside_screen(self):
        device = WebSocketDevice('test')
        device.screen_width, device.screen_height = 1320, 2856
        device._rpc = AsyncMock()
        for direction in ('down', 'up'):
            await device.scroll(direction, 'large')
            params = device._rpc.call_args.args[1]
            self.assertGreater(params['startY'], 0)
            self.assertGreater(params['endY'], 0)
            self.assertLess(params['startY'], 2856)
            self.assertLess(params['endY'], 2856)

    async def test_key_translation_and_swipe_speed(self):
        b = self.bridge()
        await b.dispatch('keyboard/key', {'key_code': 4})
        b.shell.assert_awaited_with('uitest uiInput keyEvent 2')
        await b.dispatch('swipe', dict(startX=10, startY=200, endX=10, endY=700, duration=500))
        b.shell.assert_awaited_with('uitest uiInput swipe 10 200 10 700 1000')
        with self.assertRaises(ValueError):
            await b.dispatch('keyboard/key', {'key_code': 187})

    async def test_injection_and_unsupported_commands(self):
        b = self.bridge()
        for method, params in [('tap', {'x': '1;reboot', 'y': 10}), ('tap', {'x': float('nan'), 'y': 10}),
                               ('app', {'package': 'com.foo;reboot'}), ('app', {'package': 'com.foo', 'activity': 'a;reboot'}),
                               ('install', {}), ('video/start', {}), ('keyboard/input', {'base64_text': base64.b64encode('中文'.encode()).decode()})]:
            with self.assertRaises(ValueError):
                await b.dispatch(method, params)
        b.shell.assert_not_awaited()

    async def test_launch_resolves_ability_and_checks_success(self):
        b = self.bridge()
        b.shell = AsyncMock(side_effect=['{"hapModuleInfos":[{"mainElement":"MainAbility"}]}', 'start ability successfully.'])
        self.assertEqual('Launched com.example.app', await b.dispatch('app', {'package': 'com.example.app'}))
        b.shell.assert_awaited_with('aa start -b com.example.app -a MainAbility')
        b.shell = AsyncMock(return_value='unable to start ability')
        with self.assertRaises(RuntimeError):
            await b.dispatch('app', {'package': 'com.example.app', 'activity': 'MainAbility'})
        with self.assertRaises(ValueError):
            main_ability({'hapModuleInfos': [{}]})

    async def test_screenshot_device_size_and_quoted_input(self):
        b = self.bridge()
        buf = io.BytesIO(); Image.new('RGB', (1320, 2856)).save(buf, 'PNG')
        b.capture_file = AsyncMock(return_value=buf.getvalue())
        with Image.open(io.BytesIO(base64.b64decode(await b.dispatch('screenshot', {})))) as image:
            self.assertEqual((1320, 2856), image.size)
        await b.dispatch('keyboard/input', {'base64_text': base64.b64encode(b"hello'; reboot").decode()})
        self.assertIn("'\"'\"'", b.shell.call_args.args[0])

    def test_empty_root_attributes_and_bounds(self):
        raw = '{"attributes":{"bounds":"[0,0][1320,2856]","enabled":""},"children":[{"attributes":{"bounds":"[10,20][100,200]","type":"Button","text":"设置","bundleName":"com.huawei.hmos.settings","enabled":"true","clickable":"true"}}]}'
        state = parse_layout(raw)
        self.assertTrue(state['a11y_tree']['children'][0]['isEnabled'])
        self.assertEqual('com.huawei.hmos.settings', state['phone_state']['packageName'])
        self.assertEqual({'width': 1320, 'height': 2856}, state['device_context']['screen_bounds'])

    def test_platform_tools_and_kb(self):
        tools = {t['function']['name']: t['function'] for t in tools_for_platform('harmony')}
        self.assertNotIn('install_apk', tools)
        self.assertEqual(['back', 'home'], tools['global_action']['parameters']['properties']['action']['enum'])
        self.assertIn('install_apk', [t['function']['name'] for t in TOOLS])
        self.assertIn('HarmonyOS', system_prompt_for_platform('harmony'))
        self.assertNotIn('real Android device', system_prompt_for_platform('harmony'))
        self.assertFalse(allows_document('harmony', '/platforms/android/settings.md'))
        self.assertTrue(allows_document('harmony', '/platforms/harmony/settings.md'))


class HdcTransportTest(unittest.TestCase):
    setUpClass = classmethod(BrowserUsbTest.setUpClass.__func__)
    tearDownClass = classmethod(BrowserUsbTest.tearDownClass.__func__)
    device = BrowserUsbTest.device

    def test_distinct_credentials_platform_and_rpc(self):
        android = self.device('adb_bridge')
        with self.client.websocket_connect('/v1/providers/hdc-bridge', headers={'Authorization': 'Bearer ' + android['token']}) as ws:
            with self.assertRaises(WebSocketDisconnect) as error:
                ws.receive_json()
            self.assertEqual(4001, error.exception.code)
        d = self.device('hdc_bridge')
        self.assertEqual('harmony', d['platform'])
        with self.client.websocket_connect('/v1/providers/hdc-bridge', headers={'Authorization': 'Bearer ' + d['token']}) as ws:
            self.assertEqual(d['id'], ws.receive_json()['device_id'])
            device = WebSocketDevice(d['id'])
            self.assertEqual('harmony', device.platform)
            with patch('routers.live._adb_serials') as adb:
                caps = self.client.get('/api/devices/' + d['id'] + '/capabilities', headers=self.headers).json()
                self.assertEqual([], caps['adb_serials'])
                self.assertFalse(caps['video_available'])
                adb.assert_not_called()
            from core.run_recorder import RunRecorder
            with patch('core.run_recorder._single_adb_serial') as serials:
                async def record():
                    return await RunRecorder('hdc-test').start(d['id'])
                self.assertFalse(self.client.portal.call(record))
                serials.assert_not_called()
            with patch('routers.live._adb_jpeg', new_callable=AsyncMock) as host_frame, patch('routers.live._screenshot_jpeg', new_callable=AsyncMock, return_value=b'jpeg') as hdc_frame:
                frame = self.client.get('/api/devices/' + d['id'] + '/screenshot.jpg?source=adb&serial=wrong-phone', headers=self.headers)
                self.assertEqual(200, frame.status_code)
                host_frame.assert_not_called()
                hdc_frame.assert_awaited_once_with(d['id'])
            async def action():
                await device.press_key('back')
            future = self.client.portal.start_task_soon(action)
            msg = ws.receive_json()
            self.assertEqual('keyboard/key', msg['method'])
            self.assertEqual(4, msg['params']['key_code'])
            ws.send_json({'id': msg['id'], 'result': 'OK'})
            future.result(2)
