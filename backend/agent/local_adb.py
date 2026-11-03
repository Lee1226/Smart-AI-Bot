"""Direct host-side ADB transport for Android phones attached to the backend PC.

This transport intentionally does not start/stop the shared ADB server and does
not require a browser WebUSB session or a phone-side helper application.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

_ATTACHED: dict[str, str] = {}  # database device UUID -> physical ADB serial
_SERIAL_LOCKS: dict[str, asyncio.Lock] = {}


def adb_path() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe"),
        os.path.expandvars(r"%ANDROID_HOME%\\platform-tools\\adb.exe"),
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    ]
    return next((p for p in candidates if p and os.path.isfile(p)), None)


async def _exec(*args: str, timeout: float = 20, binary: bool = False) -> bytes | str:
    adb = adb_path()
    if not adb:
        raise RuntimeError("未找到 adb，请先安装 Android SDK Platform-Tools 并配置 PATH")
    proc = await asyncio.create_subprocess_exec(
        adb, *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"ADB 命令超时（{timeout:g}s）：{' '.join(args[:3])}")
    if proc.returncode:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"ADB 命令失败：{' '.join(args[:3])}")
    return stdout if binary else stdout.decode("utf-8", errors="replace").strip()


async def discover_devices() -> list[dict[str, str]]:
    if not adb_path():
        return []
    output = await _exec("devices", "-l", timeout=8)
    devices = []
    for line in str(output).splitlines()[1:]:
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[0], fields[1]
        if state != "device":
            continue
        details = {}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                details[key] = value
        model = details.get("model", "").replace("_", " ")
        product = details.get("product", "")
        devices.append({"serial": serial, "model": model, "product": product, "state": state})
    return devices


def bind_device(device_id: str, serial: str) -> None:
    _ATTACHED[device_id] = serial


def unbind_device(device_id: str) -> None:
    _ATTACHED.pop(device_id, None)


def serial_for(device_id: str) -> str | None:
    return _ATTACHED.get(device_id)


def is_bound(device_id: str) -> bool:
    return device_id in _ATTACHED


async def refresh_bindings(rows: list[Any]) -> set[str]:
    """Refresh process-local mappings from persisted adb_usb rows and live ADB."""
    available = {item["serial"] for item in await discover_devices()}
    _ATTACHED.clear()
    for row in rows:
        serial = getattr(row, "adb_serial", None)
        if getattr(row, "connection_type", "") == "adb_usb" and serial in available:
            _ATTACHED[row.id] = serial
    return set(_ATTACHED)


async def _shell(serial: str, *args: str, timeout: float = 20) -> str:
    return str(await _exec("-s", serial, "shell", *args, timeout=timeout))


def _node(element: ET.Element) -> dict[str, Any]:
    bounds = element.attrib.get("bounds", "")
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    box = {
        "left": int(match.group(1)), "top": int(match.group(2)),
        "right": int(match.group(3)), "bottom": int(match.group(4)),
    } if match else {"left": 0, "top": 0, "right": 0, "bottom": 0}
    children = [_node(child) for child in list(element)]
    return {
        "text": element.attrib.get("text", ""),
        "contentDescription": element.attrib.get("content-desc", ""),
        "className": element.attrib.get("class", ""),
        "resourceId": element.attrib.get("resource-id", ""),
        "isEnabled": element.attrib.get("enabled", "true") == "true",
        "isVisibleToUser": element.attrib.get("visible-to-user", "true") == "true",
        "isClickable": element.attrib.get("clickable", "false") == "true",
        "isFocusable": element.attrib.get("focusable", "false") == "true",
        "isFocused": element.attrib.get("focused", "false") == "true",
        "isCheckable": element.attrib.get("checkable", "false") == "true",
        "isChecked": element.attrib.get("checked", "false") == "true",
        "isSelected": element.attrib.get("selected", "false") == "true",
        "isScrollable": element.attrib.get("scrollable", "false") == "true",
        "boundsInScreen": box,
        "children": children,
    }


async def _ui_state(serial: str) -> dict[str, Any]:
    # uiautomator writes to device storage; stdout is fetched separately to
    # avoid OEM-dependent shell redirection/encoding behavior.
    await _shell(serial, "uiautomator", "dump", "/sdcard/window.xml", timeout=25)
    xml_text = await _exec("-s", serial, "exec-out", "cat", "/sdcard/window.xml", timeout=10)
    try:
        root = ET.fromstring(str(xml_text))
    except ET.ParseError as exc:
        raise RuntimeError(f"无法解析 Android UIAutomator XML：{exc}") from exc
    tree = _node(root)
    width = max((n["boundsInScreen"]["right"] for n in _walk(tree)), default=0)
    height = max((n["boundsInScreen"]["bottom"] for n in _walk(tree)), default=0)
    focused = next((n for n in _walk(tree) if n.get("isFocused")), None)
    package = ""
    activity = ""
    try:
        dump = await _shell(serial, "dumpsys", "activity", "activities", timeout=8)
        m = re.search(r"(?:mResumedActivity|topResumedActivity)=.*? ([\w.]+)/([\w.$]+)", dump)
        if m:
            package, activity = m.group(1), m.group(2)
    except Exception:
        pass
    return {
        "device_context": {"screen_bounds": {"width": width, "height": height}},
        "phone_state": {
            "currentApp": package, "packageName": package, "activityName": activity,
            "isEditable": bool(focused and "EditText" in focused.get("className", "")),
            "focusedElement": {"text": focused.get("text", "")} if focused else None,
        },
        "a11y_tree": tree,
    }


def _walk(node: dict[str, Any]):
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


async def rpc(device_id: str, method: str, params: dict[str, Any] | None = None,
              timeout: float = 30) -> Any:
    serial = serial_for(device_id)
    if not serial:
        raise RuntimeError(f"本地 ADB 设备 {device_id} 未绑定；请在设备管理页重新连接")
    params = params or {}
    lock = _SERIAL_LOCKS.setdefault(serial, asyncio.Lock())
    async with lock:
        if method == "screenshot":
            data = await _exec("-s", serial, "exec-out", "screencap", "-p", timeout=timeout, binary=True)
            return base64.b64encode(data).decode("ascii")
        if method == "state":
            return await _ui_state(serial)
        if method == "tap":
            await _shell(serial, "input", "tap", str(int(params["x"])), str(int(params["y"])))
            return "OK"
        if method == "swipe":
            await _shell(serial, "input", "swipe", str(int(params["startX"])), str(int(params["startY"])),
                         str(int(params["endX"])), str(int(params["endY"])), str(int(params.get("duration", 500))))
            return "OK"
        if method == "keyboard/key":
            await _shell(serial, "input", "keyevent", str(int(params["key_code"])))
            return "OK"
        if method == "keyboard/input":
            if params.get("clear"):
                await _shell(serial, "input", "keyevent", "KEYCODE_MOVE_END")
                await _shell(serial, "input", "keyevent", "--longpress", "KEYCODE_DEL")
            text = base64.b64decode(params.get("base64_text", "")).decode("utf-8")
            if any(ord(ch) > 127 for ch in text):
                raise RuntimeError("当前 ADB 原生输入不支持中文/Unicode；请使用支持 Unicode 的输入法桥接方式")
            escaped = text.replace("%", "%25").replace(" ", "%s").replace("&", "\\&").replace(";", "\\;").replace("(", "\\(").replace(")", "\\)")
            await _shell(serial, "input", "text", escaped, timeout=15)
            return "OK"
        if method == "globalAction":
            action = int(params.get("action", -1))
            key = {1: "KEYCODE_HOME", 2: "KEYCODE_BACK", 3: "KEYCODE_APP_SWITCH", 4: "KEYCODE_NOTIFICATION"}.get(action)
            if not key:
                raise ValueError(f"不支持的 Android globalAction：{action}")
            await _shell(serial, "input", "keyevent", key)
            return "OK"
        if method == "app":
            package = str(params.get("package", "")).strip()
            if not package:
                raise ValueError("package 不能为空")
            activity = str(params.get("activity", "")).strip()
            if activity:
                await _shell(serial, "am", "start", "-n", f"{package}/{activity}", timeout=30)
            else:
                await _shell(serial, "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=30)
            return f"Launched {package}"
        if method == "app/stop":
            package = str(params.get("package", "")).strip()
            await _shell(serial, "am", "force-stop", package)
            return f"Force-stopped {package}"
        if method == "packages":
            output = await _shell(serial, "pm", "list", "packages", "-3", timeout=30)
            return [{"packageName": line.split(":", 1)[-1]} for line in output.splitlines() if line.startswith("package:")]
        if method == "install":
            raise RuntimeError("本地 ADB 模式暂不支持通过 URL 安装 APK；请使用 adb install 或 Portal App 配对方式")
        if method == "video/start" or method == "video/stop":
            raise RuntimeError("本地 ADB 实时视频请使用后端 screenrecord H.264 通道")
        raise NotImplementedError(f"本地 ADB 尚未实现 RPC：{method}")
