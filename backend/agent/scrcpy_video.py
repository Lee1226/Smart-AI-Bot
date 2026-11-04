"""Scrcpy-backed H.264 video capture for directly attached ADB USB devices.

This module is deliberately independent from Portal/bridge video sessions.
It launches one temporary scrcpy-server process per USB device preview and
exposes its raw Annex-B H.264 stream to the existing WebSocket route.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

SCRCPY_VERSION = "4.0"
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
SERVER_CONNECT_TIMEOUT = 15.0
FIRST_VIDEO_DATA_TIMEOUT = 20.0
FIRST_VIDEO_READ_SIZE = 64 * 1024
DEFAULT_SERVER_PATH = Path(__file__).resolve().parents[1] / "assets" / f"scrcpy-server-v{SCRCPY_VERSION}"


def server_path() -> Path | None:
    configured = os.environ.get("SCRCPY_SERVER_PATH", "").strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_SERVER_PATH
    return candidate if candidate.is_file() else None


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _video_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer video setting from the environment."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %d", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning(
            "Ignoring out-of-range %s=%d (allowed %d..%d); using %d",
            name, value, minimum, maximum, default,
        )
        return default
    return value


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

    def __init__(self, adb: str, serial: str, max_size: int | None = None,
                 bit_rate: int | None = None, max_fps: int | None = None):
        self.adb = adb
        self.serial = serial
        # Defaults preserve the previous 1280 / 8 Mbps / 30 FPS behavior.
        # Operators can tune each device session without changing source code.
        self.max_size = max_size if max_size is not None else _video_setting(
            "SCRCPY_MAX_SIZE", 1280, 480, 2560
        )
        self.bit_rate = bit_rate if bit_rate is not None else _video_setting(
            "SCRCPY_BIT_RATE", 8_000_000, 500_000, 40_000_000
        )
        self.max_fps = max_fps if max_fps is not None else _video_setting(
            "SCRCPY_MAX_FPS", 30, 5, 60
        )
        self.scid = f"{random.getrandbits(31):08x}"
        self.port = self._free_port()
        self.socket: socket.socket | None = None
        self.server_process: subprocess.Popen | None = None
        self.server_log_thread: threading.Thread | None = None
        self.server_logs: deque[str] = deque(maxlen=80)
        self.pending_data = b""
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=_creationflags(),
        )
        self.server_log_thread = threading.Thread(
            target=self._drain_server_logs,
            name=f"scrcpy-log-{self.scid}",
            daemon=True,
        )
        self.server_log_thread.start()

        # ADB forward may accept a local TCP connection before the remote
        # abstract socket is ready. Such an early connection can immediately
        # receive EOF. Retry with a NEW socket until the server produces media.
        deadline = time.monotonic() + max(SERVER_CONNECT_TIMEOUT, FIRST_VIDEO_DATA_TIMEOUT)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.server_process.poll() is not None:
                raise self._startup_error(
                    f"Scrcpy ADB shell exited with code {self.server_process.returncode}"
                )

            candidate: socket.socket | None = None
            try:
                candidate = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                candidate.settimeout(1.0)
                first_data = candidate.recv(FIRST_VIDEO_READ_SIZE)
                if not first_data:
                    last_error = ConnectionError(
                        "ADB forward accepted TCP but remote Scrcpy socket returned EOF"
                    )
                    candidate.close()
                    candidate = None
                    time.sleep(0.2)
                    continue

                # Windows SelectorEventLoop can fail with WinError 10038
                # if a socket registered by sock_recv() is closed concurrently.
                # Keep this socket blocking and perform reads in a worker thread.
                candidate.settimeout(None)
                self.socket = candidate
                candidate = None
                self.pending_data = first_data
                self.started = True
                logger.info(
                    "[SCRCPY] Video ready: serial=%s scid=%s port=%s first_bytes=%d head=%s",
                    self.serial,
                    self.scid,
                    self.port,
                    len(first_data),
                    first_data[:32].hex(" "),
                )
                return
            except socket.timeout:
                last_error = TimeoutError("connected, but no H.264 bytes arrived within 1 second")
            except OSError as exc:
                last_error = exc
            finally:
                if candidate is not None:
                    try:
                        candidate.close()
                    except OSError:
                        pass
            time.sleep(0.2)

        raise self._startup_error(
            f"Scrcpy did not produce first H.264 data before timeout: {last_error}"
        )

    def _drain_server_logs(self) -> None:
        process = self.server_process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in iter(process.stdout.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self.server_logs.append(line)
                logger.info("[SCRCPY SERVER] serial=%s scid=%s %s", self.serial, self.scid, line)
        except (OSError, ValueError):
            logger.debug("Scrcpy log reader stopped: serial=%s scid=%s", self.serial, self.scid)

    def _startup_error(self, message: str) -> RuntimeError:
        recent = " | ".join(list(self.server_logs)[-12:])
        if recent:
            message = f"{message}; recent server logs: {recent}"
        return RuntimeError(message)

    async def start(self) -> None:
        await asyncio.to_thread(self._start)

    def _recv(self, size: int) -> bytes:
        if self.pending_data:
            data = self.pending_data[:size]
            self.pending_data = self.pending_data[size:]
            return data
        sock = self.socket
        if sock is None:
            return b""
        try:
            return sock.recv(size)
        except OSError:
            # Socket shutdown/close during stop is an expected lifecycle event.
            if self.socket is not sock or sock.fileno() < 0:
                return b""
            raise

    async def read(self, size: int = 64 * 1024) -> bytes:
        # Preserve the proven Windows-compatible blocking socket path. The
        # blocking recv runs in a worker thread, never on the event loop.
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

        process = self.server_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        if process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        self.server_process = None

        if self.server_log_thread is not None and self.server_log_thread.is_alive():
            self.server_log_thread.join(timeout=1)
        self.server_log_thread = None

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
        self.pending_data = b""
        self.started = False

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop)
