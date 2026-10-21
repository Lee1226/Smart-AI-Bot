#!/usr/bin/env python3
"""SAB iOS USB bridge. A signed, running WebDriverAgent and iproxy are required."""
import argparse
import asyncio
import base64
import io
import json
import logging
import math
import re
import shutil
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from adb_bridge import Bridge, provider_url

EDITABLE = {'XCUIElementTypeTextField', 'XCUIElementTypeSecureTextField',
            'XCUIElementTypeTextView', 'XCUIElementTypeSearchField'}


def parse_source(xml, size, app, focused=None):
    """WDA coordinates are logical points, not Retina screenshot pixels."""
    root = ET.fromstring(xml)
    def node(el):
        a = el.attrib
        kind = a.get('type', el.tag)
        x, y, w, h = [float(a.get(k, 0)) for k in ('x', 'y', 'width', 'height')]
        secure = kind == 'XCUIElementTypeSecureTextField'
        return dict(className=kind, text='' if secure else a.get('value') or a.get('label', ''),
                    contentDescription='' if secure else a.get('label', ''), resourceId='' if secure else a.get('name', ''),
                    boundsInScreen=dict(left=round(x), top=round(y), right=round(x+w), bottom=round(y+h)),
                    isEnabled=a.get('enabled', 'true') == 'true',
                    isVisibleToUser=a.get('visible', 'true') == 'true' and ((w > 0 and h > 0) or kind == 'AppiumAUT'),
                    isClickable=kind.endswith(('Button', 'Cell', 'Link', 'Switch')),
                    isFocusable=kind in EDITABLE, isScrollable=kind.endswith(('ScrollView', 'Table', 'CollectionView')),
                    children=[node(c) for c in el])
    return dict(a11y_tree=node(root), phone_state=dict(
        packageName=app.get('bundleId', ''), currentApp=app.get('name', ''),
        isEditable=bool(focused and focused.get('className') in EDITABLE), focusedElement=focused),
        device_context=dict(screen_bounds=size))


def custom_code_focus(xml):
    """Custom code grids may hide their zero-size UITextField from XCTest."""
    root = ET.fromstring(xml)
    nodes = list(root.iter())
    def visible(el):
        a = el.attrib
        return a.get('visible') == 'true' and a.get('enabled', 'true') == 'true'
    keyboard = any(el.attrib.get('type', el.tag) == 'XCUIElementTypeKeyboard' and visible(el) for el in nodes)
    code_label = any(visible(el) and re.search(r'验证码|verification code|one.time code',
                     el.attrib.get('label', ''), re.I) for el in nodes)
    cells = sum(el.attrib.get('type', el.tag) == 'XCUIElementTypeCell' and visible(el) for el in nodes)
    normal_input = any(el.attrib.get('type', el.tag) in EDITABLE and visible(el)
                       and float(el.attrib.get('width', 0)) > 0 and float(el.attrib.get('height', 0)) > 0 for el in nodes)
    if keyboard and code_label and cells >= 4 and not normal_input:
        return dict(className='XCUIElementTypeTextField', text='', customCodeInput=True)
    return None


