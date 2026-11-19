"""Scrcpy 4.x audio/video transport for task recording.

The shared local ADB hub uses this session for timestamped audio/video.
If the audio-enabled server cannot start, it falls back to the established
video-only Scrcpy session so browser preview remains available. The recorder
never treats that fallback as a complete audio/video recording.
"""
from __future__ import annotations

import asyncio
import logging
import random
import socket
import subprocess
import threading
import time
from collections import deque

from agent.scrcpy_protocol import (
    SCRCPY_AUDIO_AAC,
    SCRCPY_VIDEO_H264,
    ScrcpyMediaStreamParser,
    ScrcpyPacket,
)
from agent.scrcpy_video import (
    DEVICE_SERVER_PATH,
    SCRCPY_VERSION,
    _creationflags,
    _ensure_server_on_device,
    _run_adb,
    _video_setting,
    ScrcpyVideoSession,
    server_path,
)

logger = logging.getLogger(__name__)

SERVER_CONNECT_TIMEOUT = 15.0
FIRST_VIDEO_PACKET_TIMEOUT = 20.0
READ_SIZE = 64 * 1024


class ScrcpyMediaSession:
    """Own one Scrcpy server and its separate video/audio TCP sockets."""

    def __init__(
        self,
        adb: str,
        serial: str,
        max_size: int | None = None,
        bit_rate: int | None = None,
        max_fps: int | None = None,
        audio_bit_rate: int = 128_000,
    ) -> None:
        self.adb = adb
        self.serial = serial
        self.max_size = max_size if max_size is not None else _video_setting(
            "SCRCPY_MAX_SIZE", 1280, 480, 2560
        )
        self.bit_rate = bit_rate if bit_rate is not None else _video_setting(
            "SCRCPY_BIT_RATE", 8_000_000, 500_000, 40_000_000
        )
        self.max_fps = max_fps if max_fps is not None else _video_setting(
            "SCRCPY_MAX_FPS", 30, 5, 60
        )
        self.audio_bit_rate = audio_bit_rate
        self.scid = f"{random.getrandbits(31):08x}"
        self.port = self._free_port()
        self.video_socket: socket.socket | None = None
        self.audio_socket: socket.socket | None = None
        self.audio_error: str | None = None
        self._video_only_fallback: ScrcpyVideoSession | None = None
        self.server_process: subprocess.Popen | None = None
        self.server_log_thread: threading.Thread | None = None
        self.server_logs: deque[str] = deque(maxlen=80)
        self.forward_created = False
        self.started = False
        self.video_parser = ScrcpyMediaStreamParser(
            stream_type="video",
            send_dummy_byte=True,
            send_device_meta=True,
            send_codec_meta=True,
        )
        self.audio_parser = ScrcpyMediaStreamParser(
            stream_type="audio",
            send_dummy_byte=False,
            send_device_meta=False,
            send_codec_meta=True,
        )
        self._pending_video: deque[ScrcpyPacket] = deque()
        # Low-overhead, video-socket-only diagnostics. These counters observe
        # TCP reads and parser output; they never alter packet delivery.
        self._video_read_diag = {
            "window_started": time.monotonic(),
            "recv_calls": 0,
            "timeouts": 0,
            "tcp_reads": 0,
            "tcp_bytes": 0,
            "empty_parse_reads": 0,
            "packets": 0,
            "recv_ms_total": 0.0,
            "recv_ms_max": 0.0,
            "last_packet_at": None,
            "max_packet_gap_ms": 0.0,
            "last_pts_us": None,
            "max_pts_gap_ms": 0.0,
            "pts_packets": 0,
        }

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @property
    def video_metadata(self):
        # A raw video-only fallback has no device PTS and is preview-only.
        return None if self._video_only_fallback is not None else self.video_parser.metadata

    @property
    def audio_codec_id(self) -> int | None:
        metadata = self.audio_parser.metadata
        return metadata.codec_id if metadata is not None else None

    def _connect_socket(self, label: str) -> socket.socket:
        deadline = time.monotonic() + SERVER_CONNECT_TIMEOUT
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.server_process is not None and self.server_process.poll() is not None:
                raise self._startup_error(
                    f"Scrcpy server exited with code {self.server_process.returncode}"
                )
            candidate: socket.socket | None = None
            try:
                candidate = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                if label == "video":
                    # The configured dummy byte proves this is the accepted
                    # video socket, not an early ADB-forward connection that
                    # immediately closes before the remote abstract socket is ready.
                    candidate.settimeout(1.0)
                    first_byte = candidate.recv(1)
                    if first_byte != b"\x00":
                        last_error = ConnectionError(
                            "ADB forward did not reach the Scrcpy video socket"
                        )
                        candidate.close()
                        candidate = None
                        time.sleep(0.2)
                        continue
                    self.video_parser.feed(first_byte)
                candidate.settimeout(0.25)
                result = candidate
                candidate = None
                logger.info(
                    "[SCRCPY MEDIA] %s socket connected: serial=%s scid=%s port=%s",
                    label, self.serial, self.scid, self.port,
                )
                return result
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
            finally:
                if candidate is not None:
                    candidate.close()
        raise self._startup_error(f"Scrcpy {label} socket connection timed out: {last_error}")

    def _start(self) -> None:
        jar = server_path()
        if jar is None:
            raise RuntimeError(
                "未找到 Scrcpy Server。请确认 backend/assets/ 中存在 "
                f"scrcpy-server-v{SCRCPY_VERSION}，或设置 SCRCPY_SERVER_PATH"
            )

        _ensure_server_on_device(self.adb, self.serial, jar)
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
            "video=true", "audio=true", "control=false",
            "video_codec=h264", "audio_codec=aac", "audio_source=output",
            f"video_bit_rate={self.bit_rate}",
            f"audio_bit_rate={self.audio_bit_rate}",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            "tunnel_forward=true",
            # Scrcpy 4.0 renamed send_codec_meta to send_stream_meta.
            # Keep codec/session metadata and per-packet PTS for A/V muxing.
            # DesktopConnection accepts video first, then audio, on this port.
            "send_dummy_byte=true",
            "send_device_meta=true",
            "send_stream_meta=true",
            "send_frame_meta=true",
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
            name=f"scrcpy-media-log-{self.scid}",
            daemon=True,
        )
        self.server_log_thread.start()

        try:
            # Scrcpy's tunnel-forward accept order is video, then audio.
            # Connect both before waiting for media; otherwise the server can
            # remain blocked in accept(audio) while the client waits for video.
            self.video_socket = self._connect_socket("video")
            try:
                self.audio_socket = self._connect_socket("audio")
            except Exception as exc:
                # Audio capture is best-effort: a device/app may reject playback
                # capture, but that must never take down the existing preview.
                self.audio_error = str(exc)
                logger.warning(
                    "[SCRCPY MEDIA] audio channel unavailable; keep video alive: serial=%s error=%s",
                    self.serial, exc,
                )
            self.started = True
        except Exception:
            self._stop()
            raise

    def _drain_server_logs(self) -> None:
        process = self.server_process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in iter(process.stdout.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    self.server_logs.append(line)
                    logger.info("[SCRCPY SERVER] serial=%s scid=%s %s", self.serial, self.scid, line)
        except (OSError, ValueError):
            logger.debug("Scrcpy media log reader stopped: serial=%s scid=%s", self.serial, self.scid)

    def _startup_error(self, message: str) -> RuntimeError:
        recent = " | ".join(list(self.server_logs)[-12:])
        return RuntimeError(f"{message}; recent server logs: {recent}" if recent else message)

    def _record_video_read(
        self,
        *,
        elapsed_ms: float,
        timeout: bool = False,
        chunk_bytes: int = 0,
        packet_count: int = 0,
        packets: list[ScrcpyPacket] | None = None,
    ) -> None:
        """Aggregate video TCP-read cadence without affecting the media loop."""
        stats = self._video_read_diag
        now = time.monotonic()
        stats["recv_calls"] += 1
        stats["recv_ms_total"] += elapsed_ms
        stats["recv_ms_max"] = max(stats["recv_ms_max"], elapsed_ms)
        if timeout:
            stats["timeouts"] += 1
        else:
            stats["tcp_reads"] += 1
            stats["tcp_bytes"] += chunk_bytes
            if packet_count == 0:
                stats["empty_parse_reads"] += 1
            else:
                previous = stats["last_packet_at"]
                if previous is not None:
                    stats["max_packet_gap_ms"] = max(
                        stats["max_packet_gap_ms"], (now - previous) * 1000
                    )
                stats["last_packet_at"] = now
                stats["packets"] += packet_count

                # Scrcpy PTS is the device-side frame timestamp. Its cadence
                # distinguishes encoder/source gaps from TCP delivery bursts.
                for packet in packets or ():
                    if packet.pts_us is None:
                        continue
                    previous_pts = stats["last_pts_us"]
                    if previous_pts is not None and packet.pts_us >= previous_pts:
                        stats["max_pts_gap_ms"] = max(
                            stats["max_pts_gap_ms"],
                            (packet.pts_us - previous_pts) / 1000.0,
                        )
                    stats["last_pts_us"] = packet.pts_us
                    stats["pts_packets"] += 1

        elapsed_window = now - stats["window_started"]
        if elapsed_window < 5.0:
            return
        last_packet_at = stats["last_packet_at"]
        last_packet_age_ms = (
            (now - last_packet_at) * 1000 if last_packet_at is not None else -1.0
        )
        logger.info(
            "[SCRCPY DIAG][video-read] serial=%s scid=%s "
            "recv_calls_per_sec=%.1f timeouts_per_sec=%.1f "
            "tcp_reads_per_sec=%.1f tcp_bytes_per_sec=%.0f "
            "packets_per_sec=%.1f empty_parse_reads_per_sec=%.1f "
            "recv_avg_ms=%.2f recv_max_ms=%.2f max_packet_gap_ms=%.1f "
            "max_pts_gap_ms=%.1f pts_packets_per_sec=%.1f "
            "last_packet_age_ms=%.1f parser_buffer_bytes=%d",
            self.serial, self.scid,
            stats["recv_calls"] / elapsed_window,
            stats["timeouts"] / elapsed_window,
            stats["tcp_reads"] / elapsed_window,
            stats["tcp_bytes"] / elapsed_window,
            stats["packets"] / elapsed_window,
            stats["empty_parse_reads"] / elapsed_window,
            stats["recv_ms_total"] / max(stats["recv_calls"], 1),
            stats["recv_ms_max"],
            stats["max_packet_gap_ms"],
            stats["max_pts_gap_ms"],
            stats["pts_packets"] / elapsed_window,
            last_packet_age_ms,
            len(self.video_parser.buffer),
        )
        stats.update({
            "window_started": now,
            "recv_calls": 0,
            "timeouts": 0,
            "tcp_reads": 0,
            "tcp_bytes": 0,
            "empty_parse_reads": 0,
            "packets": 0,
            "recv_ms_total": 0.0,
            "recv_ms_max": 0.0,
            "max_packet_gap_ms": 0.0,
            "max_pts_gap_ms": 0.0,
            "pts_packets": 0,
        })

    def _recv_packets(self, sock: socket.socket | None, parser: ScrcpyMediaStreamParser) -> list[ScrcpyPacket] | None:
        if sock is None:
            raise RuntimeError("Scrcpy media socket is not connected")
        recv_started = time.monotonic()
        try:
            chunk = sock.recv(READ_SIZE)
        except socket.timeout:
            if parser.stream_type == "video":
                self._record_video_read(
                    elapsed_ms=(time.monotonic() - recv_started) * 1000,
                    timeout=True,
                )
            return None
        except OSError as exc:
            if sock.fileno() < 0:
                raise ConnectionError("Scrcpy media socket closed") from exc
            raise
        if not chunk:
            raise ConnectionError("Scrcpy media socket reached EOF")
        packets = parser.feed(chunk)
        if parser.metadata is not None:
            if parser.stream_type == "audio" and parser.metadata.codec_id != SCRCPY_AUDIO_AAC:
                raise RuntimeError(
                    "Scrcpy audio capture unavailable or unsupported: "
                    f"codec_id=0x{parser.metadata.codec_id:08x} (AAC required)"
                )
            if parser.stream_type == "video" and parser.metadata.codec_id != SCRCPY_VIDEO_H264:
                raise RuntimeError(
                    "Scrcpy video codec is unsupported: "
                    f"codec_id=0x{parser.metadata.codec_id:08x} (H.264 required)"
                )
        if parser.stream_type == "video":
            self._record_video_read(
                elapsed_ms=(time.monotonic() - recv_started) * 1000,
                chunk_bytes=len(chunk),
                packet_count=len(packets),
                packets=packets,
            )
        return packets

    def _read_video_until_packet(self) -> None:
        deadline = time.monotonic() + FIRST_VIDEO_PACKET_TIMEOUT
        while time.monotonic() < deadline:
            packets = self._recv_packets(self.video_socket, self.video_parser)
            if packets:
                self._pending_video.extend(packets)
                return
            # An empty packet list means this TCP chunk was valid but did not
            # yet contain a complete framed packet; EOF raises ConnectionError.
        raise TimeoutError("Scrcpy video stream did not produce its first packet")

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self._start)
            await asyncio.to_thread(self._read_video_until_packet)
        except Exception as exc:
            await self.stop()
            self.audio_error = f"Scrcpy audio/video startup failed: {exc}"
            logger.warning(
                "[SCRCPY MEDIA] A/V startup failed; attempting video-only fallback: "
                "serial=%s scid=%s error=%s recent_server_logs=%s",
                self.serial, self.scid, exc, list(self.server_logs)[-20:],
                exc_info=True,
            )
            fallback = ScrcpyVideoSession(
                self.adb, self.serial,
                max_size=self.max_size, bit_rate=self.bit_rate, max_fps=self.max_fps,
            )
            try:
                await fallback.start()
            except Exception:
                logger.exception(
                    "[SCRCPY MEDIA] video-only fallback also failed: serial=%s scid=%s",
                    self.serial, self.scid,
                )
                raise
            self._video_only_fallback = fallback
            self.started = True

    async def read_video_packets(self) -> list[ScrcpyPacket] | None:
        if self._video_only_fallback is not None:
            chunk = await self._video_only_fallback.read(READ_SIZE)
            if chunk is None:
                return None
            if not chunk:
                raise ConnectionError("Fallback Scrcpy video stream ended")
            # PTS is intentionally unavailable in raw preview fallback mode.
            return [ScrcpyPacket("data", chunk, None, False)]
        if self._pending_video:
            return [self._pending_video.popleft()]
        return await asyncio.to_thread(
            self._recv_packets, self.video_socket, self.video_parser
        )

    async def read_audio_packets(self) -> list[ScrcpyPacket] | None:
        if self.audio_error:
            raise RuntimeError(self.audio_error)
        if self.audio_socket is None:
            raise RuntimeError("Scrcpy audio socket is unavailable")
        return await asyncio.to_thread(
            self._recv_packets, self.audio_socket, self.audio_parser
        )

    def _stop(self) -> None:
        for attr in ("video_socket", "audio_socket"):
            sock = getattr(self, attr)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)

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
                logger.debug("Scrcpy media forward cleanup failed: serial=%s", self.serial, exc_info=True)
            self.forward_created = False
        self.started = False

    async def stop(self) -> None:
        fallback = self._video_only_fallback
        self._video_only_fallback = None
        if fallback is not None:
            try:
                await fallback.stop()
            except Exception:
                logger.debug("Fallback Scrcpy stop failed: serial=%s", self.serial, exc_info=True)
        await asyncio.to_thread(self._stop)
