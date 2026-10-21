#!/usr/bin/env python3
"""SAB outgoing multi-device bridge: shares the existing adb server with IDEs."""
import argparse
import asyncio
import base64
import io
import json
import logging
import re
import shlex
import shutil
import uuid
import hashlib
import os
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger('sab.adb_bridge')


def provider_url(server, path):
    url = urlsplit(server)
    if url.scheme not in {'http', 'https', 'ws', 'wss'} or not url.netloc or url.username or url.password:
        raise ValueError('server must be an HTTP(S)/WS(S) platform URL without credentials')
    return urlunsplit(('wss' if url.scheme in {'https', 'wss'} else 'ws', url.netloc,
                      url.path.rstrip('/') + path, '', ''))


def parse_ui(xml, size):
    root = ET.fromstring(xml)
    package = ''
    focused = None

    def node(el):
        nonlocal package, focused
        a = el.attrib
        bounds = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', a.get('bounds', ''))
        if not bounds:
            raise ValueError('UI node has invalid bounds')
        left, top, right, bottom = map(int, bounds.groups())
        result = dict(className=a.get('class', ''), text=a.get('text', ''),
                      contentDescription=a.get('content-desc', ''), resourceId=a.get('resource-id', ''),
                      boundsInScreen=dict(left=left, top=top, right=right, bottom=bottom),
                      isVisibleToUser=right > left and bottom > top,
                      children=[node(c) for c in el if c.tag == 'node'])
        for key, attr in [('isEnabled', 'enabled'), ('isClickable', 'clickable'),
                          ('isFocusable', 'focusable'), ('isCheckable', 'checkable'),
                          ('isChecked', 'checked'), ('isScrollable', 'scrollable'), ('isSelected', 'selected')]:
            result[key] = a.get(attr) == 'true'
        if not package:
            package = a.get('package', '')
        if a.get('focused') == 'true':
            focused = result
        return result

    children = [node(c) for c in root if c.tag == 'node']
    if root.tag != 'hierarchy' or not children:
        raise ValueError('Empty UI hierarchy')
    matches = re.findall(r'(\d+)x(\d+)', size)
    if not matches:
        raise ValueError('Screen dimensions unavailable')
    width, height = map(int, matches[-1])
    if int(root.attrib.get('rotation', 0)) % 2:
        width, height = height, width
    return dict(a11y_tree=dict(className='hierarchy', isVisibleToUser=True, children=children),
                phone_state=dict(packageName=package, currentApp=package,
                                 isEditable='EditText' in (focused or {}).get('className', ''), focusedElement=focused),
                device_context=dict(screen_bounds=dict(width=width, height=height)))


