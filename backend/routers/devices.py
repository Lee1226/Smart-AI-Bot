"""Device management — list connected devices, generate tokens, delete."""
import asyncio
import secrets
import re
from datetime import datetime
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import Device, DeviceApp, AppCatalog, DeviceAppInstallation
from ws.portal_ws import connected_devices
from agent.ws_device import WebSocketDevice
from agent import local_adb
from core.device_control import (
    _screen_dimensions_cache,
    cache_screen_dimensions as _cache_screen_dimensions,
    refresh_local_screen_dimensions as _refresh_local_screen_dimensions,
    execute_manual_device_control,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])

# Screen-dimension cache and refresh helpers are shared with recorder gestures.

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class DeviceOut(BaseModel):
    id: str
    name: str
    token: str
    status: str
    last_seen: str
    connection_type: str = "portal"
    platform: str = "android"
    adb_serial: str | None = None
    adb_auto_connect: bool = True

    def model_post_init(self, __context):
        self.platform = {"hdc_bridge": "harmony", "ios_bridge": "ios"}.get(self.connection_type, "android")

    class Config:
        from_attributes = True


class TokenCreateRequest(BaseModel):
    name: str = "New Device"
    connection_type: Literal["portal", "browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge"] = "portal"


@router.get("", response_model=List[DeviceOut])
async def list_devices(request: Request, db: AsyncSession = Depends(get_db)):
    q = select(Device).order_by(Device.last_seen.desc())
    if request.state.account_id:
        q = q.where(Device.account_id == request.state.account_id)
    result = await db.execute(q)
    devices = result.scalars().all()
    # Rehydrate local ADB bindings after backend restart; never touch remote bridge types.
    adb_rows = (await db.execute(select(Device).where(Device.connection_type == "adb_usb"))).scalars().all()
    await local_adb.refresh_bindings(adb_rows)
    # Remove dimensions for disconnected devices and prefetch for reattached ADB devices.
    for cached_id in list(_screen_dimensions_cache):
        if not local_adb.is_bound(cached_id):
            _screen_dimensions_cache.pop(cached_id, None)
    await asyncio.gather(*[
        _refresh_local_screen_dimensions(d.id)
        for d in adb_rows if local_adb.is_bound(d.id) and d.id not in _screen_dimensions_cache
    ], return_exceptions=True)
    # Sync status from native ADB or existing WebSocket registry.
    out = []
    for d in devices:
        native_online = d.connection_type == "adb_usb" and local_adb.is_bound(d.id)
        status = "online" if native_online or (d.id in connected_devices and connected_devices[d.id].is_connected) else "offline"
        out.append(DeviceOut(
            id=d.id,
            connection_type=d.connection_type,
            adb_serial=d.adb_serial,
            adb_auto_connect=d.adb_auto_connect,
            name=d.name,
            token=d.token,
            status=status,
            last_seen=d.last_seen.isoformat() if d.last_seen else "",
        ))
    return out


@router.post("", response_model=DeviceOut, status_code=201)
async def create_device(req: TokenCreateRequest, request: Request, db: AsyncSession = Depends(get_db)):
    token = secrets.token_urlsafe(32)
    device = Device(name=req.name, connection_type=req.connection_type, token=token, status="offline", account_id=request.state.account_id)
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return DeviceOut(
        id=device.id,
        connection_type=device.connection_type,
        adb_serial=device.adb_serial,
        adb_auto_connect=device.adb_auto_connect,
        name=device.name,
        token=device.token,
        status="offline",
        last_seen=device.last_seen.isoformat() if device.last_seen else "",
    )


class LocalAdbAttachRequest(BaseModel):
    serial: str
    name: str = ""


