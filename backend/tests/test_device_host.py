import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
from device_host import DeviceHost, parse_adb_devices, parse_hdc_devices, command_output


class DeviceHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery_does_not_inherit_app_control_input(self):
        process = AsyncMock(returncode=0)
        process.communicate.return_value = (b'device list', b'')
        with patch('device_host.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            self.assertEqual('device list', await command_output('/sdk/hdc', 'list', 'targets', '-v'))
            self.assertEqual(asyncio.subprocess.DEVNULL, spawn.call_args.kwargs['stdin'])

    def test_authorization_and_offline_states(self):
        rows = parse_adb_devices('List of devices attached\na device model:Pixel_9\nb unauthorized\nc offline\n')
        self.assertEqual([True, False, False], [r['ready'] for r in rows])
        self.assertEqual('Pixel 9', rows[0]['name'])
        rows = parse_hdc_devices('127.0.0.1:5555 TCP Connected localhost\nabc USB Unauthorized phone\n[Empty]\n')
        self.assertEqual([True, False], [r['ready'] for r in rows])

    async def test_platform_specific_processes_are_idempotent_and_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            host = DeviceHost(dict(host_id='fixture', server='http://localhost:8000'), Path(folder)/'host.json', Path(folder), None)
            inventory = dict(devices=[dict(serial='same', platform=p, ready=True) for p in ('android', 'harmony')])
            processes = [AsyncMock(returncode=None), AsyncMock(returncode=None)]
            for process in processes:
                process.send_signal = __import__('unittest.mock', fromlist=['Mock']).Mock()
            with patch.object(host, 'inventory', AsyncMock(return_value=inventory)), patch('device_host.tool_path', return_value='/sdk/tool'), patch('device_host.asyncio.create_subprocess_exec', AsyncMock(side_effect=processes)) as spawn:
                await host.start('same', 'android-token', 'android')
                await host.start('same', 'android-token', 'android')
                await host.start('same', 'harmony-token', 'harmony')
                self.assertEqual(2, spawn.await_count)
                self.assertIn('--adb', spawn.call_args_list[0].args)
                self.assertIn('--hdc', spawn.call_args_list[1].args)
                self.assertEqual({'android:same', 'harmony:same'}, set(host.config['devices']))
                await asyncio.gather(*(host.stop_job(job) for job in host.jobs.values()))
                for process in processes:
                    process.send_signal.assert_called_once()
            with patch.object(host, 'inventory', AsyncMock(return_value=inventory)), patch.object(host, 'start', AsyncMock()) as start:
                await host.restore()
                self.assertEqual({'android', 'harmony'}, {c.args[2] for c in start.call_args_list})
