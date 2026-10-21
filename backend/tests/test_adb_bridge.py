"""Bridge device routing and parser checks, not physical ADB acceptance."""
import asyncio
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from PIL import Image

spec = importlib.util.spec_from_file_location('sab_adb_bridge', Path(__file__).parents[2] / 'tools/adb_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_launch_returns_portal_success_contract(self):
        instance = bridge.Bridge('adb', 'http://localhost:8000', 'phone-a', 'token')
        instance.shell = AsyncMock(side_effect=['com.android.settings/.Settings', 'Status: ok'])
        self.assertEqual('Launched com.android.settings', await instance.dispatch('app', {'package': 'com.android.settings'}))
        instance.shell = AsyncMock(side_effect=['com.android.settings/.Settings', 'Error: Activity not found'])
        with self.assertRaises(RuntimeError):
            await instance.dispatch('app', {'package': 'com.android.settings'})

    async def test_multiple_launchers_excludes_leakcanary_and_never_launches_resolver(self):
        instance = bridge.Bridge('adb', 'http://localhost:8000', 'phone-a', 'token')
        instance.shell = AsyncMock(side_effect=[
            '2 activities found:\ncom.example.fixture/.default\ncom.example.fixture/leakcanary.internal.activity.LeakLauncherActivity',
            'Status: ok'])
        self.assertEqual('Launched com.example.fixture', await instance.dispatch('app', {'package': 'com.example.fixture'}))
        instance.shell.assert_awaited_with('am start -W -n com.example.fixture/.default')
        instance.shell = AsyncMock(return_value='com.android.intentresolver/.ResolverActivity')
        with self.assertRaises(ValueError):
            await instance.dispatch('app', {'package': 'com.example.fixture'})
        self.assertEqual(1, instance.shell.await_count)

    async def test_actions_route_to_assigned_serial_and_reject_injection(self):
        a = bridge.Bridge('adb', 'http://localhost:8000', 'phone-a', 'token-a')
        b = bridge.Bridge('adb', 'http://localhost:8000', 'phone-b', 'token-b')
        a.shell, b.shell = AsyncMock(), AsyncMock()
        await asyncio.gather(a.dispatch('tap', {'x': 10, 'y': 20}), b.dispatch('keyboard/key', {'key_code': 4}))
        a.shell.assert_awaited_once_with('input tap 10 20')
        b.shell.assert_awaited_once_with('input keyevent 4')
        for method, params in [('tap', {'x': '1; reboot', 'y': 0}), ('app', {'package': 'com.foo;reboot'}), ('video/start', {'stream_id': '../foo'})]:
            with self.assertRaises(ValueError):
                await a.dispatch(method, params)
        self.assertEqual(1, a.shell.await_count)

    async def test_screenshot_preserves_device_resolution(self):
        instance = bridge.Bridge('adb', 'http://localhost:8000', 'phone-a', 'token')
        out = io.BytesIO()
        Image.new('RGB', (1080, 2400)).save(out, 'PNG')
        instance.command = AsyncMock(return_value=out.getvalue())
        result = await instance.dispatch('screenshot', {})
        with Image.open(io.BytesIO(bridge.base64.b64decode(result))) as image:
            self.assertEqual((1080, 2400), image.size)
        instance.command.assert_awaited_once_with('exec-out', 'screencap', '-p')

    def test_ui_tree_rotation_and_phone_context(self):
        xml = '<hierarchy rotation="1"><node class="android.widget.EditText" text="hello" package="com.example.app" bounds="[0,0][100,200]" focused="true" enabled="true" /></hierarchy>'
        state = bridge.parse_ui(xml, 'Physical size: 1080x2400\nOverride size: 720x1600')
        self.assertEqual({'width': 1600, 'height': 720}, state['device_context']['screen_bounds'])
        self.assertTrue(state['phone_state']['isEditable'])
        self.assertEqual('com.example.app', state['phone_state']['packageName'])
        self.assertEqual('hello', state['a11y_tree']['children'][0]['text'])
