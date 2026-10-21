"""Mac helpers connect outbound: the website never accesses a user's loopback ports."""
import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from auth.access import require_account_id, require_role
from db.database import AsyncSessionLocal
from db.models import Device, IosHost
from routers.devices import get_db
from ws.portal_ws import connected_devices

router = APIRouter(prefix='/api/ios-host', tags=['ios-host'])
_pairings = {}
_hosts = {}
_lock = asyncio.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class HostSession:
    ws: WebSocket
    inventory: dict = field(default_factory=lambda: {'devices': [], 'prepared': False, 'forwarding': False})
    pending: dict = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload):
        async with self.send_lock:
            await self.ws.send_json(payload)

    async def connect(self, serial, token, platform='ios'):
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send(dict(type='connect', id=request_id, serial=serial, token=token, platform=platform))
            result = await asyncio.wait_for(future, 25)
            if not result.get('ok'):
                raise HTTPException(409, result.get('error', '本机助手连接失败'))
        except asyncio.TimeoutError:
            raise HTTPException(504, '助手响应超时，请检查 Mac 上的助手日志后重试')
        except (RuntimeError, WebSocketDisconnect, OSError):
            raise HTTPException(409, 'Mac 助手已断开，请重试')
        finally:
            self.pending.pop(request_id, None)


@router.post('/pairings')
async def pairing(request: Request):
    account = require_account_id(request)
    require_role(request, 'owner', 'admin')
    async with _lock:
        now = time.monotonic()
        for key, value in list(_pairings.items()):
            if value[1] < now or value[0] == account:
                _pairings.pop(key, None)
        code = secrets.token_urlsafe(24)
        _pairings[digest(code)] = (account, now + 300)
    return {'code': code, 'expires_in': 300}


class PairRequest(BaseModel):
    code: str = Field(min_length=20, max_length=100)
    name: str = Field(min_length=1, max_length=100)


@router.post('/pair')
async def pair(body: PairRequest, db=Depends(get_db)):
    # This endpoint exchanges a high-entropy, one-use, five-minute grant only.
    async with _lock:
        grant = _pairings.pop(digest(body.code), None)
        if not grant or grant[1] < time.monotonic():
            raise HTTPException(401, '配对码已失效，请在网页重新生成')
        token = secrets.token_urlsafe(32)
        host = IosHost(account_id=grant[0], name=body.name, token_hash=digest(token))
        db.add(host)
        await db.commit()
        await db.refresh(host)
    return {'host_id': host.id, 'token': token}


@router.get('')
async def hosts(request: Request, db=Depends(get_db)):
    rows = (await db.execute(select(IosHost).where(IosHost.account_id == require_account_id(request)))).scalars().all()
    result = []
    for row in rows:
        session = _hosts.get(row.id)
        mappings = json.loads(row.devices_json)
        inventory = session.inventory if session else {'devices': [], 'prepared': False, 'forwarding': False}
        devices = []
        for item in inventory['devices']:
            device_id = mappings.get(device_key(item.get('platform', 'ios'), item['serial']))
            conn = connected_devices.get(device_id)
            devices.append({**item, 'device_id': device_id, 'online': bool(conn and conn.is_connected)})
        result.append(dict(id=row.id, name=row.name, online=bool(session), **{**inventory, 'devices': devices}))
    return result


class ConnectRequest(BaseModel):
    serial: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')
    platform: Literal['ios', 'android', 'harmony'] = 'ios'


def device_key(platform, serial):
    return serial if platform == 'ios' else platform + ':' + serial


@router.post('/{host_id}/connect')
async def connect_device(host_id: str, body: ConnectRequest, request: Request, db=Depends(get_db)):
    # Serialize map updates and revocation, including concurrent connect clicks.
    async with _lock:
        host = await db.get(IosHost, host_id)
        if not host or host.account_id != require_account_id(request):
            raise HTTPException(404, '助手不存在')
        session = _hosts.get(host_id)
        if not session:
            raise HTTPException(409, 'Mac 助手离线，请先在连接设备的 Mac 上启动助手')
        device = next((d for d in session.inventory['devices'] if d['serial'] == body.serial and d.get('platform', 'ios') == body.platform), None)
        if not device or not device['ready']:
            raise HTTPException(409, '请解锁设备，信任电脑并开启开发者模式')
        if body.platform == 'ios' and (not session.inventory['prepared'] or not session.inventory['forwarding']):
            raise HTTPException(409, '请在 Mac 上完成 WDA 签名编译及 USB 转发工具准备')
        mapping = json.loads(host.devices_json)
        key = device_key(body.platform, body.serial)
        connection_type = {'ios': 'ios_bridge', 'android': 'adb_bridge', 'harmony': 'hdc_bridge'}[body.platform]
        row = await db.get(Device, mapping.get(key, ''))
        if not row or row.account_id != host.account_id or row.connection_type != connection_type:
            row = Device(name=device['name'] + ' · USB', token=secrets.token_urlsafe(32),
                         account_id=host.account_id, connection_type=connection_type)
            db.add(row)
            await db.flush()
            mapping[key] = row.id
            host.devices_json = json.dumps(mapping)
            await db.commit()
        device_id, token = row.id, row.token
    await session.connect(body.serial, token, body.platform)
    return {'device_id': device_id}


