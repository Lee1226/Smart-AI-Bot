"""Browser transport contract/security tests; not real USB acceptance."""
import unittest
import sqlite3
from db.database import DB_PATH
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app
from ws.portal_ws import connected_devices, send_rpc


class BrowserUsbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()
        cls.headers = {}
        cls.created_devices = set()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        with sqlite3.connect(DB_PATH) as db:
            db.executemany('DELETE FROM devices WHERE id = ?', [(device_id,) for device_id in cls.created_devices])
            db.commit()

    def device(self, kind='browser_usb'):
        result = self.client.post('/api/devices', headers=self.headers,
                                  json={'name': 'USB 测试', 'connection_type': kind})
        self.assertEqual(201, result.status_code)
        self.created_devices.add(result.json()["id"])
        return result.json()

    def test_auth_rejects_bad_credentials_and_remote_tokens(self):
        remote = self.device('portal')
        for hello in ({'type': 'auth', 'token': 'bad'},
                      {'type': 'auth', 'token': remote['token']}, [],
                      {'type': 'wrong', 'token': remote['token']}):
            with self.client.websocket_connect('/v1/providers/browser-usb') as ws:
                ws.send_json(hello)
                with self.assertRaises(WebSocketDisconnect) as error:
                    ws.receive_json()
                self.assertEqual(4001, error.exception.code)
        local = self.device()
        with self.client.websocket_connect('/v1/providers/join',
                headers={'Authorization': f"Bearer {local['token']}"}) as ws:
            with self.assertRaises(WebSocketDisconnect) as error:
                ws.receive_json()
            self.assertEqual(4001, error.exception.code)

    def test_shared_rpc_duplicate_session_and_disconnect(self):
        d = self.device()
        with self.client.websocket_connect('/v1/providers/browser-usb') as ws:
            ws.send_json({'type': 'auth', 'token': d['token']})
            self.assertEqual({'type': 'ready', 'device_id': d['id']}, ws.receive_json())
            conn = connected_devices[d['id']]
            future = self.client.portal.start_task_soon(send_rpc, conn, 'state', {})
            request = ws.receive_json()
            self.assertEqual('state', request['method'])
            ws.send_json({'id': request['id'], 'result': {'phone_state': {'packageName': 'com.android.settings'}}})
            self.assertEqual('com.android.settings', future.result(timeout=5)['phone_state']['packageName'])
            future = self.client.portal.start_task_soon(send_rpc, conn, 'install', {})
            request = ws.receive_json()
            ws.send_json({'id': request['id'], 'status': 'error', 'result': 'unsupported'})
            with self.assertRaisesRegex(RuntimeError, 'unsupported'):
                future.result(timeout=5)
            with self.client.websocket_connect('/v1/providers/browser-usb') as duplicate:
                duplicate.send_json({'type': 'auth', 'token': d['token']})
                with self.assertRaises(WebSocketDisconnect) as error:
                    duplicate.receive_json()
                self.assertEqual(4009, error.exception.code)
            self.assertIs(conn, connected_devices[d['id']])
            from core.run_recorder import RunRecorder
            with patch('core.run_recorder._single_adb_serial') as host_adb:
                async def start_recorder():
                    return await RunRecorder('test-run').start(d['id'])
                recorded = self.client.portal.call(start_recorder)
                self.assertFalse(recorded)
                host_adb.assert_not_called()
            with patch('routers.live._adb_serials', return_value=['wrong-server-phone']) as serials:
                caps = self.client.get(f"/api/devices/{d['id']}/capabilities", headers=self.headers).json()
                self.assertEqual([], caps['adb_serials'])
                serials.assert_not_called()
            with patch('routers.live._adb_jpeg', new_callable=AsyncMock) as host_frame, \
                    patch('routers.live._screenshot_jpeg', new_callable=AsyncMock, return_value=b'jpeg') as usb_frame:
                frame = self.client.get(f"/api/devices/{d['id']}/screenshot.jpg?source=adb&serial=wrong-server-phone",
                                        headers=self.headers)
                self.assertEqual(200, frame.status_code)
                host_frame.assert_not_called()
                usb_frame.assert_awaited_once_with(d['id'])
            pending = self.client.portal.start_task_soon(send_rpc, conn, 'screenshot', {})
            ws.receive_json()
            ws.close()
            with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                pending.result(timeout=5)
        self.assertNotIn(d['id'], connected_devices)
        rows = self.client.get('/api/devices', headers=self.headers).json()
        self.assertEqual('offline', next(r for r in rows if r['id'] == d['id'])['status'])

    def test_modes_are_validated_and_portal_default_is_preserved(self):
        result = self.client.post('/api/devices', headers=self.headers, json={'name': 'old client'})
        self.created_devices.add(result.json()['id'])
        self.assertEqual('portal', result.json()['connection_type'])
        result = self.client.post('/api/devices', headers=self.headers, json={'connection_type': 'shell'})
        self.assertEqual(422, result.status_code)
