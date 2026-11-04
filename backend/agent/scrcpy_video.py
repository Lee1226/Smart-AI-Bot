"""Scrcpy-backed H.264 video capture for directly attached ADB USB devices.

This module is deliberately independent from Portal/bridge video sessions.
It launches one temporary scrcpy-server process per USB device preview and
exposes its raw Annex-B H.264 stream to the existing WebSocket route.
"""
from __future__ import annotations

import asyncio
import os
import random
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable

SCRCPY_VERSION = "4.0"
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
DEFAULT_SERVER_PATH = Path(__file__).resolve().parents[1] / "assets" / f"scrcpy-server-v{SCRCPY_VERSION}.jar"


def server_path() -> Path | None:
    configured = os.environ.get("SCRCPY_SERVER_PATH", "").strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_SERVER_PATH
    return candidate if candidate.is_file() else None


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_adb(adb: str, serial: str, *args: str, timeout: float = 20) -> str:
    result = subprocess.run(
        [adb, "-s", serial, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        creationflags=_creationflags(),
    )
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"ADB command failed: {' '.join(args[:3])}")
    return result.stdout.decode("utf-8", errors="replace").strip()


class ScrcpyVideoSession:
    """Own the adb forward, temporary server process and H.264 socket."""

    def __init__(self, adb: str, serial: str, max_size: int = 1280,
                 bit_rate: int = 8_000_000, max_fps: int = 30):
        self.adb = adb
        self.serial = serial
        self.max_size = max_size
        self.bit_rate = bit_rate
        self.max_fps = max_fps
        self.scid = f"{random.getrandbits(31):08x}"
        self.port = self._free_port()
        self.socket: socket.socket | None = None
        self.server_process: subprocess.Popen | None = None
        self.forward_created = False
        self.started = False

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _start(self) -> None:
        jar = server_path()
        if jar is None:
            raise RuntimeError(
                "未找到 Scrcpy Server。请下载与配置版本一致的 "
                f"scrcpy-server-v{SCRCPY_VERSION}.jar，并放到 backend/assets/ "
                "或设置环境变量 SCRCPY_SERVER_PATH"
            )

        _run_adb(self.adb, self.serial, "push", str(jar), DEVICE_SERVER_PATH, timeout=30)
        socket_name = f"scrcpy_{self.scid}"
        _run_adb(
            self.adb, self.serial, "forward",
            f"tcp:{self.port}", f"localabstract:{socket_name}", timeout=10,
        )
        self.forward_created = True

        command = [
            self.adb, "-s", self.serial, "shell",
            f"CLASSPATH={DEVICE_SERVER_PATH}", "app_process", "/",
            "com.genymobile.scrcpy.Server", SCRCPY_VERSION,
            f"scid={self.scid}",
            "log_level=info",
            "video=true", "audio=false", "control=false",
            "video_codec=h264",
            f"video_bit_rate={self.bit_rate}",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            "tunnel_forward=true",
            # The frontend already parses Annex-B NAL units. Raw mode removes
            # scrcpy's device-name, codec, session and per-packet headers.
            "raw_stream=true",
        ]
        self.server_process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creationflags(),
        )

        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.server_process.poll() is not None:
                raise RuntimeError(
                    f"Scrcpy Server 启动失败，ADB shell 进程退出码：{self.server_process.returncode}"
                )
            try:
                conn = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                conn.settimeout(1.0)
                self.socket = conn
                self.started = True
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(f"等待 Scrcpy H.264 通道超时：{last_error}")

    async def start(self) -> None:
        await asyncio.to_thread(self._start)

    def _recv(self, size: int) -> bytes:
        if self.socket is None:
            return b""
        return self.socket.recv(size)

    async def read(self, size: int = 64 * 1024) -> bytes:
        return await asyncio.to_thread(self._recv, size)

    def _stop(self) -> None:
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        if self.server_process is not None and self.server_process.poll() is None:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
                self.server_process.wait(timeout=2)
        self.server_process = None

        if self.forward_created:
            try:
                subprocess.run(
                    [self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False, creationflags=_creationflags(),
                )
            except Exception:
                pass
            self.forward_created = False
        self.started = False

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop)
