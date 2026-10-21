"""Pairing and outgoing helper contracts; real USB verification is separate."""
import asyncio
import concurrent.futures
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
from starlette.websockets import WebSocketDisconnect
from tests.test_browser_usb import BrowserUsbTest
from routers import ios_hosts
sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
from ios_host import validate_server, save_config, run_helper, discovered_devices

SERIAL = '00008120-0000000000000001'
INVENTORY = dict(type='inventory', devices=[dict(serial=SERIAL, name='Fixture iPad', ready=True)], prepared=True, forwarding=True)

class MacHostTests(unittest.TestCase):
    setUpClass = classmethod(BrowserUsbTest.setUpClass.__func__)
    tearDownClass = classmethod(BrowserUsbTest.tearDownClass.__func__)
    def tearDown(self):
        for row in self.client.get('/api/ios-host', headers=self.headers).json():
            self.client.delete('/api/ios-host/' + row['id'], headers=self.headers)
        ios_hosts._pairings.clear()
    def pair(self):
        code = self.client.post('/api/ios-host/pairings', headers=self.headers).json()['code']
        response = self.client.post('/api/ios-host/pair', json={'code': code, 'name': 'Fixture Mac'})
        self.assertEqual(200, response.status_code, response.text)
        return code, response.json()
    def test_grants_expire_are_single_use_and_tenant_scoped(self):
        self.assertEqual(401, self.client.post('/api/ios-host/pairings').status_code)
        self.assertEqual(401, self.client.get('/api/ios-host').status_code)
        code, host = self.pair()
        self.assertEqual(401, self.client.post('/api/ios-host/pair', json={'code': code, 'name': 'Replay'}).status_code)
        rows = self.client.get('/api/ios-host', headers=self.headers).json()
        self.assertFalse(rows[0]['online']); self.assertNotIn(host['token'], json.dumps(rows))
        grant = self.client.post('/api/ios-host/pairings', headers=self.headers).json()['code']
        ios_hosts._pairings[ios_hosts.digest(grant)] = (self.account_id, 0)
        self.assertEqual(401, self.client.post('/api/ios-host/pair', json={'code': grant, 'name': 'Expired'}).status_code)
        with patch.dict(os.environ, {'SAB_ALLOW_PUBLIC_REGISTRATION': 'true'}):
            other = self.client.post('/api/auth/register', json=dict(account_name='other', name='Other', email=str(uuid.uuid4())+'@example.test', password='fixture-password')).json()
        headers = {'Authorization': 'Bearer '+other['access_token']}
        try:
            self.assertEqual([], self.client.get('/api/ios-host', headers=headers).json())
            self.assertEqual(404, self.client.post('/api/ios-host/'+host['host_id']+'/connect', headers=headers, json={'serial': SERIAL}).status_code)
            self.assertEqual(404, self.client.delete('/api/ios-host/'+host['host_id'], headers=headers).status_code)
        finally:
            import sqlite3
            from db.database import DB_PATH
            with sqlite3.connect(DB_PATH) as db:
                db.execute('DELETE FROM users WHERE account_id=?', (other['user']['account_id'],))
                db.execute('DELETE FROM accounts WHERE id=?', (other['user']['account_id'],))
    def test_inventory_connect_and_revocation(self):
        _, host = self.pair()
        with self.client.websocket_connect('/v1/providers/ios-host', headers={'Authorization': 'Bearer '+host['token']}) as ws:
            self.assertEqual('ready', ws.receive_json()['type']); ws.send_json(INVENTORY)
            async def wait_inventory():
                for _ in range(100):
                    if ios_hosts._hosts[host['host_id']].inventory['devices']: return
                    await asyncio.sleep(.01)
                raise AssertionError('Missing inventory')
            self.client.portal.call(wait_inventory)
            self.assertEqual(SERIAL, self.client.get('/api/ios-host', headers=self.headers).json()[0]['devices'][0]['serial'])
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.client.post, '/api/ios-host/'+host['host_id']+'/connect', headers=self.headers, json={'serial': SERIAL})
                command = ws.receive_json(); self.assertEqual('connect', command['type'])
                ws.send_json({'type': 'result', 'id': command['id'], 'ok': True})
                response = future.result(timeout=5)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(409, self.client.post('/api/ios-host/'+host['host_id']+'/connect', headers=self.headers, json={'serial': 'different-device'}).status_code)
            with self.client.websocket_connect('/v1/providers/ios-bridge', headers={'Authorization': 'Bearer '+command['token']}) as device:
                self.assertEqual(response.json()['device_id'], device.receive_json()['device_id'])
                self.assertEqual(200, self.client.delete('/api/ios-host/'+host['host_id'], headers=self.headers).status_code)
                with self.assertRaises(WebSocketDisconnect): ws.receive_json()
                with self.assertRaises(WebSocketDisconnect): device.receive_json()
            with self.client.websocket_connect('/v1/providers/ios-bridge', headers={'Authorization': 'Bearer '+command['token']}) as device:
                with self.assertRaises(WebSocketDisconnect): device.receive_json()
        with self.client.websocket_connect('/v1/providers/ios-host', headers={'Authorization': 'Bearer '+host['token']}) as ws:
            with self.assertRaises(WebSocketDisconnect): ws.receive_json()
    def test_bundle(self):
        response = self.client.get('/api/ios-host/bundle', headers=self.headers)
        self.assertEqual(200, response.status_code)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual({'ios_host.py','ios_bridge.py','ios_usbmux.py','device_host.py','adb_bridge.py','hdc_bridge.py','adb-bridge-requirements.txt','ios-host-README.md'}, set(archive.namelist()))

    def test_multiple_platforms_use_distinct_devices_without_wda(self):
        _, host = self.pair()
        with self.client.websocket_connect('/v1/providers/ios-host', headers={'Authorization': 'Bearer '+host['token']}) as ws:
            ws.receive_json()
            ws.send_json(dict(type='inventory', prepared=False, forwarding=False, devices=[
                dict(serial='same-device', platform=p, name=p, ready=True) for p in ('android', 'harmony')]))
            async def wait_inventory():
                for _ in range(100):
                    if len(ios_hosts._hosts[host['host_id']].inventory['devices']) == 2: return
                    await asyncio.sleep(.01)
                raise AssertionError('Missing devices')
            self.client.portal.call(wait_inventory)
            ids = []
            with concurrent.futures.ThreadPoolExecutor() as pool:
                for platform, connection in [('android', 'adb_bridge'), ('harmony', 'hdc_bridge')]:
                    response = pool.submit(self.client.post, '/api/ios-host/'+host['host_id']+'/connect', headers=self.headers,
                                           json=dict(serial='same-device', platform=platform))
                    command = ws.receive_json()
                    self.assertEqual(platform, command['platform'])
                    ws.send_json(dict(type='result', id=command['id'], ok=True))
                    result = response.result(5)
                    self.assertEqual(200, result.status_code, result.text)
                    ids.append(result.json()['device_id'])
                    with self.client.websocket_connect('/v1/providers/'+connection.replace('_', '-'), headers={'Authorization': 'Bearer '+command['token']}) as device:
                        self.assertEqual(ids[-1], device.receive_json()['device_id'])
            self.assertEqual(2, len(set(ids)))

    def test_desktop_artifacts_are_explicit_and_authenticated(self):
        self.assertEqual(401, self.client.get('/api/ios-host/desktop').status_code)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'SAB_MAC_HELPER_DIR': folder}):
            self.assertEqual([], self.client.get('/api/ios-host/desktop', headers=self.headers).json()['downloads'])
            self.assertEqual(404, self.client.get('/api/ios-host/desktop/arm64', headers=self.headers).status_code)
            Path(folder, 'SAB-Device-Helper-macOS-arm64.zip').write_bytes(b'fixture archive')
            self.assertEqual('arm64', self.client.get('/api/ios-host/desktop', headers=self.headers).json()['downloads'][0]['architecture'])
            self.assertEqual(b'fixture archive', self.client.get('/api/ios-host/desktop/arm64', headers=self.headers).content)
            self.assertEqual(422, self.client.get('/api/ios-host/desktop/other', headers=self.headers).status_code)

