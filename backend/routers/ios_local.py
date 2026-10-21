"""Opt-in, loopback-only Mac connector for already signed WDA runners."""
import asyncio
import json
import os
import platform
import re
import signal
import secrets
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from db.database import DB_PATH
from db.models import Device
from routers.devices import get_db
from ws.portal_ws import connected_devices

router = APIRouter(prefix='/api/ios-local', tags=['ios-local'])
_jobs = {}
_lock = asyncio.Lock()
_root = Path(__file__).resolve().parents[2]


def guard(request):
    if (os.getenv('SAB_IOS_LOCAL_ENABLED') != '1' or platform.system() != 'Darwin'
            or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}
            or not request.client or request.client.host not in {'127.0.0.1', '::1'}):
        raise HTTPException(403, '一键连接需要在连接 iPad 的 Mac 上启用本机助手；远程部署请使用手动桥接。')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '只允许本机网页连接')
    if not request.state.account_id:
        raise HTTPException(401, '请先登录')


async def discover():
    with tempfile.TemporaryDirectory(prefix='sab-ios-') as folder:
        output = Path(folder) / 'devices.json'
        p = await asyncio.create_subprocess_exec('xcrun', 'devicectl', 'list', 'devices', '--json-output', str(output),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(p.wait(), 20)
            if p.returncode or not output.exists():
                raise HTTPException(503, '无法读取设备，请检查 Xcode 命令行工具。')
            result = json.loads(output.read_text())['result']['devices']
        finally:
            if p.returncode is None:
                p.kill(); await p.wait()
    devices = []
    for d in result:
        h, c, props = d.get('hardwareProperties', {}), d.get('connectionProperties', {}), d.get('deviceProperties', {})
        if h.get('platform') != 'iOS' or c.get('transportType') != 'wired' or c.get('tunnelState') != 'connected':
            continue
        devices.append(dict(serial=h['udid'], name=h.get('marketingName', props.get('name', 'iOS')),
                            ready=props.get('developerModeStatus') == 'enabled' and c.get('pairingState') == 'paired'))
    return devices


@router.get('')
async def status(request: Request):
    guard(request)
    devices = await discover()
    prepared = bool(list(Path(os.getenv('SAB_IOS_WDA_DERIVED_DATA', '')).glob('Build/Products/*.xctestrun'))) if os.getenv('SAB_IOS_WDA_DERIVED_DATA') else False
    for d in devices:
        job = _jobs.get((request.state.account_id, d['serial']))
        d['status'] = '未连接'
        if job:
            conn = connected_devices.get(job['device_id'])
            d['status'] = '已连接' if conn and conn.is_connected else '连接中'
            if job['bridge'].returncode is not None:
                d['status'] = '连接中断，请重试'
            log = job['wda_log'].read_text(errors='replace')[-12000:]
            if d['status'] == '连接中' and time.monotonic() - job['started'] > 90:
                d['status'] = '连接超时，请保持设备解锁后重试'
            if job['wda'].returncode is not None and d['status'] != '已连接':
                d['status'] = 'WDA 启动失败，请在 Xcode 重新签名并运行'
                if 'Timed out while enabling automation mode' in log:
                    d['status'] = '自动化启动超时，请解锁 iPad 后重试'
                if 'not trusted' in log or 'explicitly trusted' in log:
                    d['status'] = '请在 iPad「VPN 与设备管理」信任开发者 App'
            d['device_id'] = job['device_id']
    return dict(devices=devices, prepared=prepared, forwarding=bool(shutil.which('iproxy') or os.getenv('SAB_IOS_PYMOBILEDEVICE3')))


class ConnectRequest(BaseModel):
    serial: str
    device_id: Optional[str] = None


async def stop(job):
    for key in ('bridge', 'wda'):
        proc = job[key]
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT if key == 'bridge' else signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 8)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()


@router.post('/connect')
async def connect(req: ConnectRequest, request: Request, db=Depends(get_db)):
    guard(request)
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', req.serial):
        raise HTTPException(400, '无效设备标识')
    async with _lock:
        found = next((d for d in await discover() if d['serial'] == req.serial), None)
        if not found or not found['ready']:
            raise HTTPException(409, '请连接并解锁设备，信任电脑并开启开发者模式。')
        derived = os.getenv('SAB_IOS_WDA_DERIVED_DATA', '')
        builds = sorted(Path(derived).glob('Build/Products/*.xctestrun')) if derived else []
        proxy = os.getenv('SAB_IOS_PYMOBILEDEVICE3')
        if not builds or not (shutil.which('iproxy') or proxy):
            raise HTTPException(409, '首次使用请先完成 WDA 签名编译及助手配置。')
        key = (request.state.account_id, req.serial)
        old = _jobs.get(key)
        if any(k[1] == req.serial and k[0] != key[0] and j['bridge'].returncode is None for k, j in _jobs.items()):
            raise HTTPException(409, '设备已由其他账号连接')
        if old:
            conn = connected_devices.get(old['device_id'])
            if conn and conn.is_connected:
                return {'device_id': old['device_id']}
            await stop(old)
        folder = DB_PATH.parent / 'ios-local'
        folder.mkdir(mode=0o700, exist_ok=True)
        mapping_path = folder / 'devices.json'
        mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        mapping_key = key[0] + ':' + req.serial
        device_id = mapping.get(mapping_key) or req.device_id
        device = await db.get(Device, device_id) if device_id else None
        if device and (device.account_id != key[0] or device.connection_type != 'ios_bridge'):
            raise HTTPException(403, '设备不属于当前账号')
        if not device:
            device = Device(name=found['name'] + ' · USB', connection_type='ios_bridge', token=secrets.token_urlsafe(32), account_id=key[0], status='offline')
            db.add(device); await db.commit(); await db.refresh(device)
        mapping[mapping_key] = device.id
        mapping_path.write_text(json.dumps(mapping)); mapping_path.chmod(0o600)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        config = folder / (device.id + '.json')
        config.write_text(json.dumps(dict(server=os.getenv('SAB_IOS_LOCAL_SERVER', 'http://127.0.0.1:8000'), devices=[dict(serial=req.serial, token=device.token, port=port)])))
        config.chmod(0o600)
        wda_log = folder / (device.id + '-wda.log')
        with wda_log.open('w') as log:
            wda = await asyncio.create_subprocess_exec('xcodebuild', '-xctestrun', str(builds[-1]), '-destination', 'id=' + req.serial, 'test-without-building', stdout=log, stderr=log)
        args = [sys.executable, str(_root / 'tools/ios_bridge.py'), '--config', str(config)]
        if proxy:
            args += ['--pymobiledevice3', proxy]
        try:
            bridge = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except Exception:
            wda.terminate(); await wda.wait()
            raise HTTPException(503, '桥接启动失败，请检查助手运行环境')
        _jobs[key] = dict(wda=wda, bridge=bridge, device_id=device.id, wda_log=wda_log, started=time.monotonic())
        return {'device_id': device.id}


async def shutdown():
    await asyncio.gather(*(stop(job) for job in _jobs.values()))
    _jobs.clear()
