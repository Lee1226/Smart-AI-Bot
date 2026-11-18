"""Low-overhead MP4 muxing for timestamped Scrcpy H.264/AAC packets.

Encoded media is not decoded or re-encoded. Scrcpy's Annex-B H.264 access
units are converted to MP4 length-prefixed NAL units; device PTS is preserved
in a microsecond time base.
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


def parse_aac_audio_specific_config(data: bytes) -> tuple[int, str]:
    """Read sample rate and channel layout from AAC AudioSpecificConfig."""
    if len(data) < 2:
        raise ValueError("AAC configuration packet is too short")
    bits = int.from_bytes(data, "big")
    total_bits = len(data) * 8
    object_type = (bits >> (total_bits - 5)) & 0x1F
    if object_type == 0:
        raise ValueError("Invalid AAC audio object type")
    frequency_index = (bits >> (total_bits - 9)) & 0x0F
    if frequency_index == 15:
        if len(data) < 5:
            raise ValueError("AAC explicit sample rate is truncated")
        sample_rate = (bits >> (total_bits - 33)) & 0xFFFFFF
    elif frequency_index < len(_AAC_SAMPLE_RATES):
        sample_rate = _AAC_SAMPLE_RATES[frequency_index]
    else:
        raise ValueError(f"Unsupported AAC sample-rate index: {frequency_index}")
    channel_config = (bits >> (total_bits - 13)) & 0x0F
    layout = _AAC_LAYOUTS.get(channel_config)
    if not sample_rate or layout is None:
        raise ValueError(f"Unsupported AAC channel configuration: {channel_config}")
    return sample_rate, layout


class ScrcpyMp4Muxer:
    """Mux Scrcpy H.264/AAC packets into one MP4 without re-encoding."""

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
        self._av = av
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(str(self.output_path), mode="w", format="mp4")
        self.video_stream = self.container.add_stream("h264", rate=self.fps)
        self.video_stream.width = self.width
        self.video_stream.height = self.height
        self.video_stream.time_base = TIME_BASE
        self.audio_stream = self.container.add_stream("aac")
        self.audio_stream.time_base = TIME_BASE

    def write_video(self, packet: ScrcpyPacket) -> None:
        if packet.kind == "configuration":
            self._ensure_container()
            self.video_stream.codec_context.extradata = build_avc_decoder_config(packet.payload)
            self.video_configured = True
            self._start_if_ready()
            return
        if packet.pts_us is None:
            raise ValueError("Scrcpy video packet has no presentation timestamp")
        if not self.video_configured:
            raise RuntimeError("Received H.264 frame before codec configuration")
        if self._last_video_pts is not None and packet.pts_us <= self._last_video_pts:
            logger.debug("Dropping non-monotonic video packet PTS=%d", packet.pts_us)
            return
        self._ensure_container()
        encoded = annexb_to_avcc(packet.payload)
        av_packet = self._av.Packet(encoded)
        av_packet.stream = self.video_stream
        av_packet.pts = packet.pts_us
        av_packet.dts = packet.pts_us
        av_packet.time_base = TIME_BASE
        av_packet.is_keyframe = bool(packet.keyframe)
        self.container.mux(av_packet)
        self._last_video_pts = packet.pts_us
        self._video_packets += 1

    def write_audio(self, packet: ScrcpyPacket) -> None:
        if packet.kind == "configuration":
            self._ensure_container()
            sample_rate, layout = parse_aac_audio_specific_config(packet.payload)
            self.audio_stream.codec_context.sample_rate = sample_rate
            self.audio_stream.codec_context.layout = layout
            self.audio_stream.codec_context.extradata = packet.payload
            self.audio_configured = True
            self._start_if_ready()
            return
        if packet.pts_us is None:
            raise ValueError("Scrcpy audio packet has no presentation timestamp")
        if not self.audio_configured:
            raise RuntimeError("Received AAC frame before codec configuration")
        if self._last_audio_pts is not None and packet.pts_us <= self._last_audio_pts:
            logger.debug("Dropping non-monotonic audio packet PTS=%d", packet.pts_us)
            return
        self._ensure_container()
        av_packet = self._av.Packet(packet.payload)
        av_packet.stream = self.audio_stream
        av_packet.pts = packet.pts_us
        av_packet.dts = packet.pts_us
        av_packet.time_base = TIME_BASE
        self.container.mux(av_packet)
        self._last_audio_pts = packet.pts_us
        self._audio_packets += 1

    def _start_if_ready(self) -> None:
        if self.container is not None and self.video_configured and self.audio_configured and not self.started:
            self.container.start_encoding()
            self.started = True

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
        if not self.output_path.exists() or self.output_path.stat().st_size <= 0:
            raise RuntimeError("MP4 muxing produced an empty recording")
        try:
            with self._av.open(str(self.output_path), mode="r") as check:
                if not check.streams.video or not check.streams.audio:
                    raise RuntimeError("Final MP4 is missing its video or audio stream")
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