class HelperConfigTests(unittest.TestCase):
    def test_usb_discovery_does_not_require_developer_tunnel(self):
        item = dict(hardwareProperties=dict(platform='iOS', udid=SERIAL, marketingName='iPad'),
                    connectionProperties=dict(transportType='wired', tunnelState='disconnected', pairingState='paired'),
                    deviceProperties=dict(developerModeStatus='enabled'))
        devices = discovered_devices([item], {SERIAL.replace('-', '')})
        self.assertEqual(1, len(devices))
        self.assertTrue(devices[0]['ready'])
        self.assertIn('开发者连接', devices[0]['status'])
        self.assertEqual([], discovered_devices([item], set()))
        item['connectionProperties']['pairingState'] = 'unpaired'
        self.assertFalse(discovered_devices([item], {SERIAL.replace('-', '')})[0]['ready'])

    def test_restart_reuses_saved_tool_paths(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            save_config(path, dict(server='http://localhost:8000', host_id='fixture', token='fixture',
                wda_derived_data='/tmp/signed-wda', pymobiledevice3='/tmp/usb-proxy'))
            args = Namespace(config=path, pair_code=None, server=None, wda_derived_data=None, pymobiledevice3=None)
            with patch('ios_host.MacHost') as host:
                host.return_value.run = AsyncMock()
                asyncio.run(run_helper(args, None))
                self.assertEqual(Path('/tmp/signed-wda'), host.call_args.args[2])
                self.assertEqual('/tmp/usb-proxy', host.call_args.args[3])
                host.return_value.run.assert_awaited_once()

    def test_transport_and_config_permissions(self):
        self.assertEqual('https://example.test', validate_server('https://example.test/'))
        self.assertEqual('http://127.0.0.1:18000', validate_server('http://127.0.0.1:18000'))
        for url in ['http://example.test','https://user:pass@example.test','https://example.test?token=secret','file:///tmp/helper']:
            with self.assertRaises(ValueError): validate_server(url)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'; save_config(path, {'token':'fixture'})
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
