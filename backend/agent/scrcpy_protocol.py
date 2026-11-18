"""Incremental parser for Scrcpy 4.x framed media sockets.

Scrcpy uses independent video and audio TCP connections. Each stream starts
with a 4-byte codec id; the first video connection may additionally carry a
dummy byte and the 64-byte device name. Video then emits a 12-byte session
header, followed by 12-byte packet headers and encoded payloads.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

SCRCPY_VIDEO_H264 = 0x68323634
SCRCPY_VIDEO_H265 = 0x68323635
SCRCPY_VIDEO_AV1 = 0x00617631
SCRCPY_AUDIO_OPUS = 0x6F707573
SCRCPY_AUDIO_AAC = 0x00616163
SCRCPY_AUDIO_FLAC = 0x666C6163
SCRCPY_AUDIO_RAW = 0x00726177

PACKET_FLAG_SESSION = 1 << 63
PACKET_FLAG_CONFIG = 1 << 62
PACKET_FLAG_KEY_FRAME = 1 << 61
PACKET_PTS_MASK = (1 << 61) - 1
MAX_PACKET_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True)
class ScrcpyPacket:
    kind: Literal["configuration", "data"]
    payload: bytes
    pts_us: int | None
    keyframe: bool = False


@dataclass(frozen=True)
class ScrcpyStreamMetadata:
    codec_id: int
    device_name: str | None = None
    width: int | None = None
    height: int | None = None


class ScrcpyMediaStreamParser:
    """Parse arbitrary TCP chunks without assuming packet-aligned reads."""

    def __init__(
        self,
        *,
        stream_type: Literal["video", "audio"],
        send_dummy_byte: bool = False,
        send_device_meta: bool = False,
        send_codec_meta: bool = True,
    ) -> None:
        self.stream_type = stream_type
        self.send_dummy_byte = send_dummy_byte
        self.send_device_meta = send_device_meta
        self.send_codec_meta = send_codec_meta
        self.buffer = bytearray()
        self.metadata: ScrcpyStreamMetadata | None = None
        self._session_seen = stream_type == "audio"

    def feed(self, data: bytes) -> list[ScrcpyPacket]:
        if data:
            self.buffer.extend(data)
        packets: list[ScrcpyPacket] = []

        if self.metadata is None:
            prefix_size = (1 if self.send_dummy_byte else 0)
            prefix_size += 64 if self.send_device_meta else 0
            prefix_size += 4 if self.send_codec_meta else 0
            if len(self.buffer) < prefix_size:
                return packets
            offset = 0
            if self.send_dummy_byte:
                # Scrcpy's dummy byte is currently zero; validate rather than
                # silently treating an unexpected byte as media data.
                if self.buffer[0] != 0:
                    raise ValueError("Invalid Scrcpy dummy byte")
                offset += 1
            device_name = None
            if self.send_device_meta:
                raw_name = bytes(self.buffer[offset:offset + 64])
                device_name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                offset += 64
            codec_id = struct.unpack_from(">I", self.buffer, offset)[0] if self.send_codec_meta else 0
            del self.buffer[:prefix_size]
            self.metadata = ScrcpyStreamMetadata(codec_id=codec_id, device_name=device_name)

        while len(self.buffer) >= 12:
            pts_flags, size = struct.unpack_from(">QI", self.buffer, 0)
            if size > MAX_PACKET_SIZE:
                raise ValueError(f"Scrcpy packet too large: {size}")
            is_session = bool(pts_flags & PACKET_FLAG_SESSION)
            if is_session and self.stream_type == "video":
                if len(self.buffer) < 12:
                    break
                width, height = struct.unpack_from(">II", self.buffer, 4)
                self.metadata = ScrcpyStreamMetadata(
                    codec_id=self.metadata.codec_id,
                    device_name=self.metadata.device_name,
                    width=width,
                    height=height,
                )
                del self.buffer[:12]
                self._session_seen = True
                continue

            if not self._session_seen:
                # A video stream must provide its session dimensions before
                # media packets; this catches a mismatched/raw-stream socket.
                raise ValueError("Scrcpy video session metadata is missing")
            if len(self.buffer) < 12 + size:
                break

            payload = bytes(self.buffer[12:12 + size])
            del self.buffer[:12 + size]
            is_config = bool(pts_flags & PACKET_FLAG_CONFIG)
            is_keyframe = bool(pts_flags & PACKET_FLAG_KEY_FRAME)
            pts_us = None if is_config else (pts_flags & PACKET_PTS_MASK)
            packets.append(ScrcpyPacket(
                kind="configuration" if is_config else "data",
                payload=payload,
                pts_us=pts_us,
                keyframe=is_keyframe,
            ))

        return packets