class IosBridge(Bridge):
    provider_path = '/v1/providers/ios-bridge'
    video_available = False

    def __init__(self, server, serial, token, port):
        super().__init__('', server, serial, token)
        self.url = f'http://127.0.0.1:{port}'
        self.session_id = None
        self.proxy = None

    async def request(self, method, path, data=None):
        def call():
            body = None if data is None else json.dumps(data).encode()
            req = urllib.request.Request(self.url + path, data=body, method=method,
                                         headers={'Content-Type': 'application/json'})
            try:
                # The WDA endpoint is always loopback; ignore proxy environment variables.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    response = opener.open(req, timeout=25)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise RuntimeError('WDA response too large')
                result = json.loads(raw)
            except (urllib.error.URLError, TimeoutError) as exc:
                # WDA errors may echo typed passwords. Never relay response bodies.
                raise RuntimeError('WDA request failed; check USB, runner and session') from None
            except (ValueError, TypeError):
                raise RuntimeError('Invalid WDA response') from None
            value = result.get('value')
            if result.get('status', 0) or isinstance(value, dict) and value.get('error'):
                if isinstance(value, dict) and value.get('error') == 'invalid session id':
                    self.session_id = None
                raise RuntimeError('WDA command rejected')
            return result
        return await asyncio.to_thread(call)

    async def check_device(self):
        if self.proxy is not None and self.proxy.returncode is not None:
            raise RuntimeError('USB forwarding stopped')
        try:
            await self.request('GET', '/status')
        except RuntimeError:
            self.session_id = None
            raise

    async def session(self):
        if not self.session_id:
            result = await self.request('POST', '/session', {'capabilities': {'alwaysMatch': {}}})
            self.session_id = result.get('sessionId') or (result.get('value') or {}).get('sessionId')
            if not self.session_id:
                raise RuntimeError('WDA session unavailable')
        return '/session/' + quote(self.session_id, safe='')

    async def command_value(self, method, suffix, data=None):
        return (await self.request(method, await self.session() + suffix, data)).get('value')

    async def focused_element(self, xml=None):
        try:
            active = await self.command_value('GET', '/element/active')
            eid = active.get('element-6066-11e4-a52e-4f735466cecf') or active.get('ELEMENT')
            if not eid:
                raise RuntimeError('No active editable element')
            path = '/element/' + quote(eid, safe='')
            kind = await self.command_value('GET', path + '/attribute/type')
            if kind not in EDITABLE:
                raise RuntimeError('Active element is not editable')
            return dict(className=kind, text='', elementId=eid)
        except (RuntimeError, AttributeError):
            # No known editable focus is not permission to fill credentials.
            try:
                if xml is None:
                    xml = await self.command_value('GET', '/source?format=xml')
                return custom_code_focus(xml)
            except (RuntimeError, ET.ParseError):
                return None

    async def dispatch(self, method, p):
        def num(key, maximum=100000):
            value = p.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError('Invalid ' + key)
            return value
        def bundle():
            value = p.get('package', '')
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', value):
                raise ValueError('Invalid iOS Bundle ID')
            return value
        if method == 'ping':
            await self.check_device()
            return 'pong'
        async with self.lock:
            if method == 'screenshot':
                size = await self.command_value('GET', '/window/size')
                raw = base64.b64decode(await self.command_value('GET', '/screenshot'), validate=True)
                with Image.open(io.BytesIO(raw)) as image:
                    out = io.BytesIO()
                    # Use WDA logical coordinates; the agent alone downsizes for the model.
                    image.resize((round(size['width']), round(size['height']))).save(out, 'PNG')
                    return base64.b64encode(out.getvalue()).decode()
            if method in {'state', 'state/full-tree'}:
                size = await self.command_value('GET', '/window/size')
                app = await self.command_value('GET', '/wda/activeAppInfo')
                xml = await self.command_value('GET', '/source?format=xml')
                return parse_source(xml, size, app, await self.focused_element(xml))
            if method == 'tap':
                await self.command_value('POST', '/wda/tap', dict(x=num('x'), y=num('y')))
            elif method == 'swipe':
                params = dict(fromX=num('startX'), fromY=num('startY'), toX=num('endX'), toY=num('endY'),
                              duration=num('duration', 10000)/1000)
                await self.command_value('POST', '/wda/dragfromtoforduration', params)
            elif method == 'keyboard/key':
                code = num('key_code', 300)
                if code == 3:
                    await self.request('POST', '/wda/homescreen', {})
                elif code in {66, 67, 62}:
                    await self.command_value('POST', '/wda/keys', {'value': [{66:'\n', 67:'\b', 62:' '}[code]]})
                else:
                    raise ValueError('iOS has no universal Back key; use visible navigation. This key is unsupported.')
            elif method == 'keyboard/input':
                text = base64.b64decode(p.get('base64_text', ''), validate=True).decode()
                if not text or len(text) > 2000:
                    raise ValueError('Text must contain 1–2000 characters')
                active = await self.focused_element()
                if not active:
                    raise ValueError('Focus an editable iOS field first')
                if active.get('customCodeInput'):
                    if not re.fullmatch(r'[0-9]{1,8}', text):
                        raise ValueError('Custom verification code fields accept only 1–8 digits')
                    if p.get('clear'):
                        await self.command_value('POST', '/wda/keys', {'value': ['\b' * 8]})
                elif p.get('clear'):
                    await self.command_value('POST', '/element/' + quote(active['elementId'], safe='') + '/clear', {})
                await self.command_value('POST', '/wda/keys', {'value': [text]})
                return 'Text entered'
            elif method in {'app', 'app/launch'}:
                target = bundle()
                if p.get('activity'):
                    raise ValueError('iOS uses a Bundle ID, not an Android Activity')
                if p.get('stopBeforeLaunch'):
                    await self.command_value('POST', '/wda/apps/terminate', {'bundleId': target})
                await self.command_value('POST', '/wda/apps/launch', {'bundleId': target})
                return 'Launched ' + target
            elif method == 'app/stop':
                await self.command_value('POST', '/wda/apps/terminate', {'bundleId': bundle()})
            else:
                raise ValueError('Unsupported iOS bridge operation: app listing, installation, reset, diagnostics, recording and video are unavailable')
            return 'OK'


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--iproxy', default=shutil.which('iproxy'))
    parser.add_argument('--pymobiledevice3', help='Alternative pymobiledevice3 executable for USB forwarding')
    parser.add_argument('--check', action='store_true', help='Check USB forwarding and WDA, without connecting to SAB')
    parser.add_argument('--builtin-usbmux', action='store_true', help='Use the bundled macOS USB forwarder')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    provider_url(config['server'], IosBridge.provider_path)
    devices = config.get('devices', [])
    if not devices or any(not re.fullmatch(r'[A-Za-z0-9-]+', d.get('serial', '')) or not d.get('token') for d in devices):
        parser.error('devices must contain a USB UDID (serial) and token')
    if len({d['serial'] for d in devices}) != len(devices) or len({d['token'] for d in devices}) != len(devices):
        parser.error('Each USB UDID/token must be unique')
    ports = [d.get('port', 8100+i) for i, d in enumerate(devices)]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != len(ports):
        parser.error('Each device needs a unique local port (1024–65535)')
    if not args.iproxy and not args.pymobiledevice3 and not args.builtin_usbmux:
        parser.error('iproxy is required. Install libusbmuxd and start the signed WDA runner in Xcode.')
    bridges = []
    try:
        for device, port in zip(devices, ports):
            # Reject occupied ports to avoid controlling another device's WDA.
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            bridge = IosBridge(config['server'], device['serial'], device['token'], port)
            proxy_command = ([args.pymobiledevice3, 'usbmux', 'forward', '--serial', device['serial'], '--host', '127.0.0.1', str(port), '8100']
                             if args.pymobiledevice3 else [args.iproxy, '-u', device['serial'], '-s', '127.0.0.1', f'{port}:8100'])
            if args.builtin_usbmux:
                from ios_usbmux import forward
                bridge.usbmux_server = await forward(device['serial'], port)
            else:
                bridge.proxy = await asyncio.create_subprocess_exec(*proxy_command,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            bridges.append(bridge)
        if args.check:
            # iproxy starts asynchronously; only bounded readiness probes.
            for bridge in bridges:
                for attempt in range(5):
                    try:
                        await bridge.check_device()
                        break
                    except RuntimeError:
                        if attempt == 4:
                            raise
                        await asyncio.sleep(0.5)
                print('USB/WDA reachable: ' + bridge.serial)
        else:
            await asyncio.gather(*(b.run() for b in bridges))
    finally:
        for bridge in bridges:
            if hasattr(bridge, 'usbmux_server'):
                bridge.usbmux_server.close()
                await bridge.usbmux_server.wait_closed()
            if bridge.proxy and bridge.proxy.returncode is None:
                bridge.proxy.terminate()
                await bridge.proxy.wait()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
