"""Timestamp-preserving MP4 muxing for Scrcpy H.264/AAC packets.

The video stream uses PyAV's codec-context-free mux stream, so no H.264
encoder is opened. Audio stream parameters are initialized from Scrcpy's AAC
AudioSpecificConfig. Encoded packets are muxed directly with device PTS.
"""
from __future__ import annotations

import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from agent.scrcpy_protocol import ScrcpyPacket

logger = logging.getLogger(__name__)
TIME_BASE = Fraction(1, 1_000_000)

_AAC_SAMPLE_RATES = (
    96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
    16000, 12000, 11025, 8000, 7350,
)
_AAC_LAYOUTS = {
    1: "mono",
    2: "stereo",
    3: "3.0",
    4: "quad",
    5: "5.0",
    6: "5.1",
    7: "7.1",
}


def split_annexb_nals(data: bytes) -> list[bytes]:
    """Return NAL payloads without Annex-B start codes."""
    starts: list[tuple[int, int]] = []
    i = 0
    while i < len(data) - 2:
        if data[i:i + 4] == b"\x00\x00\x00\x01":
            starts.append((i, 4))
            i += 4
        elif data[i:i + 3] == b"\x00\x00\x01":
            starts.append((i, 3))
            i += 3
        else:
            i += 1
    if not starts:
        return [data] if data else []
    result = []
    for index, (start, prefix) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        nal = data[start + prefix:end].rstrip(b"\x00")
        if nal:
            result.append(nal)
    return result


def annexb_to_avcc(data: bytes) -> bytes:
    """Convert Annex-B NAL units to 4-byte length-prefixed AVCC packets."""
    nals = split_annexb_nals(data)
    if not nals:
        raise ValueError("H.264 packet contains no Annex-B NAL units")
    return b"".join(len(nal).to_bytes(4, "big") + nal for nal in nals)


def build_avc_decoder_config(data: bytes) -> bytes:
    """Build AVCDecoderConfigurationRecord from SPS/PPS in a config packet."""
    nals = split_annexb_nals(data)
    sps = next((nal for nal in nals if nal and (nal[0] & 0x1F) == 7), None)
    pps = next((nal for nal in nals if nal and (nal[0] & 0x1F) == 8), None)
    if sps is None or pps is None or len(sps) < 4:
        raise ValueError("Scrcpy H.264 configuration is missing SPS or PPS")
    if len(sps) > 0xFFFF or len(pps) > 0xFFFF:
        raise ValueError("H.264 SPS/PPS exceeds MP4 configuration limits")
    return (
        bytes((1, sps[1], sps[2], sps[3], 0xFF, 0xE1))
        + len(sps).to_bytes(2, "big") + sps
        + bytes((1,))
        + len(pps).to_bytes(2, "big") + pps
    )


def parse_aac_audio_specific_config(data: bytes) -> tuple[int, str, int]:
    """Read AAC-LC sample rate and channel layout from AudioSpecificConfig."""
    if len(data) < 2:
        raise ValueError("AAC configuration packet is too short")
    bits = int.from_bytes(data, "big")
    total_bits = len(data) * 8
    position = 0

    def read(count: int) -> int:
        nonlocal position
        if position + count > total_bits:
            raise ValueError("AAC AudioSpecificConfig is truncated")
        value = (bits >> (total_bits - position - count)) & ((1 << count) - 1)
        position += count
        return value

    object_type = read(5)
    if object_type != 2:
        raise ValueError(f"Only AAC-LC is supported by the MP4 recorder (object_type={object_type})")
    frequency_index = read(4)
    if frequency_index == 15:
        sample_rate = read(24)
    elif frequency_index < len(_AAC_SAMPLE_RATES):
        sample_rate = _AAC_SAMPLE_RATES[frequency_index]
    else:
        raise ValueError(f"Unsupported AAC sample-rate index: {frequency_index}")
    channel_config = read(4)
    layout = _AAC_LAYOUTS.get(channel_config)
    if not sample_rate or layout is None:
        raise ValueError(f"Unsupported AAC channel configuration: {channel_config}")
    # AAC-LC GASpecificConfig: frameLengthFlag=0 => 1024 samples,
    # frameLengthFlag=1 => 960 samples per access unit.
    frame_size = 960 if read(1) else 1024
    return sample_rate, layout, frame_size


