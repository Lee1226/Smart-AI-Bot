#!/usr/bin/env python3
"""Pair a Mac to SAB and receive USB connection requests over an outbound socket."""
import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from adb_bridge import provider_url

log = logging.getLogger('sab.ios_host')


def validate_server(server):
    url = urlsplit(server)
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or (url.scheme == 'http' and url.hostname not in {'127.0.0.1', 'localhost', '::1'})):
        raise ValueError('Use an HTTPS platform URL (HTTP is allowed only for local testing)')
    return server.rstrip('/')


def save_config(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.ios-host-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Pairing redirects are not allowed; use the final platform URL')


def pair(server, code, name):
    request = urllib.request.Request(server + '/api/ios-host/pair',
        json.dumps(dict(code=code, name=name)).encode(), {'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise RuntimeError('Pairing code expired; generate a new code on the website') from None
        raise RuntimeError('Pairing failed (HTTP %d); deploy the matching server version first' % error.code) from None


async def discover():
    with tempfile.TemporaryDirectory(prefix='sab-ios-discover-') as folder:
        output = Path(folder) / 'devices.json'
        proc = await asyncio.create_subprocess_exec('xcrun', 'devicectl', 'list', 'devices', '--json-output', str(output),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(proc.wait(), 20)
            if proc.returncode or not output.exists():
                raise RuntimeError('无法读取 USB 设备，请检查 Xcode 命令行工具')
            items = json.loads(output.read_text())['result']['devices']
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()
    from ios_usbmux import connected_serials
    usb_serials = await asyncio.wait_for(connected_serials(), 5)
    return discovered_devices(items, usb_serials)


def discovered_devices(items, usb_serials):
    result = []
    for item in items:
        h, c, props = item.get('hardwareProperties', {}), item.get('connectionProperties', {}), item.get('deviceProperties', {})
        if h.get('platform') == 'iOS' and h.get('udid', '').replace('-', '') in usb_serials:
            result.append(dict(serial=h['udid'], name=h.get('marketingName', props.get('name', 'iOS')),
                ready=props.get('developerModeStatus') == 'enabled' and c.get('pairingState') == 'paired',
                status='' if c.get('tunnelState') == 'connected' else 'USB 已连接，开发者连接尚未就绪；请保持解锁后连接设备'))
    return result


class MacHost:
    def __init__(self, config, path, derived, proxy):
        self.config, self.path, self.derived, self.proxy = config, path, derived, proxy
        self.jobs = {}
        self.job_lock = asyncio.Lock()
        self.last_error = ''

    def builds(self):
        return sorted(self.derived.glob('Build/Products/WebDriverAgent*.xctestrun'), key=lambda p: p.stat().st_mtime)

    async def inventory(self):
        try:
            devices = await discover()
            self.last_error = ''
        except (RuntimeError, OSError, asyncio.TimeoutError, ValueError, KeyError):
            devices = []
            self.last_error = '无法发现 USB 设备，请在 Mac 检查 Xcode 并保持设备解锁'
        for device in devices:
            job = self.jobs.get(device['serial'])
            device.setdefault('status', '')
            if job:
                bridge, wda = job['bridge'], job['wda']
                device['status'] = '连接中' if bridge.returncode is None else '桥接已退出，请重试'
                if 'Timed out while enabling automation mode' in job['log'].read_text(errors='replace')[-12000:]:
                    device['status'] = '自动化启动超时，请在设备允许自动化后重试'
                elif wda.returncode is not None:
                    device['status'] = 'WDA 已退出，请检查签名、信任和设备解锁状态'
        return dict(type='inventory', devices=devices, error=self.last_error,
                    prepared=bool(self.builds()), forwarding=bool(self.proxy or shutil.which('iproxy') or getattr(self, 'builtin_usbmux', False)))

    async def start(self, serial, token):
        # Only start a device presently connected to this Mac. No arbitrary shell RPC.
        async with self.job_lock:
            found = next((d for d in await discover() if d['serial'] == serial and d['ready']), None)
            builds = self.builds()
            if not found:
                raise RuntimeError('请解锁设备，信任电脑并开启开发者模式')
            if not builds or not (self.proxy or shutil.which('iproxy') or getattr(self, 'builtin_usbmux', False)):
                raise RuntimeError('请在 Mac 完成 WDA 签名编译及 USB 转发工具准备')
            old = self.jobs.get(serial)
            if old and old.get('token') == token and old['bridge'].returncode is None and old['wda'].returncode is None:
                return
            if old:
                await self.stop_job(old)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            folder = self.path.parent / (self.config['host_id'] + '-devices')
            folder.mkdir(exist_ok=True, mode=0o700)
            config_path = folder / (serial + '.json')
            save_config(config_path, dict(server=self.config['server'], devices=[dict(serial=serial, token=token, port=port)]))
            log_path = folder / (serial + '-wda.log')
            with log_path.open('w') as stream:
                wda = await asyncio.create_subprocess_exec('xcodebuild', '-xctestrun', str(builds[-1]), '-destination', 'id=' + serial,
                    'test-without-building', stdout=stream, stderr=stream)
            bridge_args = ([sys.executable, '--ios-bridge'] if getattr(sys, 'frozen', False)
                           else [sys.executable, str(Path(__file__).with_name('ios_bridge.py'))]) + ['--config', str(config_path)]
            if getattr(self, 'builtin_usbmux', False):
                bridge_args += ['--builtin-usbmux']
            if self.proxy:
                bridge_args += ['--pymobiledevice3', self.proxy]
            try:
                bridge = await asyncio.create_subprocess_exec(*bridge_args,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            except BaseException:
                wda.terminate(); await wda.wait()
                raise
            self.jobs[serial] = dict(bridge=bridge, wda=wda, log=log_path, token=token)
            self.config.setdefault('devices', {})[serial] = token
            save_config(self.path, self.config)

    async def stop_job(self, job):
        for key in ('bridge', 'wda'):
            proc = job[key]
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT if key == 'bridge' else signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 8)
                except asyncio.TimeoutError:
                    proc.kill(); await proc.wait()

    async def publish(self, ws):
        while True:
            await ws.send(json.dumps(await self.inventory()))
            await asyncio.sleep(5)

    async def listen(self, ws):
        async for raw in ws:
            message = json.loads(raw)
            if message.get('type') != 'connect':
                raise RuntimeError('Unexpected helper command')
            result = dict(type='result', id=message['id'], ok=False)
            try:
                if message.get('platform', 'ios') == 'ios':
                    await self.start(message['serial'], message['token'])
                else:
                    await self.start(message['serial'], message['token'], message['platform'])
                result['ok'] = True
            except (RuntimeError, OSError, asyncio.TimeoutError) as error:
                # Never return subprocess output, tokens or raw platform responses.
                result['error'] = str(error) if isinstance(error, RuntimeError) else '助手启动失败，请检查 Mac 日志'
            await ws.send(json.dumps(result))
            await ws.send(json.dumps(await self.inventory()))

    async def restore(self):
        try:
            available = {d['serial'] for d in await discover() if d['ready']}
            for serial, token in list(self.config.get('devices', {}).items()):
                if serial in available:
                    try:
                        await self.start(serial, token)
                    except (RuntimeError, OSError, asyncio.TimeoutError):
                        log.warning('Previously connected device is not ready; retry from the website')
        except (RuntimeError, OSError, asyncio.TimeoutError):
            log.warning('USB discovery unavailable; retry from the website')

    async def run(self):
        try:
            while True:
                try:
                    async with connect(provider_url(self.config['server'], '/v1/providers/ios-host'),
                        additional_headers={'Authorization': 'Bearer ' + self.config['token']}, max_size=100000,
                        ping_interval=15, ping_timeout=15) as ws:
                        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if ready.get('type') != 'ready':
                            raise RuntimeError('Invalid platform response')
                        log.info('Mac helper paired and online; return to the website to connect your iPad')
                        tasks = [asyncio.create_task(self.publish(ws)), asyncio.create_task(self.listen(ws))]
                        # Keep inventory heartbeats alive while several devices restart.
                        restoring = asyncio.create_task(self.restore())
                        try:
                            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                            for task in done:
                                task.result()
                        finally:
                            for task in [*tasks, restoring]:
                                task.cancel()
                            await asyncio.gather(*tasks, restoring, return_exceptions=True)
                except ConnectionClosed as error:
                    if error.code in {4001, 4009}:
                        log.error('Helper was revoked or is already running; pair again or stop the duplicate helper')
                        return
                    log.warning('Platform disconnected; reconnecting in 5 seconds')
                except (OSError, asyncio.TimeoutError):
                    log.warning('Platform unavailable; reconnecting in 5 seconds')
                await asyncio.sleep(5)
        finally:
            await asyncio.gather(*(self.stop_job(job) for job in self.jobs.values()))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server')
    parser.add_argument('--pair-code')
    parser.add_argument('--name', default=socket.gethostname())
    parser.add_argument('--config', type=Path, default=Path.home() / '.config/sab/ios-host.json')
    parser.add_argument('--wda-derived-data', type=Path, default=os.getenv('SAB_IOS_WDA_DERIVED_DATA'))
    parser.add_argument('--pymobiledevice3', default=os.getenv('SAB_IOS_PYMOBILEDEVICE3'))
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('The USB helper must run on the Mac connected to the iPhone/iPad')
    # Lock before exchanging a grant or replacing a running helper's credentials.
    import fcntl
    args.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.config.with_suffix('.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('This helper is already running')
        await run_helper(args, parser)


async def run_helper(args, parser):
    if args.pair_code:
        if not args.server:
            parser.error('--server is required when pairing')
        server = validate_server(args.server)
        credentials = await asyncio.to_thread(pair, server, args.pair_code, args.name)
        config = dict(server=server, **credentials, devices={})
        save_config(args.config, config)
    elif args.config.exists():
        config = json.loads(args.config.read_text())
        validate_server(config['server'])
        if args.server and validate_server(args.server) != config['server']:
            parser.error('The helper is paired to another platform; use a new pairing code')
    else:
        parser.error('Generate a pairing command on the website first')
    derived = args.wda_derived_data or Path(config.get('wda_derived_data', str(Path.home() / 'Library/Developer/Xcode/DerivedData/SAB-WDA')))
    proxy = args.pymobiledevice3 or config.get('pymobiledevice3')
    config.update(wda_derived_data=str(derived), pymobiledevice3=proxy)
    save_config(args.config, config)
    await MacHost(config, args.config, derived, proxy).run()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
