"""Protocol checks; real USB/WDA acceptance is separate."""
import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image
from starlette.websockets import WebSocketDisconnect
sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
from ios_bridge import IosBridge, parse_source, custom_code_focus
from agent.ws_device import WebSocketDevice
from agent.tools import tools_for_platform
from agent.prompt import system_prompt_for_platform
from tests.test_browser_usb import BrowserUsbTest

class IosProtocolTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self):return IosBridge('http://localhost:8000','test-udid','fixture-token',8100)

    async def test_retina_coordinates_and_focus_fail_closed(self):
        b=self.bridge();out=io.BytesIO();Image.new('RGB',(1170,2532)).save(out,'PNG')
        b.command_value=AsyncMock(side_effect=[{'width':390,'height':844},base64.b64encode(out.getvalue()).decode()])
        image=Image.open(io.BytesIO(base64.b64decode(await b.dispatch('screenshot',{}))))
        self.assertEqual((390,844),image.size)
        xml='<AppiumAUT><XCUIElementTypeApplication x="0" y="0" width="390" height="844"><XCUIElementTypeSecureTextField name="password" value="secret-fixture" x="10" y="20" width="200" height="50"/></XCUIElementTypeApplication></AppiumAUT>'
        state=parse_source(xml,{'width':390,'height':844},{'bundleId':'com.test.app'},None)
        self.assertFalse(state['phone_state']['isEditable']);self.assertNotIn('secret-fixture',str(state))
        state=parse_source(xml,{'width':390,'height':844},{'bundleId':'com.test.app'},{'className':'XCUIElementTypeSecureTextField'})
        self.assertTrue(state['phone_state']['isEditable'])
        from agent.perception import format_ui_state
        formatted,elements,*_=format_ui_state(state)
        self.assertTrue(elements)
        self.assertNotIn('secret-fixture',formatted)

    async def test_unicode_clear_and_unsupported_back(self):
        b=self.bridge();b.command_value=AsyncMock()
        b.focused_element=AsyncMock(return_value={'elementId':'input','className':'XCUIElementTypeTextField'})
        await b.dispatch('keyboard/input',{'base64_text':base64.b64encode('中文'.encode()).decode(),'clear':True})
        self.assertEqual('/element/input/clear',b.command_value.await_args_list[0].args[1])
        self.assertEqual({'value':['中文']},b.command_value.await_args_list[1].args[2])
        b.focused_element.return_value=None
        with self.assertRaisesRegex(ValueError,'Focus'):await b.dispatch('keyboard/input',{'base64_text':base64.b64encode(b'text').decode()})
        for method,params in [('keyboard/key',{'key_code':4}),('packages',{}),('app/install',{}),('video/start',{}),('tap',{'x':float('nan'),'y':3}),('app',{'package':'com.foo;bad'})]:
            with self.assertRaises(ValueError):await b.dispatch(method,params)

    async def test_custom_code_grid_requires_keyboard_and_context(self):
        xml = '<AppiumAUT><XCUIElementTypeStaticText label="输入4位验证码" visible="true"/>' + '<XCUIElementTypeCell visible="true"/>' * 4 + '<XCUIElementTypeKeyboard visible="true"/></AppiumAUT>'
        self.assertTrue(custom_code_focus(xml)['customCodeInput'])
        self.assertIsNone(custom_code_focus(xml.replace('XCUIElementTypeKeyboard', 'XCUIElementTypeOther')))
        self.assertIsNone(custom_code_focus(xml.replace('输入4位验证码', '普通页面')))
        self.assertIsNone(custom_code_focus(xml.replace('</AppiumAUT>', '<XCUIElementTypeTextField visible="true" width="100" height="40"/></AppiumAUT>')))
        b = self.bridge()
        b.command_value = AsyncMock(side_effect=[{}, xml])
        self.assertTrue((await b.focused_element())['customCodeInput'])
        b.command_value = AsyncMock(side_effect=[{}, xml.replace('XCUIElementTypeKeyboard', 'XCUIElementTypeOther')])
        self.assertIsNone(await b.focused_element())
        b = self.bridge(); b.focused_element = AsyncMock(return_value=custom_code_focus(xml)); b.command_value = AsyncMock()
        await b.dispatch('keyboard/input', {'base64_text': base64.b64encode(b'1234').decode(), 'clear': True})
        self.assertEqual({'value': ['\b' * 8]}, b.command_value.await_args_list[0].args[2])
        self.assertEqual({'value': ['1234']}, b.command_value.await_args_list[1].args[2])
        with self.assertRaisesRegex(ValueError, 'only'):
            await b.dispatch('keyboard/input', {'base64_text': base64.b64encode(b'password').decode()})

    async def test_session_focus_and_tools(self):
        b=self.bridge();b.request=AsyncMock(return_value={'sessionId':'s','value':{}})
        self.assertEqual('/session/s',await b.session())
        b.command_value=AsyncMock(side_effect=[{'element-6066-11e4-a52e-4f735466cecf':'field'},'XCUIElementTypeTextField'])
        self.assertEqual('field',(await b.focused_element())['elementId'])
        b.command_value=AsyncMock(side_effect=RuntimeError('no focus'));self.assertIsNone(await b.focused_element())
        tools={t['function']['name']:t['function'] for t in tools_for_platform('ios')}
        self.assertNotIn('install_apk',tools);self.assertNotIn('list_packages',tools)
        self.assertNotIn('back',tools['press_key']['parameters']['properties']['key']['enum'])
        self.assertIn('iOS',system_prompt_for_platform('ios'));self.assertNotIn('real Android',system_prompt_for_platform('ios'))

    async def test_wda_errors_redacted_and_session_recreated(self):
        import json
        from urllib.error import HTTPError
        from unittest.mock import Mock
        b=self.bridge();b.session_id='expired'
        payload=json.dumps({'value':{'error':'invalid session id','message':'typed-secret-must-not-leak'}}).encode()
        opener=Mock();opener.open.side_effect=HTTPError(b.url+'/session/expired',404,'Not Found',{},io.BytesIO(payload))
        with patch('ios_bridge.urllib.request.build_opener',return_value=opener):
            with self.assertRaises(RuntimeError) as error:await b.request('GET','/session/expired')
        self.assertNotIn('typed-secret',str(error.exception));self.assertIsNone(b.session_id)
        b.request=AsyncMock(return_value={'value':None})
        await b.dispatch('keyboard/key',{'key_code':3})
        b.request.assert_awaited_once_with('POST','/wda/homescreen',{})