@router.get("/local-adb/discover")
async def discover_local_adb(request: Request):
    """Discover Android phones attached to the backend host over USB ADB."""
    if request.state.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可管理服务端本机 USB 设备")
    try:
        return {"available": bool(local_adb.adb_path()), "devices": await local_adb.discover_devices()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ADB 设备发现失败：{exc}")


@router.post("/local-adb/attach", response_model=DeviceOut, status_code=201)
async def attach_local_adb(req: LocalAdbAttachRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Bind a discovered physical USB serial to a stable Smart-AI-Bot device UUID."""
    if request.state.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可连接服务端本机 USB 设备")
    serial = req.serial.strip()
    if not serial:
        raise HTTPException(status_code=400, detail="ADB serial 不能为空")
    found = {item["serial"]: item for item in await local_adb.discover_devices()}
    item = found.get(serial)
    if not item:
        raise HTTPException(status_code=409, detail="未发现该 ADB 设备，请检查 USB 调试授权和连接状态")
    existing = (await db.execute(select(Device).where(Device.adb_serial == serial))).scalars().first()
    if existing:
        if existing.account_id != request.state.account_id:
            raise HTTPException(status_code=409, detail="该 USB 设备已被其他账号绑定")
        if existing.connection_type != "adb_usb":
            raise HTTPException(status_code=409, detail="该设备序列号已被其他连接方式占用")
        existing.name = req.name.strip() or item.get("model") or existing.name
        existing.adb_auto_connect = True
        local_adb.bind_device(existing.id, serial)
        _screen_dimensions_cache.pop(existing.id, None)
        await _refresh_local_screen_dimensions(existing.id)
        await db.commit()
        await db.refresh(existing)
        return DeviceOut(
            id=existing.id, connection_type=existing.connection_type, adb_serial=serial,
            adb_auto_connect=True, name=existing.name, token=existing.token, status="online",
            last_seen=existing.last_seen.isoformat() if existing.last_seen else "",
        )
    device = Device(
        name=req.name.strip() or item.get("model") or f"Android {serial[-6:]}",
        connection_type="adb_usb", adb_serial=serial, adb_auto_connect=True,
        token=secrets.token_urlsafe(32), status="online",
        account_id=request.state.account_id,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    local_adb.bind_device(device.id, serial)
    await _refresh_local_screen_dimensions(device.id)
    return DeviceOut(
        id=device.id, connection_type=device.connection_type, adb_serial=serial,
        adb_auto_connect=True, name=device.name, token=device.token, status="online",
        last_seen=device.last_seen.isoformat() if device.last_seen else "",
    )


@router.post("/{device_id}/local-adb/detach", status_code=204)
async def detach_local_adb(device_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    if request.state.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可断开服务端本机 USB 设备")
    query = select(Device).where(Device.id == device_id, Device.connection_type == "adb_usb")
    if request.state.account_id:
        query = query.where(Device.account_id == request.state.account_id)
    device = (await db.execute(query)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="本地 ADB 设备不存在")
    from core.device_scheduler import locks
    if locks.get(device_id) and locks[device_id].locked():
        raise HTTPException(status_code=409, detail="设备正在执行任务，请等待完成后再断开")
    # Close the active Scrcpy preview before releasing the ADB binding.
    from routers.live import stop_local_adb_stream
    await stop_local_adb_stream(device_id)
    local_adb.unbind_device(device_id)
    _screen_dimensions_cache.pop(device_id, None)
    device.adb_auto_connect = False
    device.status = "offline"
    await db.commit()


class DeviceRenameRequest(BaseModel):
    name: str


@router.patch("/{device_id}", response_model=DeviceOut)
async def rename_device(device_id: str, req: DeviceRenameRequest, db: AsyncSession = Depends(get_db)):
    """Rename a device. The name is backend-owned and won't be overwritten by the
    Portal's X-Device-Name header (which can't carry non-ASCII reliably)."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device.name = name
    await db.commit()
    await db.refresh(device)
    status = "online" if (device.connection_type == "adb_usb" and local_adb.is_bound(device.id)) or (device.id in connected_devices and connected_devices[device.id].is_connected) else "offline"
    return DeviceOut(
        id=device.id,
        connection_type=device.connection_type,
        adb_serial=device.adb_serial,
        adb_auto_connect=device.adb_auto_connect,
        name=device.name,
        token=device.token,
        status=status,
        last_seen=device.last_seen.isoformat() if device.last_seen else "",
    )


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    conn = connected_devices.get(device_id)
    if conn:
        await conn.ws.close(code=4000, reason="Device deleted")
    local_adb.unbind_device(device_id)
    await db.delete(device)
    await db.commit()


class InstallRequest(BaseModel):
    apk_url: str


class DeviceAppUpdateRequest(BaseModel):
    app_name: str = ""
    is_test_app: bool = False


@router.get("/apps/catalog")
async def list_app_catalog(request: Request, device_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """Return account app catalog with optional per-device installation state."""
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    if device_id:
        device = (await db.execute(select(Device).where(
            Device.id == device_id, Device.account_id == account_id, Device.connection_type == "adb_usb"
        ))).scalar_one_or_none()
        if not device:
            raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    catalog = (await db.execute(select(AppCatalog).where(
        AppCatalog.account_id == account_id
    ).order_by(AppCatalog.is_test_app.desc(), AppCatalog.app_name, AppCatalog.package_name))).scalars().all()
    installs = []
    if device_id:
        installs = (await db.execute(select(DeviceAppInstallation).where(
            DeviceAppInstallation.account_id == account_id, DeviceAppInstallation.device_id == device_id
        ))).scalars().all()
    by_package = {x.package_name: x for x in installs}
    return {"apps": [{
        "id": row.id, "package_name": row.package_name, "app_name": row.app_name,
        "is_test_app": row.is_test_app,
        "installed": bool(by_package.get(row.package_name) and by_package[row.package_name].installed),
        "first_seen_at": by_package[row.package_name].first_seen_at.isoformat() if row.package_name in by_package and by_package[row.package_name].first_seen_at else "",
        "last_seen_at": by_package[row.package_name].last_seen_at.isoformat() if row.package_name in by_package and by_package[row.package_name].last_seen_at else "",
    } for row in catalog]}


@router.get("/{device_id}/apps")
async def list_device_apps(device_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Discover launcher apps and reconcile per-device installs with the shared catalog."""
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    device = (await db.execute(select(Device).where(
        Device.id == device_id, Device.connection_type == "adb_usb", Device.account_id == account_id
    ))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    if not local_adb.is_bound(device_id):
        raise HTTPException(status_code=409, detail="本地 ADB 设备未连接")
    from core.device_scheduler import locks
    lock = locks.setdefault(device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行任务，请稍后刷新应用列表")
    try:
        async with lock:
            packages = await local_adb.rpc(device_id, "packages", timeout=35)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取应用包名失败：{exc}") from exc

    now = datetime.utcnow()
    package_metadata = {
        str(x.get("packageName", "")).strip(): x
        for x in (packages or [])
        if isinstance(x, dict) and str(x.get("packageName", "")).strip()
    }
    current = set(package_metadata)
    legacy_rows = (await db.execute(select(DeviceApp).where(
        DeviceApp.account_id == account_id, DeviceApp.device_id == device_id
    ))).scalars().all()
    legacy_by_pkg = {x.package_name: x for x in legacy_rows}
    install_rows = (await db.execute(select(DeviceAppInstallation).where(
        DeviceAppInstallation.account_id == account_id, DeviceAppInstallation.device_id == device_id
    ))).scalars().all()
    install_by_pkg = {x.package_name: x for x in install_rows}
    for package_name in current:
        legacy = legacy_by_pkg.get(package_name)
        catalog = (await db.execute(select(AppCatalog).where(
            AppCatalog.account_id == account_id, AppCatalog.package_name == package_name
        ))).scalar_one_or_none()
        if not catalog:
            discovered_name = str(package_metadata.get(package_name, {}).get("appName", "")).strip()
            catalog = AppCatalog(account_id=account_id, package_name=package_name,
                app_name=(legacy.app_name if legacy and legacy.app_name else discovered_name),
                is_test_app=bool(legacy and legacy.is_test_app))
            db.add(catalog)
            await db.flush()
        else:
            discovered_name = str(package_metadata.get(package_name, {}).get("appName", "")).strip()
            if not catalog.app_name and discovered_name:
                catalog.app_name = discovered_name
            if legacy:
                if not catalog.app_name and legacy.app_name:
                    catalog.app_name = legacy.app_name
                catalog.is_test_app = bool(catalog.is_test_app or legacy.is_test_app)
        install = install_by_pkg.get(package_name)
        if install:
            install.installed, install.last_seen_at = True, now
        else:
            db.add(DeviceAppInstallation(account_id=account_id, device_id=device_id,
                package_name=package_name, installed=True, first_seen_at=now, last_seen_at=now))
        if legacy:
            legacy.installed, legacy.last_seen_at = True, now
        else:
            db.add(DeviceApp(account_id=account_id, device_id=device_id, package_name=package_name,
                app_name=catalog.app_name, is_test_app=catalog.is_test_app,
                installed=True, first_seen_at=now, last_seen_at=now))
    for pkg, install in install_by_pkg.items():
        if pkg not in current:
            install.installed = False
    for pkg, legacy in legacy_by_pkg.items():
        if pkg not in current:
            legacy.installed = False
    await db.commit()

    catalog_rows = (await db.execute(select(AppCatalog).where(
        AppCatalog.account_id == account_id
    ).order_by(AppCatalog.is_test_app.desc(), AppCatalog.app_name, AppCatalog.package_name))).scalars().all()
    installs = (await db.execute(select(DeviceAppInstallation).where(
        DeviceAppInstallation.account_id == account_id, DeviceAppInstallation.device_id == device_id
    ))).scalars().all()
    by_package = {x.package_name: x for x in installs}
    return {"device_id": device_id, "apps": [{
        "id": row.id, "package_name": row.package_name, "app_name": row.app_name,
        "is_test_app": row.is_test_app,
        "installed": bool(by_package.get(row.package_name) and by_package[row.package_name].installed),
        "first_seen_at": by_package[row.package_name].first_seen_at.isoformat() if row.package_name in by_package and by_package[row.package_name].first_seen_at else "",
        "last_seen_at": by_package[row.package_name].last_seen_at.isoformat() if row.package_name in by_package and by_package[row.package_name].last_seen_at else "",
    } for row in catalog_rows if row.package_name in by_package]}


@router.put("/{device_id}/apps/{package_name}")
async def update_device_app(device_id: str, package_name: str, req: DeviceAppUpdateRequest,
                            request: Request, db: AsyncSession = Depends(get_db)):
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    if not re.fullmatch(r"[A-Za-z0-9_.]+", package_name):
        raise HTTPException(status_code=400, detail="Package 名称格式无效")
    device = (await db.execute(select(Device).where(
        Device.id == device_id, Device.account_id == account_id, Device.connection_type == "adb_usb"
    ))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    row = (await db.execute(select(AppCatalog).where(
        AppCatalog.account_id == account_id, AppCatalog.package_name == package_name
    ))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="应用记录不存在，请先刷新设备应用列表")
    row.app_name, row.is_test_app, row.updated_at = req.app_name.strip()[:80], bool(req.is_test_app), datetime.utcnow()
    legacy_rows = (await db.execute(select(DeviceApp).where(
        DeviceApp.account_id == account_id, DeviceApp.package_name == package_name
    ))).scalars().all()
    for legacy in legacy_rows:
        legacy.app_name, legacy.is_test_app = row.app_name, row.is_test_app
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "package_name": row.package_name, "app_name": row.app_name, "is_test_app": row.is_test_app}


@router.post("/{device_id}/apps/{package_name}/launch")
async def launch_device_app(device_id: str, package_name: str, request: Request,
                            db: AsyncSession = Depends(get_db)):
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    if not re.fullmatch(r"[A-Za-z0-9_.]+", package_name):
        raise HTTPException(status_code=400, detail="Package 名称格式无效")
    device = (await db.execute(select(Device).where(
        Device.id == device_id, Device.account_id == account_id, Device.connection_type == "adb_usb"
    ))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    install = (await db.execute(select(DeviceAppInstallation).where(
        DeviceAppInstallation.account_id == account_id, DeviceAppInstallation.device_id == device_id,
        DeviceAppInstallation.package_name == package_name, DeviceAppInstallation.installed.is_(True)
    ))).scalar_one_or_none()
    # Launching an installed app from the device app library must not depend
    # on whether the user has marked it as a test app. is_test_app only controls
    # test-case/app selection; installation state is the launch prerequisite.
    if not install:
        raise HTTPException(status_code=404, detail="应用未在当前设备安装")
    if not local_adb.is_bound(device_id):
        raise HTTPException(status_code=409, detail="本地 ADB 设备未连接")
    from core.device_scheduler import locks
    lock = locks.setdefault(device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行测试任务，请完成后再启动应用")
    try:
        async with lock:
            result = await local_adb.rpc(device_id, "app", {"package": package_name}, timeout=35)
        if isinstance(result, str) and ("no activities found" in result.lower() or "monkey aborted" in result.lower() or "error:" in result.lower()):
            raise RuntimeError(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"启动应用失败：{exc}") from exc
    return {"ok": True, "device_id": device_id, "package_name": package_name, "result": result}


@router.post("/{device_id}/install")
async def install_apk(device_id: str, req: InstallRequest, db: AsyncSession = Depends(get_db)):
    """Download the APK at *apk_url* and launch the system installer on the device.

    The device must be connected (Portal WebSocket). Returns the installer result.
    """
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if not WebSocketDevice(device_id).is_connected():
        raise HTTPException(status_code=400, detail=f"Device {device_id} is not connected")

    from core.device_scheduler import locks
    lock = locks.setdefault(device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行任务，请等待完成再安装")
    try:
        async with lock:
            result = await WebSocketDevice(device_id).install_apk(req.apk_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Install failed: {e}")

    return {"ok": True, "device_id": device_id, "result": result}


class DeviceControlRequest(BaseModel):
    action: Literal["tap", "swipe", "back", "home", "recent", "power"]
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    # Dimensions of the decoded video frame. The backend converts these
    # coordinates to the phone's native screen resolution before input.
    source_width: int | None = None
    source_height: int | None = None
    duration_ms: int = 450


@router.post("/{device_id}/control")
async def control_device(device_id: str, req: DeviceControlRequest, request: Request,
                         db: AsyncSession = Depends(get_db)):
    """Perform a deliberate manual interaction from the debug live preview."""
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="未登录")
    device = await db.get(Device, device_id)
    if not device or (account_id and device.account_id != account_id):
        raise HTTPException(status_code=404, detail="设备不存在或无权访问")

    transport = WebSocketDevice(device_id)
    if not transport.is_connected():
        raise HTTPException(status_code=409, detail="设备当前离线")

    # Share the exact scheduler lock used by AI runs so manual control cannot
    # race an active task. Import locally to avoid router import cycles.
    from core.device_scheduler import locks
    lock = locks.setdefault(device_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="设备正在执行 AI 调试或测试任务，请先停止任务再手动操作")

    try:
        async with lock:
            if req.action in ("tap", "swipe"):
                required = (req.x, req.y) if req.action == "tap" else (req.x, req.y, req.x2, req.y2)
                if any(value is None for value in required):
                    raise HTTPException(status_code=422, detail="触控坐标缺失")
            await execute_manual_device_control(
                device_id, transport, req.action,
                x=req.x, y=req.y, x2=req.x2, y2=req.y2,
                source_width=req.source_width, source_height=req.source_height,
                duration_ms=req.duration_ms,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"设备操作失败：{exc}") from exc

    return {"ok": True, "device_id": device_id, "action": req.action}
