#!/usr/bin/env python3
"""SAB outgoing HarmonyOS HDC bridge, for phones and local emulators."""
import argparse
import asyncio
import base64
import io
import json
import logging
import math
import re
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path

from PIL import Image
from adb_bridge import Bridge, provider_url


# SAB RPC key codes retain their existing Android-compatible wire format.
KEY_CODES = {4: 2, 3: 1, 66: 2054, 67: 2055, 61: 2049, 62: 2050}


def targets(output):
    return [line.strip() for line in output.splitlines()
            if line.strip() and line.strip() != '[Empty]' and not line.startswith('[Fail]')]


def parse_layout(raw):
    layout = json.loads(raw)
    roots = layout if isinstance(layout, list) else [layout]
    package = ''
    width = height = 0
    focused = None

    def flag(value, default=False):
        return default if value is None or value == '' else value is True or str(value).lower() == 'true'

    def convert(item):
        nonlocal package, width, height, focused
        attrs = item.get('attributes', item)
        bounds = attrs.get('bounds', '')
        if isinstance(bounds, dict):
            left, top, right, bottom = [int(bounds.get(k, 0)) for k in ('left', 'top', 'right', 'bottom')]
        else:
            match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', bounds)
            if not match:
                raise ValueError('Harmony UI node has invalid bounds')
            left, top, right, bottom = map(int, match.groups())
        width, height = max(width, right), max(height, bottom)
        package = package or attrs.get('bundleName', '')
        result = dict(className=attrs.get('type', ''), text=attrs.get('text', ''),
                      contentDescription=attrs.get('description', ''), resourceId=attrs.get('id', ''),
                      boundsInScreen=dict(left=left, top=top, right=right, bottom=bottom),
                      isVisibleToUser=flag(attrs.get('visible'), right > left and bottom > top),
                      isEnabled=flag(attrs.get('enabled'), True),
                      isFocused=flag(attrs.get('focused')),
                      children=[])
        for key in ('clickable', 'focusable', 'checkable', 'checked', 'scrollable', 'selected'):
            result['is' + key.title()] = flag(attrs.get(key))
        # ArkUI marks every ancestor on the focus path as focused. Traverse
        # parent first so the actual descendant wins, rather than replacing a
        # focused TextInput with its Stack/Navigation/root after recursion.
        if result['isFocused']:
            focused = result
        result['children'] = [convert(child) for child in item.get('children', [])]
        return result

    children = [convert(root) for root in roots]
    return dict(a11y_tree=dict(className='HarmonyUI', children=children),
                phone_state=dict(packageName=package, currentApp=package, focusedElement=focused,
                                 isEditable=(focused or {}).get('className') in {'TextInput', 'TextArea', 'Search'}),
                device_context=dict(screen_bounds=dict(width=width, height=height)))


def bundle_info(raw):
    start = raw.find('{')
    if start < 0:
        raise ValueError('Bundle information unavailable')
    return json.JSONDecoder().raw_decode(raw[start:])[0]


def main_ability(info):
    for module in info.get('hapModuleInfos', info.get('moduleInfos', [])):
        name = module.get('mainElement') or module.get('mainAbility')
        if name:
            return name
    name = info.get('mainAbility')
    if name:
        return name
    raise ValueError('Cannot resolve launch Ability; provide activity as the Harmony Ability name')


