"""Local MediaMTX + FFmpeg bridge for the optional WebRTC video PoC.

The bridge consumes the existing Scrcpy raw Annex-B stream. FFmpeg only
repacks H.264 into RTSP (stream copy); it never decodes or re-encodes video.
All listeners are loopback-only in this first local-machine PoC.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_WRITE_STOP = object()


def webrtc_enabled() -> bool:
    return os.environ.get("SMART_AI_WEBRTC_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _executable(env_name: str, fallback: str) -> str:
    configured = os.environ.get(env_name, "").strip()
    candidate = configured or shutil.which(fallback)
    if not candidate:
        raise RuntimeError(
            f"找不到 {fallback} 可执行文件；请安装并配置 {env_name}"
        )
    path = Path(candidate).expanduser()
    if configured and not path.is_file():
        raise RuntimeError(f"{env_name} 指定的文件不存在：{path}")
    return str(path if configured else candidate)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


class MediaMTXManager:
    """Own one local MediaMTX process for the lifetime of the backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._config_path: str | None = None
        self._owned = False
        self.host = os.environ.get("SMART_AI_MEDIAMTX_HOST", "127.0.0.1")
        self.rtsp_port = int(os.environ.get("SMART_AI_MEDIAMTX_RTSP_PORT", "8554"))
        self.webrtc_port = int(os.environ.get("SMART_AI_MEDIAMTX_WEBRTC_PORT", "8889"))
        self.udp_port = int(os.environ.get("SMART_AI_MEDIAMTX_UDP_PORT", "8189"))

    def _config(self) -> str:
        # The first PoC is deliberately restricted to loopback. Anonymous
        # access is limited to generated per-device paths and cannot be reached
        # from the LAN because all MediaMTX listeners bind to 127.0.0.1.
        return f"""logLevel: info
logDestinations: [stdout]
rtsp: yes
rtspAddress: {self.host}:{self.rtsp_port}
rtspTransports: [tcp]
webrtc: yes
webrtcAddress: {self.host}:{self.webrtc_port}
webrtcLocalUDPAddress: {self.host}:{self.udp_port}
webrtcAllowOrigins: ["*"]
authMethod: internal
authInternalUsers:
  - user: any
    pass:
    ips: []
    permissions:
      - action: publish
        path: "~^smart_ai_[a-f0-9]+$"
      - action: read
        path: "~^smart_ai_[a-f0-9]+$"
paths:
  all_others:
    source: publisher
"""

    def ensure_started(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            if _port_open(self.host, self.rtsp_port) or _port_open(self.host, self.webrtc_port):
                raise RuntimeError(
                    "MediaMTX 端口已被占用；请检查 SMART_AI_MEDIAMTX_*_PORT，"
                    "不要复用未受本服务管理的 MediaMTX 实例"
                )

            executable = _executable("MEDIAMTX_PATH", "mediamtx")
            handle = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".yml",
                prefix="smart-ai-mediamtx-", delete=False,
            )
            try:
                handle.write(self._config())
                handle.flush()
                self._config_path = handle.name
            finally:
                handle.close()

            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            try:
                self._process = subprocess.Popen(
                    [executable, self._config_path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    creationflags=flags,
                )
                self._owned = True
                threading.Thread(
                    target=self._drain_logs,
                    args=(self._process,),
                    name="mediamtx-log",
                    daemon=True,
                ).start()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        raise RuntimeError(
                            f"MediaMTX 启动失败，退出码：{self._process.returncode}"
                        )
                    if _port_open(self.host, self.rtsp_port) and _port_open(self.host, self.webrtc_port):
                        logger.info(
                            "[WEBRTC] MediaMTX ready: rtsp=%s:%d whep=%s:%d",
                            self.host, self.rtsp_port, self.host, self.webrtc_port,
                        )
                        return
                    time.sleep(0.1)
                raise RuntimeError("MediaMTX 启动超时，请检查端口和配置")
            except Exception:
                self._stop_locked()
                raise

    @staticmethod
    def _drain_logs(process: subprocess.Popen) -> None:
        if process.stderr is None:
            return
        try:
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    logger.info("[MEDIAMTX] %s", line)
        except (OSError, ValueError):
            pass

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process is not None and process.stderr is not None:
            try:
                process.stderr.close()
            except OSError:
                pass
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None
        self._owned = False

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


class FFmpegRtspPublisher:
    """Feed raw Annex-B H.264 to FFmpeg without blocking Scrcpy's reader."""

    def __init__(self, stream_path: str, fps: int, queue_size: int = 128) -> None:
        self.stream_path = stream_path
        self.fps = max(5, min(60, int(fps)))
        self.queue: queue.Queue[bytes | object] = queue.Queue(maxsize=queue_size)
        self.process: subprocess.Popen | None = None
        self.writer: threading.Thread | None = None
        self.log_reader: threading.Thread | None = None
        self.failed = False
        self._lock = threading.Lock()
        self.rtsp_url = (
            f"rtsp://127.0.0.1:"
            f"{os.environ.get('SMART_AI_MEDIAMTX_RTSP_PORT', '8554')}/{stream_path}"
        )

    def start(self) -> None:
        executable = _executable("FFMPEG_PATH", "ffmpeg")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        command = [
            executable, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-fflags", "nobuffer",
            "-f", "h264", "-framerate", str(self.fps), "-i", "pipe:0",
            "-an", "-c:v", "copy",
            "-rtsp_transport", "tcp", "-muxdelay", "0",
            "-f", "rtsp", self.rtsp_url,
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=flags,
        )
        self.writer = threading.Thread(
            target=self._write_loop, name=f"ffmpeg-stdin-{self.stream_path}", daemon=True,
        )
        self.log_reader = threading.Thread(
            target=self._drain_logs, name=f"ffmpeg-log-{self.stream_path}", daemon=True,
        )
        self.writer.start()
        self.log_reader.start()
        logger.info(
            "[WEBRTC] FFmpeg publisher started: path=%s fps=%d",
            self.stream_path, self.fps,
        )

    def write(self, data: bytes) -> bool:
        if not data or self.failed or self.process is None or self.process.poll() is not None:
            return False
        try:
            self.queue.put_nowait(data)
            return True
        except queue.Full:
            # Never drop arbitrary H.264 byte chunks: doing so corrupts Annex-B.
            # Stop only the optional publisher; the existing WebSocket fan-out
            # continues to receive the original bytes.
            self._mark_failed("FFmpeg 输入队列积压，停止 WebRTC 发布以保护 Scrcpy 主链路")
            return False

    def _mark_failed(self, reason: str) -> None:
        with self._lock:
            if self.failed:
                return
            self.failed = True
        logger.warning("[WEBRTC] publisher disabled: path=%s reason=%s", self.stream_path, reason)
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _write_loop(self) -> None:
        process = self.process
        if process is None or process.stdin is None:
            return
        try:
            while True:
                item = self.queue.get()
                if item is _WRITE_STOP:
                    break
                process.stdin.write(item)
                process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._mark_failed(f"FFmpeg stdin 写入失败：{exc}")
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    def _drain_logs(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    logger.warning("[FFMPEG] path=%s %s", self.stream_path, line)
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            self.queue.put_nowait(_WRITE_STOP)
        except queue.Full:
            self._mark_failed("停止时清理 FFmpeg 输入队列")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.writer and self.writer.is_alive():
            self.writer.join(timeout=1)
        if self.log_reader and self.log_reader.is_alive():
            self.log_reader.join(timeout=1)
        if process.stderr is not None:
            try:
                process.stderr.close()
            except OSError:
                pass
        self.process = None


mediamtx_manager = MediaMTXManager()


def stop_webrtc_media_server() -> None:
    mediamtx_manager.stop()