class Bridge:
    provider_path = '/v1/providers/adb-bridge'
    video_available = True

    def __init__(self, adb, server, serial, token):
        self.adb, self.server, self.serial, self.token = adb, server, serial, token
        self.lock = asyncio.Lock()
        self.video_task = None
        self.stream_id = None

    async def command(self, *args, timeout=25):
        proc = await asyncio.create_subprocess_exec(self.adb, '-s', self.serial, *args,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            if proc.returncode:
                raise RuntimeError(err.decode(errors='replace').strip() or f'ADB exit {proc.returncode}')
            return out
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def shell(self, command):
        return (await self.command('shell', command)).decode(errors='replace')

    async def check_device(self):
        state = await self.command('get-state', timeout=5)
        if state.strip() != b'device':
            raise RuntimeError('Device offline')

    async def stop_video(self, stream_id=None):
        if stream_id is not None and self.stream_id != stream_id:
            return
        if self.video_task:
            self.video_task.cancel()
            await asyncio.gather(self.video_task, return_exceptions=True)
        self.video_task = None
        self.stream_id = None

    async def video(self, stream_id, ready):
        proc = None
        try:
            async with connect(provider_url(self.server, '/v1/providers/video'),
                    additional_headers={'Authorization': f'Bearer {self.token}', 'X-Stream-ID': stream_id},
                    max_size=256 * 1024, open_timeout=10) as ws:
                # Every preview gets a new encoder with SPS/PPS + IDR. A 175s
                # cycle ends this connection; the browser reconnects and resets.
                proc = await asyncio.create_subprocess_exec(self.adb, '-s', self.serial, 'exec-out', 'screenrecord',
                        '--output-format=h264', '--time-limit', '175', '--bit-rate', '2000000', '-',
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                ready.set_result(True)

                async def pump():
                    while chunk := await proc.stdout.read(16384):
                        await asyncio.wait_for(ws.send(chunk), 5)

                tasks = [asyncio.create_task(pump()), asyncio.create_task(ws.wait_closed())]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            log.warning('Video ended for %s: %s', self.serial, type(error).__name__)
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def dispatch(self, method, p):
        def num(key, default=None, limit=100000):
            value = p.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= limit:
                raise ValueError(f'Invalid {key}')
            return round(value)

        def pkg():
            value = p.get('package', '')
            if not re.fullmatch(r'[\w]+(?:\.[\w]+)+', value, re.ASCII):
                raise ValueError('Invalid package')
            return value

        if method == 'ping':
            return 'pong'
        if method == 'video/start':
            stream_id = p.get('stream_id', '')
            if not re.fullmatch(r'[a-f0-9]{32}', stream_id):
                raise ValueError('Invalid stream ID')
            await self.stop_video()
            self.stream_id = stream_id
            ready = asyncio.get_running_loop().create_future()
            self.video_task = asyncio.create_task(self.video(stream_id, ready))
            await asyncio.wait_for(ready, 12)
            return 'Video started'
        if method == 'video/stop':
            await self.stop_video(p.get('stream_id'))
            return 'Video stopped'
        async with self.lock:
            if method == 'app/install':
                package = pkg()
                url = p.get('download_url', '')
                expected = p.get('sha256', '')
                if urlsplit(url).scheme not in {'http', 'https'} or not re.fullmatch(r'[a-f0-9]{64}', expected):
                    raise ValueError('Invalid APK source')
                sdk = Path(os.getenv('ANDROID_HOME', os.getenv('ANDROID_SDK_ROOT', str(Path.home() / 'Library/Android/sdk'))))
                aapt = shutil.which('aapt') or next((str(path) for path in sorted((sdk / 'build-tools').glob('*/aapt'), reverse=True)), None)
                if not aapt:
                    raise RuntimeError('APK verification requires Android SDK build-tools aapt on the bridge computer')
                with tempfile.TemporaryDirectory(prefix='sab-apk-') as folder:
                    apk = Path(folder) / 'app.apk'
                    def download():
                        digest, size = hashlib.sha256(), 0
                        with urllib.request.urlopen(url, timeout=60) as response, apk.open('wb') as output:
                            while chunk := response.read(1024 * 1024):
                                size += len(chunk)
                                if size > 512 * 1024 * 1024:
                                    raise ValueError('APK too large')
                                digest.update(chunk); output.write(chunk)
                        if digest.hexdigest() != expected:
                            raise ValueError('APK SHA256 mismatch')
                    await asyncio.to_thread(download)
                    process = await asyncio.create_subprocess_exec(aapt, 'dump', 'badging', str(apk), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        out, _ = await asyncio.wait_for(process.communicate(), 30)
                    except BaseException:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                        raise
                    match = re.search(r"package: name='([^']+)' versionCode='([^']+)' versionName='([^']*)'", out.decode(errors='replace'))
                    if process.returncode or not match or match[1] != package:
                        raise ValueError('APK package does not match the configured app')
                    result = (await self.command('install', '-r', str(apk), timeout=120)).decode(errors='replace')
                    if 'Success' not in result:
                        raise RuntimeError('APK installation failed')
                    return {'package': package, 'version_code': match[2], 'version_name': match[3], 'sha256': expected}
            if method == 'app/reset':
                result = await self.shell('pm clear ' + shlex.quote(pkg()))
                if result.strip() != 'Success':
                    raise RuntimeError('Application data reset failed')
                return 'Application data cleared'
            if method == 'diagnostics':
                return {'status': 'collected', 'scope': 'recent device logs, not network capture',
                        'logcat': (await self.shell('logcat -d -t 400'))[-200000:],
                        'last_anr': (await self.shell('dumpsys activity lastanr'))[-50000:],
                        'device': (await self.shell('getprop ro.product.model; getprop ro.build.version.release; wm size')).strip()}
            if method == 'screenshot':
                raw = await self.command('exec-out', 'screencap', '-p')
                def encode():
                    with Image.open(io.BytesIO(raw)) as image:
                        out = io.BytesIO()
                        # Keep device dimensions; the agent downsizes once for the model.
                        image.save(out, 'PNG')
                        return base64.b64encode(out.getvalue()).decode()
                return await asyncio.to_thread(encode)
            if method in {'state', 'state/full-tree'}:
                path = '/data/local/tmp/sab-' + uuid.uuid4().hex + '.xml'
                xml = await self.shell(f'rm -f {path}; uiautomator dump {path} >/dev/null && cat {path}; result=$?; rm -f {path}; exit $result')
                return parse_ui(xml, await self.shell('wm size'))
            if method == 'tap':
                await self.shell(f"input tap {num('x')} {num('y')}")
            elif method == 'swipe':
                await self.shell(f"input swipe {num('startX')} {num('startY')} {num('endX')} {num('endY')} {num('duration', 500, 10000)}")
            elif method == 'keyboard/key':
                await self.shell(f"input keyevent {num('key_code', limit=300)}")
            elif method == 'keyboard/input':
                text = base64.b64decode(p.get('base64_text', ''), validate=True).decode()
                if p.get('clear') or not re.fullmatch(r'[\x20-\x7e]*', text) or '%' in text:
                    raise ValueError('ADB bridge supports ASCII input without %; clear/Unicode input needs the App mode')
                await self.shell('input text ' + shlex.quote(text.replace(' ', '%s')))
            elif method in {'app', 'app/launch'}:
                package = pkg()
                if p.get('stopBeforeLaunch'):
                    await self.shell('am force-stop ' + shlex.quote(package))
                activity = p.get('activity')
                if activity:
                    if not re.fullmatch(r'[\w.$]+', activity, re.ASCII):
                        raise ValueError('Invalid activity')
                    activity = package + '/' + activity
                else:
                    output = await self.shell('cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p ' + shlex.quote(package))
                    entries = [line.strip() for line in output.splitlines()
                               if re.fullmatch(re.escape(package) + r'/[\w.$]+', line.strip(), re.ASCII)]
                    preferred = [entry for entry in entries if not entry.startswith(package + '/leakcanary.')]
                    candidates = preferred or entries
                    if len(candidates) != 1:
                        raise ValueError('App has no unique launcher entry; specify its activity')
                    activity = candidates[0]
                if not re.fullmatch(r'[\w.$]+/[\w.$]+', activity, re.ASCII):
                    raise ValueError('App entry not found')
                result = await self.shell('am start -W -n ' + shlex.quote(activity))
                if re.search(r'Error:|Exception|Status: (?!ok)', result):
                    raise RuntimeError(result)
                return 'Launched ' + package
            elif method == 'app/stop':
                await self.shell('am force-stop ' + shlex.quote(pkg()))
            elif method == 'packages':
                return [dict(packageName=line[8:], appName=line[8:]) for line in (await self.shell('pm list packages')).splitlines() if line.startswith('package:')]
            elif method == 'globalAction' and p.get('action') == 4:
                await self.shell('cmd statusbar expand-notifications')
            else:
                raise ValueError(f'ADB bridge does not support {method}')
            return 'OK'

    async def run(self):
        while True:
            try:
                # Retry only when the configured physical device is available.
                await self.check_device()
                async with connect(provider_url(self.server, self.provider_path),
                        additional_headers={'Authorization': f'Bearer {self.token}'},
                        open_timeout=10, max_size=4 * 1024 * 1024) as ws:
                    hello = json.loads(await ws.recv())
                    if hello.get('type') != 'ready':
                        raise ValueError('Invalid provider handshake')
                    await ws.send(json.dumps({'type': 'capabilities', 'video': self.video_available}))
                    log.info('Connected device %s', self.serial)
                    async def receive_commands():
                        async for raw in ws:
                            msg = json.loads(raw)
                            try:
                                result = await self.dispatch(msg.get('method', ''), msg.get('params') or {})
                                response = dict(id=msg.get('id'), result=result)
                            except Exception as error:
                                response = dict(id=msg.get('id'), status='error', result=str(error))
                            await ws.send(json.dumps(response))

                    async def watch_device():
                        while True:
                            await asyncio.sleep(5)
                            await self.check_device()

                    tasks = [asyncio.create_task(receive_commands()), asyncio.create_task(watch_device())]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except ConnectionClosed as error:
                if error.code in {4001, 4009}:
                    log.error('Device %s rejected (auth or duplicate connection); stopping', self.serial)
                    return
                log.info('Device %s disconnected; retrying', self.serial)
            except Exception as error:
                # Avoid credential-containing URLs/headers in logs.
                log.warning('Device %s unavailable (%s); retrying', self.serial, type(error).__name__)
            finally:
                await self.stop_video()
            await asyncio.sleep(3)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='JSON with server and devices: [{serial, token}]')
    parser.add_argument('--adb', default=shutil.which('adb') or str(Path.home() / 'Library/Android/sdk/platform-tools/adb'))
    parser.add_argument('--list', action='store_true', help='List device serials without connecting to SAB')
    args = parser.parse_args()
    if args.list:
        proc = await asyncio.create_subprocess_exec(args.adb, 'devices', '-l')
        await proc.wait()
        return
    if not args.config:
        parser.error('--config is required unless --list is used')
    config = json.loads(args.config.read_text())
    provider_url(config['server'], '/v1/providers/adb-bridge')
    devices = config.get('devices', [])
    if not devices or any(not d.get('serial') or not d.get('token') for d in devices):
        parser.error('devices must contain serial and token')
    if len({d['serial'] for d in devices}) != len(devices) or len({d['token'] for d in devices}) != len(devices):
        parser.error('Each serial/token must be unique')
    await asyncio.gather(*(Bridge(args.adb, config['server'], d['serial'], d['token']).run() for d in devices))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