@router.delete('/{host_id}')
async def revoke(host_id: str, request: Request, db=Depends(get_db)):
    require_role(request, 'owner', 'admin')
    async with _lock:
        host = await db.get(IosHost, host_id)
        if not host or host.account_id != require_account_id(request):
            raise HTTPException(404, '助手不存在')
        ids = list(json.loads(host.devices_json).values())
        for device_id in ids:
            device = await db.get(Device, device_id)
            if device and device.account_id == host.account_id:
                device.token = secrets.token_urlsafe(32)
        await db.delete(host)
        await db.commit()
        session = _hosts.pop(host_id, None)
    if session:
        await session.ws.close(code=4001, reason='Helper revoked')
    for device_id in ids:
        conn = connected_devices.get(device_id)
        if conn:
            await conn.ws.close(code=4001, reason='Helper revoked')
    return {'ok': True}


@router.get('/bundle')
async def bundle(request: Request):
    require_account_id(request)
    backend_root = Path(__file__).resolve().parents[1]
    tools = backend_root / 'tools'
    if not tools.is_dir():
        tools = backend_root.parent / 'tools'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['ios_host.py', 'ios_bridge.py', 'ios_usbmux.py', 'device_host.py', 'adb_bridge.py', 'hdc_bridge.py', 'adb-bridge-requirements.txt', 'ios-host-README.md']:
            archive.write(tools / name, name)
    return Response(buf.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="sab-mac-helper.zip"'})


def desktop_directory():
    if os.getenv('SAB_MAC_HELPER_DIR'):
        return Path(os.environ['SAB_MAC_HELPER_DIR'])
    backend_root = Path(__file__).resolve().parents[1]
    tools = backend_root / 'tools'
    return (tools if tools.is_dir() else backend_root.parent / 'tools') / 'macos/dist'


@router.get('/desktop')
async def desktop_info(request: Request):
    require_account_id(request)
    return {'minimum_macos': '14', 'downloads': [
        {'architecture': arch, 'path': '/ios-host/desktop/' + arch}
        for arch in ('arm64', 'x86_64')
        if (desktop_directory() / ('SAB-Device-Helper-macOS-' + arch + '.zip')).is_file()]}


@router.get('/desktop/{architecture}')
async def desktop_download(architecture: Literal['arm64', 'x86_64'], request: Request):
    require_account_id(request)
    path = desktop_directory() / ('SAB-Device-Helper-macOS-' + architecture + '.zip')
    if not path.is_file():
        raise HTTPException(404, '该架构的 Mac 应用安装包尚未发布')
    return FileResponse(path, media_type='application/zip', filename=path.name)


def validate_inventory(data):
    devices = data.get('devices')
    if not isinstance(devices, list) or len(devices) > 100:
        raise ValueError('Invalid inventory')
    result = []
    seen = set()
    for d in devices:
        serial = d.get('serial', '')
        platform = d.get('platform', 'ios')
        if platform not in {'ios', 'android', 'harmony'}:
            raise ValueError('Invalid platform')
        key = device_key(platform, serial) if isinstance(serial, str) else ''
        if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', serial) or key in seen:
            raise ValueError('Invalid serial')
        seen.add(key)
        result.append({'serial': serial, 'platform': platform, 'name': str(d.get('name', '设备'))[:100],
                       'ready': d.get('ready') is True, 'status': str(d.get('status', ''))[:250]})
    return {'devices': result, 'prepared': data.get('prepared') is True, 'forwarding': data.get('forwarding') is True,
            'error': str(data.get('error', ''))[:250]}


async def host_websocket(ws: WebSocket):
    session = None
    host_id = None
    token = ws.headers.get('authorization', '').removeprefix('Bearer ').strip()
    await ws.accept()
    try:
        async with _lock:
            async with AsyncSessionLocal() as db:
                host = (await db.execute(select(IosHost).where(IosHost.token_hash == digest(token)))).scalar_one_or_none() if token else None
            if not host:
                await ws.close(code=4001, reason='Unauthorized')
                return
            host_id = host.id
            if host_id in _hosts:
                await ws.close(code=4009, reason='Helper already connected')
                return
            session = HostSession(ws)
            _hosts[host_id] = session
        await session.send({'type': 'ready'})
        while True:
            payload = await asyncio.wait_for(ws.receive_text(), 45)
            if len(payload) > 100000:
                raise ValueError('Frame too large')
            data = json.loads(payload)
            if data.get('type') == 'inventory':
                session.inventory = validate_inventory(data)
            elif data.get('type') == 'result' and isinstance(data.get('id'), str):
                # A slow device can finish after the HTTP request timed out.
                future = session.pending.get(data['id'])
                if future and not future.done():
                    future.set_result({'ok': data.get('ok') is True, 'error': str(data.get('error', '连接失败'))[:250]})
            else:
                raise ValueError('Unexpected frame')
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, TypeError, AttributeError):
        try:
            await ws.close(code=4000, reason='Helper disconnected or invalid frame')
        except RuntimeError:
            pass
    finally:
        if session and _hosts.get(host_id) is session:
            _hosts.pop(host_id, None)
            for future in session.pending.values():
                if not future.done():
                    future.set_result({'ok': False, 'error': 'Mac 助手已断开，请重试'})