class HdcBridge(Bridge):
    provider_path = '/v1/providers/hdc-bridge'
    video_available = False

    async def command(self, *args, timeout=25):
        proc = await asyncio.create_subprocess_exec(self.adb, '-t', self.serial, *args,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            if proc.returncode or out.lstrip().startswith(b'[Fail]'):
                raise RuntimeError(err.decode(errors='replace').strip() or out.decode(errors='replace').strip() or f'HDC exit {proc.returncode}')
            return out
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def check_device(self):
        proc = await asyncio.create_subprocess_exec(self.adb, 'list', 'targets',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            if proc.returncode or self.serial not in targets(out.decode(errors='replace')):
                raise RuntimeError('HDC device offline')
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def shell(self, command):
        # HDC may return host exit 0 even when the remote shell command failed.
        marker = 'SAB_EXIT_' + uuid.uuid4().hex
        script = command + "; sab_result=$?; printf '\\n" + marker + ":%s\\n' \"$sab_result\""
        output = (await self.command('shell', script)).decode(errors='replace')
        match = re.search(r'\n' + marker + r':(\d+)\s*$', output)
        if not match:
            raise RuntimeError('HDC did not return a remote command status')
        text = output[:match.start()].strip()
        if int(match.group(1)) or re.search(r'(?im)^\s*(?:error:|\[fail\]|failed\b|illegal argument|missing parameter|unknown command)', text):
            raise RuntimeError(text or 'Harmony shell command failed')
        return text

    async def capture_file(self, kind):
        remote = '/data/local/tmp/sab-' + uuid.uuid4().hex + ('.png' if kind == 'screenCap' else '.json')
        try:
            await self.shell(f'uitest {kind} -p {remote}')
            with tempfile.TemporaryDirectory(prefix='sab-hdc-') as directory:
                local = Path(directory) / Path(remote).name
                await self.command('file', 'recv', remote, str(local))
                return await asyncio.to_thread(local.read_bytes)
        finally:
            try:
                await self.shell('rm -f ' + remote)
            except Exception:
                logging.getLogger(__name__).debug('Could not remove HDC temporary capture')

    async def dispatch(self, method, p):
        def num(key, default=None, maximum=100000):
            value = p.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError('Invalid ' + key)
            return round(value)

        def identifier(value, label):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][\w.]*(?:\.[\w]+)*', value, re.ASCII):
                raise ValueError('Invalid ' + label)
            return shlex.quote(value)

        if method == 'ping':
            return 'pong'
        async with self.lock:
            if method == 'screenshot':
                raw = await self.capture_file('screenCap')
                def encode():
                    with Image.open(io.BytesIO(raw)) as image:
                        out = io.BytesIO()
                        image.resize((max(1, image.width // 2), max(1, image.height // 2))).save(out, 'PNG')
                        return base64.b64encode(out.getvalue()).decode()
                return await asyncio.to_thread(encode)
            if method in {'state', 'state/full-tree'}:
                return parse_layout((await self.capture_file('dumpLayout')).decode())
            if method == 'tap':
                await self.shell(f"uitest uiInput click {num('x')} {num('y')}")
            elif method == 'swipe':
                x1, y1, x2, y2 = [num(k) for k in ('startX', 'startY', 'endX', 'endY')]
                duration = max(1, num('duration', 500, 10000))
                # uitest's last argument is speed in px/s, not milliseconds.
                speed = max(200, min(15000, round(math.hypot(x2 - x1, y2 - y1) * 1000 / duration)))
                await self.shell(f'uitest uiInput swipe {x1} {y1} {x2} {y2} {speed}')
            elif method == 'keyboard/key':
                code = KEY_CODES.get(num('key_code', maximum=300))
                if code is None:
                    raise ValueError('This key is not supported by the HDC bridge')
                await self.shell(f'uitest uiInput keyEvent {code}')
            elif method == 'keyboard/input':
                if p.get('clear'):
                    raise ValueError('HDC input clearing is not supported; select or clear the field first')
                text = base64.b64decode(p.get('base64_text', ''), validate=True).decode()
                if len(text) > 2000 or not re.fullmatch(r'[\x20-\x7e]+', text):
                    raise ValueError('HDC bridge currently supports non-empty ASCII input only')
                await self.shell('uitest uiInput text ' + shlex.quote(text))
            elif method in {'app', 'app/launch'}:
                package = identifier(p.get('package', ''), 'bundle')
                ability = p.get('activity') or main_ability(bundle_info(await self.shell('bm dump -n ' + package)))
                ability = identifier(ability, 'Ability')
                if p.get('stopBeforeLaunch'):
                    await self.shell('aa force-stop ' + package)
                result = await self.shell(f'aa start -b {package} -a {ability}')
                if 'success' not in result.lower():
                    raise RuntimeError('Ability launch was not confirmed: ' + result)
                return 'Launched ' + p['package']
            elif method == 'app/stop':
                await self.shell('aa force-stop ' + identifier(p.get('package', ''), 'bundle'))
            elif method == 'packages':
                result = await self.shell('bm dump -a')
                return [dict(packageName=line.strip(), appName=line.strip()) for line in result.splitlines()
                        if re.fullmatch(r'[\w]+(?:\.[\w]+)+', line.strip(), re.ASCII)]
            else:
                raise ValueError(f'HDC bridge does not support {method}; Unicode/clear input, APK install, recording and video are not supported')
            return 'OK'


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='JSON with server and devices: [{serial, token}]')
    parser.add_argument('--hdc', default=shutil.which('hdc') or '/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc')
    parser.add_argument('--list', action='store_true', help='List HDC targets without connecting to SAB')
    args = parser.parse_args()
    if args.list:
        proc = await asyncio.create_subprocess_exec(args.hdc, 'list', 'targets')
        raise SystemExit(await proc.wait())
    if not args.config:
        parser.error('--config is required unless --list is used')
    config = json.loads(args.config.read_text())
    provider_url(config['server'], HdcBridge.provider_path)
    devices = config.get('devices', [])
    if not devices or any(not d.get('serial') or not d.get('token') for d in devices):
        parser.error('devices must contain serial and token')
    if len({d['serial'] for d in devices}) != len(devices) or len({d['token'] for d in devices}) != len(devices):
        parser.error('Each serial/token must be unique')
    await asyncio.gather(*(HdcBridge(args.hdc, config['server'], d['serial'], d['token']).run() for d in devices))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
