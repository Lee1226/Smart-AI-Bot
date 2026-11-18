"""Tests for the Scrcpy 4.x framed media stream parser."""
import struct
import unittest

from agent.scrcpy_protocol import (
    PACKET_FLAG_CONFIG,
    PACKET_FLAG_KEY_FRAME,
    PACKET_FLAG_SESSION,
    SCRCPY_AUDIO_AAC,
    SCRCPY_VIDEO_H264,
    ScrcpyMediaStreamParser,
)


class ScrcpyMediaStreamParserTests(unittest.TestCase):
    def test_video_metadata_and_split_packets(self):
        parser = ScrcpyMediaStreamParser(
            stream_type="video", send_dummy_byte=True, send_device_meta=True
        )
        device_name = b"Test Phone".ljust(64, bytes([0]))
        prefix = bytes([0]) + device_name + struct.pack(">I", SCRCPY_VIDEO_H264)
        session = struct.pack(">QII", PACKET_FLAG_SESSION, 1080, 2400)
        config_payload = bytes.fromhex("00000001675350530000000168505053")
        config = struct.pack(">QI", PACKET_FLAG_CONFIG, len(config_payload)) + config_payload
        frame_payload = bytes.fromhex("0000000165494452")
        frame = struct.pack(">QI", 1234567 | PACKET_FLAG_KEY_FRAME, len(frame_payload)) + frame_payload

        self.assertEqual(parser.feed(prefix[:20]), [])
        self.assertEqual(parser.feed(prefix[20:] + session[:7]), [])
        packets = parser.feed(session[7:] + config + frame[:5])
        self.assertEqual([p.kind for p in packets], ["configuration"])
        self.assertEqual(parser.metadata.codec_id, SCRCPY_VIDEO_H264)
        self.assertEqual(parser.metadata.device_name, "Test Phone")
        self.assertEqual((parser.metadata.width, parser.metadata.height), (1080, 2400))
        packets = parser.feed(frame[5:])
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].pts_us, 1234567)
        self.assertTrue(packets[0].keyframe)
        self.assertEqual(packets[0].payload, frame_payload)

    def test_audio_stream_has_no_video_session_header(self):
        parser = ScrcpyMediaStreamParser(stream_type="audio")
        config_payload = b"audio-config"
        packet_payload = b"aac-frame"
        raw = (
            struct.pack(">I", SCRCPY_AUDIO_AAC)
            + struct.pack(">QI", PACKET_FLAG_CONFIG, len(config_payload)) + config_payload
            + struct.pack(">QI", 987654, len(packet_payload)) + packet_payload
        )
        packets = []
        for byte in raw:
            packets.extend(parser.feed(bytes([byte])))
        self.assertEqual(parser.metadata.codec_id, SCRCPY_AUDIO_AAC)
        self.assertEqual([p.kind for p in packets], ["configuration", "data"])
        self.assertEqual(packets[1].pts_us, 987654)
        self.assertEqual(packets[1].payload, packet_payload)


if __name__ == "__main__":
    unittest.main()
