"""Multi-platform desktop host, retaining the existing iOS pairing protocol."""
import asyncio
import hashlib
import os
import shutil
import sys
from pathlib import Path

from ios_host import MacHost, save_config


def tool_path(config, kind):
    defaults = {'adb': [str(Path.home() / 'Library/Android/sdk/platform-tools/adb')],
                'hdc': ['/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc']}
    for value in [config.get(kind, ''), shutil.which(kind) or '', *defaults[kind]]:
        if value and Path(value).is_file() and os.access(value, os.X_OK):
            return value
    return ''


async def command_output(executable, *args):
    process = await asyncio.create_subprocess_exec(executable, *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(process.communicate(), 8)
        if process.returncode:
            raise RuntimeError('设备工具执行失败，请检查工具版本及 USB 授权')
        return out.decode(errors='replace')
    finally:
        if process.returncode is None:
            process.kill(); await process.wait()


def parse_adb_devices(text):
    devices = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in {'List', '*'}:
            continue
        serial, state = parts[:2]
        name = next((p[6:].replace('_', ' ') for p in parts[2:] if p.startswith('model:')), serial)
        devices.append(dict(serial=serial, name=name, platform='android', ready=state == 'device',
                            status='' if state == 'device' else '请在设备允许 USB 调试' if state == 'unauthorized' else '设备离线，请重新连接 USB'))
    return devices


def parse_hdc_devices(text):
    # hdc list targets -v: serial, transport, connection status, optional name.
    devices = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith('['):
            continue
        ready = 'Connected' in parts[1:]
        devices.append(dict(serial=parts[0], name='鸿蒙 · ' + parts[0], platform='harmony', ready=ready,
                            status='' if ready else '请在设备确认 HDC 调试授权并检查连接'))
    return devices


class DeviceHost(MacHost):
    builtin_usbmux = True

    async def inventory(self):
        ios_task = asyncio.create_task(super().inventory())
        async def scan(kind, parser, args):
            executable = tool_path(self.config, kind)
            if not executable:
                return [], ''
            try:
                return parser(await command_output(executable, *args)), ''
            except (OSError, RuntimeError, asyncio.TimeoutError):
                return [], kind.upper() + ' 设备发现失败，请检查工具和 USB 授权'
        adb, hdc, ios = await asyncio.gather(scan('adb', parse_adb_devices, ['devices', '-l']),
                                           scan('hdc', parse_hdc_devices, ['list', 'targets', '-v']), ios_task)
        for d in ios['devices']:
            d['platform'] = 'ios'
        devices = ios['devices'] + adb[0] + hdc[0]
        for d in adb[0] + hdc[0]:
            job = self.jobs.get(d['platform'] + ':' + d['serial'])
            if job:
                d['status'] = '连接中，在线状态请查看网页' if job['bridge'].returncode is None else '桥接已退出，请在网页重试'
        ios.update(devices=devices, error='；'.join(e for e in (adb[1], hdc[1]) if e),
                   tools={'adb': tool_path(self.config, 'adb'), 'hdc': tool_path(self.config, 'hdc')},
                   ios_error=ios.get('error', ''))
        return ios

    async def start(self, serial, token, platform='ios'):
        if platform == 'ios':
            return await super().start(serial, token)
        if platform not in {'android', 'harmony'}:
            raise RuntimeError('不支持的设备平台')
        async with self.job_lock:
            inventory = await self.inventory()
            if not any(d['platform'] == platform and d['serial'] == serial and d['ready'] for d in inventory['devices']):
                raise RuntimeError('设备未就绪，请连接并授权调试')
            key = platform + ':' + serial
            old = self.jobs.get(key)
            if old and old['token'] == token and old['bridge'].returncode is None:
                return
            if old:
                await self.stop_job(old)
            folder = self.path.parent / (self.config['host_id'] + '-devices')
            folder.mkdir(exist_ok=True, mode=0o700)
            config_path = folder / (hashlib.sha256(key.encode()).hexdigest() + '.json')
            save_config(config_path, dict(server=self.config['server'], devices=[dict(serial=serial, token=token)]))
            kind = 'adb' if platform == 'android' else 'hdc'
            args = ([sys.executable, '--' + kind + '-bridge'] if getattr(sys, 'frozen', False)
                    else [sys.executable, str(Path(__file__).with_name(kind + '_bridge.py'))])
            bridge = await asyncio.create_subprocess_exec(*args, '--config', str(config_path), '--' + kind, tool_path(self.config, kind),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self.jobs[key] = dict(bridge=bridge, token=token)
            self.config.setdefault('devices', {})[key] = token
            save_config(self.path, self.config)

    async def stop_job(self, job):
        if 'wda' in job:
            return await super().stop_job(job)
        process = job['bridge']
        if process.returncode is None:
            import signal
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), 8)
            except asyncio.TimeoutError:
                process.kill(); await process.wait()

    async def restore(self):
        inventory = await self.inventory()
        available = {(d['platform'], d['serial']) for d in inventory['devices'] if d['ready']}
        for key, token in list(self.config.get('devices', {}).items()):
            platform, serial = key.split(':', 1) if key.startswith(('android:', 'harmony:')) else ('ios', key)
            if (platform, serial) in available:
                try:
                    await self.start(serial, token, platform)
                except (RuntimeError, OSError, asyncio.TimeoutError):
                    pass
