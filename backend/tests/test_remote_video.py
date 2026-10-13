"""Transport integration checks with simulated providers; no real-device proof."""
import asyncio
import unittest
from unittest.mock import patch
from starlette.websockets import WebSocketDisconnect
from tests.test_browser_usb import BrowserUsbTest
from ws.portal_ws import connected_devices
from ws.video import sessions


class RemoteVideoTest(unittest.TestCase):
    setUpClass = classmethod(BrowserUsbTest.setUpClass.__func__)
    tearDownClass = classmethod(BrowserUsbTest.tearDownClass.__func__)
    device = BrowserUsbTest.device

    async def wait_capability(self, device_id):
        for _ in range(100):
            if connected_devices[device_id].video_available:
                return
            await asyncio.sleep(.01)
        raise AssertionError('capability not received')

    def test_bridge_requires_bridge_credentials(self):
        portal = self.device('portal')
        with self.client.websocket_connect('/v1/providers/adb-bridge',
                headers={'Authorization': 'Bearer ' + portal['token']}) as ws:
            with self.assertRaises(WebSocketDisconnect) as error:
                ws.receive_json()
            self.assertEqual(4001, error.exception.code)
        bridge = self.device('adb_bridge')
        with self.client.websocket_connect('/v1/providers/adb-bridge',
                headers={'Authorization': 'Bearer ' + bridge['token']}) as ws:
            self.assertEqual(bridge['id'], ws.receive_json()['device_id'])
            with patch('routers.live._adb_serials') as server_adb:
                caps = self.client.get('/api/devices/' + bridge['id'] + '/capabilities', headers=self.headers)
                self.assertEqual([], caps.json()['adb_serials'])
                server_adb.assert_not_called()

    def test_remote_video_grants_relay_and_stop(self):
        device = self.device('portal')
        wrong = self.device('portal')
        with self.client.websocket_connect('/v1/providers/join',
                headers={'Authorization': 'Bearer ' + device['token']}) as control:
            control.send_json({'type': 'capabilities', 'video': True})
            self.client.portal.call(self.wait_capability, device['id'])
            caps = self.client.get('/api/devices/' + device['id'] + '/capabilities', headers=self.headers).json()
            self.assertTrue(caps['video_available'])
            with self.client.websocket_connect('/v1/devices/' + device['id'] + '/live?source=device', headers=self.headers) as viewer:
                request = control.receive_json()
                self.assertEqual('video/start', request['method'])
                stream_id = request['params']['stream_id']
                # Another device token cannot attach to this preview grant.
                with self.client.websocket_connect('/v1/providers/video', headers={
                        'Authorization': 'Bearer ' + wrong['token'], 'X-Stream-ID': stream_id}) as denied:
                    with self.assertRaises(WebSocketDisconnect) as error:
                        denied.receive_bytes()
                    self.assertEqual(4403, error.exception.code)
                with self.client.websocket_connect('/v1/providers/video', headers={
                        'Authorization': 'Bearer ' + device['token'], 'X-Stream-ID': stream_id}) as producer:
                    control.send_json({'id': request['id'], 'result': 'started'})
                    producer.send_bytes(b'\x00\x00\x00\x01\x67test')
                    self.assertEqual(b'\x00\x00\x00\x01\x67test', viewer.receive_bytes())
                    # Video viewer lifecycle must not close the control channel.
                    viewer.close()
                    stop = control.receive_json()
                    self.assertEqual('video/stop', stop['method'])
                    self.assertEqual(stream_id, stop['params']['stream_id'])
                    control.send_json({'id': stop['id'], 'result': 'stopped'})
            self.assertFalse(sessions)
            self.assertIn(device['id'], connected_devices)
            # A stopped stream grant cannot be replayed.
            with self.client.websocket_connect('/v1/providers/video', headers={
                    'Authorization': 'Bearer ' + device['token'], 'X-Stream-ID': stream_id}) as expired:
                with self.assertRaises(WebSocketDisconnect):
                    expired.receive_bytes()

    def test_explicit_video_never_selects_unrelated_server_phone(self):
        device = self.device('portal')
        with patch('routers.live._adb_serials', return_value=['unrelated-phone']) as adb:
            with self.assertRaises(WebSocketDisconnect) as error:
                with self.client.websocket_connect('/v1/devices/' + device['id'] + '/live?source=device', headers=self.headers) as viewer:
                    viewer.receive_bytes()
            self.assertEqual(4404, error.exception.code)
            adb.assert_not_called()

    def test_two_devices_have_independent_video_streams(self):
        a, b = self.device('adb_bridge'), self.device('adb_bridge')
        with self.client.websocket_connect('/v1/providers/adb-bridge', headers={'Authorization': 'Bearer ' + a['token']}) as ca, \
             self.client.websocket_connect('/v1/providers/adb-bridge', headers={'Authorization': 'Bearer ' + b['token']}) as cb:
            ca.receive_json(); cb.receive_json()
            ca.send_json({'type': 'capabilities', 'video': True})
            cb.send_json({'type': 'capabilities', 'video': True})
            self.client.portal.call(self.wait_capability, a['id'])
            self.client.portal.call(self.wait_capability, b['id'])
            with self.client.websocket_connect('/v1/devices/' + a['id'] + '/live?source=device', headers=self.headers) as va, \
                 self.client.websocket_connect('/v1/devices/' + b['id'] + '/live?source=device', headers=self.headers) as vb:
                ra, rb = ca.receive_json(), cb.receive_json()
                sa, sb = ra['params']['stream_id'], rb['params']['stream_id']
                self.assertNotEqual(sa, sb)
                with self.client.websocket_connect('/v1/providers/video', headers={'Authorization': 'Bearer ' + a['token'], 'X-Stream-ID': sa}) as pa, \
                     self.client.websocket_connect('/v1/providers/video', headers={'Authorization': 'Bearer ' + b['token'], 'X-Stream-ID': sb}) as pb:
                    ca.send_json({'id': ra['id'], 'result': 'started'})
                    cb.send_json({'id': rb['id'], 'result': 'started'})
                    pa.send_bytes(b'phone-a'); pb.send_bytes(b'phone-b')
                    self.assertEqual(b'phone-a', va.receive_bytes())
                    self.assertEqual(b'phone-b', vb.receive_bytes())
                    with self.client.websocket_connect('/v1/devices/' + a['id'] + '/live?source=device', headers=self.headers) as duplicate:
                        with self.assertRaises(WebSocketDisconnect) as error:
                            duplicate.receive_bytes()
                        self.assertEqual(4409, error.exception.code)
                    # Closing A must not end B, nor should a duplicate stop A.
                    pa.send_bytes(b'a-still-live')
                    self.assertEqual(b'a-still-live', va.receive_bytes())
                    va.close()
                    stop_a = ca.receive_json()
                    ca.send_json({'id': stop_a['id'], 'result': 'stopped'})
                    pb.send_bytes(b'b-still-live')
                    self.assertEqual(b'b-still-live', vb.receive_bytes())
                    vb.close()
                    stop_b = cb.receive_json()
                    cb.send_json({'id': stop_b['id'], 'result': 'stopped'})
