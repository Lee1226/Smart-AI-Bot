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
    adb_rows = [d for d in devices if d.connection_type == "adb_usb"]
    if adb_rows:
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
        name=device.name,
        token=device.token,
        status="offline",
        last_seen=device.last_seen.isoformat() if device.last_seen else "",
    )


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