class ScrcpyMp4Muxer:
    """Mux Scrcpy H.264/AAC packets into one MP4 without re-encoding."""

    MAX_PENDING_PACKETS = 256

    def __init__(self, output_path: Path, width: int, height: int, fps: int = 30):
        self.output_path = Path(output_path)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.container: Any = None
        self.video_stream: Any = None
        self.audio_stream: Any = None
        self.video_configured = False
        self.audio_configured = False
        self.started = False
        self.closed = False
        self._video_config: bytes | None = None
        self._video_config_injected = False
        self._audio_rate: int | None = None
        self._audio_layout: str | None = None
        self._audio_frame_size = 1024
        self._pending_video: list[ScrcpyPacket] = []
        self._pending_audio: list[ScrcpyPacket] = []
        self._last_video_pts: int | None = None
        self._last_audio_pts: int | None = None
        self._video_packets = 0
        self._audio_packets = 0
        self._av: Any = None

    def _ensure_container(self) -> None:
        if self.container is not None:
            return
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(
                "PyAV is required for timestamp-preserving audio/video recording; "
                "install the backend dependency 'av'"
            ) from exc
        if not hasattr(av.container.OutputContainer, "add_mux_stream"):
            raise RuntimeError("PyAV 17 or newer is required for encoded-packet muxing")
        self._av = av
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(str(self.output_path), mode="w", format="mp4", options={"movflags": "+faststart"})
        self.video_stream = self.container.add_mux_stream(
            "h264",
            rate=self.fps,
            width=self.width,
            height=self.height,
            time_base=TIME_BASE,
        )

    def write_video(self, packet: ScrcpyPacket) -> None:
        if packet.kind == "configuration":
            self._ensure_container()
            self._video_config = packet.payload
            # Validate SPS/PPS now; the first keyframe will carry them in-band
            # so FFmpeg can populate MP4's AVC decoder configuration record.
            build_avc_decoder_config(packet.payload)
            self.video_configured = True
            self._start_if_ready()
            return
        if packet.pts_us is None:
            raise ValueError("Scrcpy video packet has no presentation timestamp")
        if not self.video_configured:
            raise RuntimeError("Received H.264 frame before codec configuration")
        if not self.started:
            self._pending_video.append(packet)
            if len(self._pending_video) > self.MAX_PENDING_PACKETS:
                raise RuntimeError("AAC configuration did not arrive; video startup buffer is full")
            return
        self._mux_video(packet)

    def write_audio(self, packet: ScrcpyPacket) -> None:
        if packet.kind == "configuration":
            self._ensure_container()
            self._audio_rate, self._audio_layout, self._audio_frame_size = parse_aac_audio_specific_config(packet.payload)
            if self._audio_frame_size != 1024:
                raise ValueError("AAC 960-sample access units are not supported by the current MP4 muxer")
            self.audio_configured = True
            self._start_if_ready()
            return
        if packet.pts_us is None:
            raise ValueError("Scrcpy audio packet has no presentation timestamp")
        if not self.audio_configured:
            raise RuntimeError("Received AAC frame before codec configuration")
        if not self.started:
            self._pending_audio.append(packet)
            if len(self._pending_audio) > self.MAX_PENDING_PACKETS:
                raise RuntimeError("H.264 configuration did not arrive; audio startup buffer is full")
            return
        self._mux_audio(packet)

    def _start_if_ready(self) -> None:
        if self.started or not (self.video_configured and self.audio_configured):
            return
        self.audio_stream = self.container.add_stream("aac", rate=self._audio_rate)
        self.audio_stream.codec_context.layout = self._audio_layout
        self.audio_stream.time_base = TIME_BASE
        self.started = True

        # Preserve timestamp order across the short startup buffer. PyAV's
        # muxer then interleaves subsequent packets using the same device clock.
        pending = [
            (packet.pts_us, "video", packet) for packet in self._pending_video
        ] + [
            (packet.pts_us, "audio", packet) for packet in self._pending_audio
        ]
        self._pending_video.clear()
        self._pending_audio.clear()
        for _, kind, buffered_packet in sorted(pending, key=lambda item: item[0]):
            if kind == "video":
                self._mux_video(buffered_packet)
            else:
                self._mux_audio(buffered_packet)

    def _mux_video(self, packet: ScrcpyPacket) -> None:
        if self._last_video_pts is not None and packet.pts_us <= self._last_video_pts:
            logger.debug("Dropping non-monotonic video packet PTS=%d", packet.pts_us)
            return
        if not self._video_config_injected:
            if not packet.keyframe:
                return
            payload = (self._video_config or b"") + packet.payload
            self._video_config_injected = True
        else:
            payload = packet.payload
        av_packet = self._av.Packet(payload)
        av_packet.stream = self.video_stream
        av_packet.pts = packet.pts_us
        av_packet.dts = packet.pts_us
        av_packet.duration = max(1, round(1_000_000 / self.fps))
        av_packet.time_base = TIME_BASE
        av_packet.is_keyframe = bool(packet.keyframe)
        self.container.mux(av_packet)
        self._last_video_pts = packet.pts_us
        self._video_packets += 1

    def _mux_audio(self, packet: ScrcpyPacket) -> None:
        if self._last_audio_pts is not None and packet.pts_us <= self._last_audio_pts:
            logger.debug("Dropping non-monotonic audio packet PTS=%d", packet.pts_us)
            return
        av_packet = self._av.Packet(packet.payload)
        av_packet.stream = self.audio_stream
        av_packet.pts = packet.pts_us
        av_packet.dts = packet.pts_us
        av_packet.duration = max(1, round(self._audio_frame_size * 1_000_000 / self._audio_rate))
        av_packet.time_base = TIME_BASE
        self.container.mux(av_packet)
        self._last_audio_pts = packet.pts_us
        self._audio_packets += 1

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.container is not None:
            self.container.close()
            self.container = None
        if not self.started:
            self.output_path.unlink(missing_ok=True)
            raise RuntimeError("Recording did not receive both H.264 and AAC codec configuration")
        if not self._video_packets or not self._audio_packets:
            self.output_path.unlink(missing_ok=True)
            raise RuntimeError("Recording did not contain both video frames and audio packets")
        if not self._video_config_injected:
            self.output_path.unlink(missing_ok=True)
            raise RuntimeError("Recording did not receive an H.264 keyframe")
        if not self.output_path.exists() or self.output_path.stat().st_size <= 0:
            raise RuntimeError("MP4 muxing produced an empty recording")
        try:
            with self._av.open(str(self.output_path), mode="r") as check:
                if not check.streams.video or not check.streams.audio:
                    raise RuntimeError("Final MP4 is missing its video or audio stream")
                if not check.streams.video[0].codec_context.extradata:
                    raise RuntimeError("Final MP4 is missing H.264 decoder configuration")
                if not check.streams.audio[0].codec_context.extradata:
                    raise RuntimeError("Final MP4 is missing AAC decoder configuration")
        except Exception:
            self.output_path.unlink(missing_ok=True)
            raise

    def abort(self) -> None:
        """Close the muxer and discard a partial file after any capture error."""
        if self.closed:
            self.output_path.unlink(missing_ok=True)
            return
        self.closed = True
        try:
            if self.container is not None:
                self.container.close()
        finally:
            self.container = None
            self.output_path.unlink(missing_ok=True)