class IosTransportTests(unittest.TestCase):
    setUpClass=classmethod(BrowserUsbTest.setUpClass.__func__)
    tearDownClass=classmethod(BrowserUsbTest.tearDownClass.__func__)
    device=BrowserUsbTest.device
    def test_platform_credentials_and_no_android_fallback(self):
        android=self.device('adb_bridge')
        with self.client.websocket_connect('/v1/providers/ios-bridge',headers={'Authorization':'Bearer '+android['token']}) as ws:
            with self.assertRaises(WebSocketDisconnect) as error:ws.receive_json()
            self.assertEqual(4001,error.exception.code)
        d=self.device('ios_bridge');self.assertEqual('ios',d['platform'])
        with self.client.websocket_connect('/v1/providers/ios-bridge',headers={'Authorization':'Bearer '+d['token']}) as ws:
            self.assertEqual(d['id'],ws.receive_json()['device_id']);device=WebSocketDevice(d['id']);self.assertEqual('ios',device.platform)
            with patch('routers.live._adb_serials') as adb:
                caps=self.client.get('/api/devices/'+d['id']+'/capabilities',headers=self.headers).json()
                self.assertFalse(caps['video_available']);self.assertEqual([],caps['adb_serials']);adb.assert_not_called()
            self.assertEqual(400,self.client.post('/api/recorder/record/start?device_id='+d['id'],headers=self.headers).status_code)
            with patch('routers.live._adb_jpeg',new_callable=AsyncMock) as adb,patch('routers.live._screenshot_jpeg',new_callable=AsyncMock,return_value=b'jpeg') as screen:
                r=self.client.get('/api/devices/'+d['id']+'/screenshot.jpg?source=adb',headers=self.headers)
                self.assertEqual(200,r.status_code);adb.assert_not_called();screen.assert_awaited_once_with(d['id'])
            async def action():await device.press_key('home')
            task=self.client.portal.start_task_soon(action);msg=ws.receive_json();self.assertEqual(3,msg['params']['key_code'])
            ws.send_json({'id':msg['id'],'result':'OK'});task.result(2)
