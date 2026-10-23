"""Scrcpy 协议辅助模块 - 参考 ya-webadb 实现"""

from dataclasses import dataclass
from typing import Optional

# Codec IDs
SCRCPY_CODEC_H264 = 0x68323634
SCRCPY_CODEC_H265 = 0x68323635
SCRCPY_CODEC_AV1 = 0x00617631

SCRCPY_CODEC_NAME_TO_ID: dict[str, int] = {
    "h264": SCRCPY_CODEC_H264,
    "h265": SCRCPY_CODEC_H265,
    "av1": SCRCPY_CODEC_AV1,
}

SCRCPY_KNOWN_CODECS = set(SCRCPY_CODEC_NAME_TO_ID.values())

# PTS 标志位
PTS_CONFIG = 1 << 63
PTS_KEYFRAME = 1 << 62


@dataclass
class ScrcpyVideoStreamMetadata:
    """视频流元数据"""
    device_name: Optional[str]
    width: Optional[int]
    height: Optional[int]
    codec: int


@dataclass
class ScrcpyMediaStreamPacket:
    """媒体流数据包"""
    type: str  # 'configuration' | 'data'
    data: bytes
    keyframe: Optional[bool] = None
    pts: Optional[int] = None


@dataclass
class ScrcpyVideoStreamOptions:
    """视频流选项"""
    send_device_meta: bool = True
    send_codec_meta: bool = True
    send_frame_meta: bool = True
    send_dummy_byte: bool = True
    video_codec: str = "h264"


@dataclass
class ScrcpyServerOptions:
    """Scrcpy 服务器选项"""
    max_size: int = 1280
    bit_rate: int = 4_000_000
    max_fps: int = 20
    tunnel_forward: bool = True
    audio: bool = False
    control: bool = False
    cleanup: bool = False
    video_codec: str = "h264"
    send_frame_meta: bool = True
    send_device_meta: bool = True
    send_codec_meta: bool = True
    send_dummy_byte: bool = True
    video_codec_options: Optional[str] = None

