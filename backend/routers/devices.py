"""Device management — list connected devices, generate tokens, delete."""
import asyncio
import secrets
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import Device
from ws.portal_ws import connected_devices
from agent.ws_device import WebSocketDevice
from agent import local_adb

router = APIRouter(prefix="/api/devices", tags=["devices"])


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
    connection_type: Literal["portal", "browser_usb", "adb_bridge", "hdc_bridge", "ios_bridge", "adb_usb"] = "portal"


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
    local_adb.unbind_device(device_id)
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
