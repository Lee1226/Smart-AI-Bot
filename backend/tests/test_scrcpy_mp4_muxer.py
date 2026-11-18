"""Unit tests for Scrcpy packet conversion used by the MP4 muxer."""
import unittest

from core.scrcpy_mp4_muxer import (
    annexb_to_avcc,
    build_avc_decoder_config,
    parse_aac_audio_specific_config,
    split_annexb_nals,
)


class ScrcpyMp4MuxerHelpersTests(unittest.TestCase):
    def test_split_annexb_supports_three_and_four_byte_prefixes(self):
        sps = bytes.fromhex("6764001f80")
        pps = bytes.fromhex("68ee3c80")
        idr = bytes.fromhex("65aabb")
        payload = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x01" + pps + b"\x00\x00\x00\x01" + idr
        self.assertEqual(split_annexb_nals(payload), [sps, pps, idr])

    def test_annexb_to_avcc_length_prefixes_each_nal(self):
        payload = b"\x00\x00\x00\x01\x65\xaa\xbb\x00\x00\x01\x41\xcc"
        self.assertEqual(
            annexb_to_avcc(payload),
            b"\x00\x00\x00\x03\x65\xaa\xbb\x00\x00\x00\x02\x41\xcc",
        )

    def test_build_avc_decoder_configuration_record(self):
        sps = bytes.fromhex("6764001f80")
        pps = bytes.fromhex("68ee3c80")
        config = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x01" + pps
        avcc = build_avc_decoder_config(config)
        self.assertEqual(avcc[:6], bytes((1, 0x64, 0x00, 0x1F, 0xFF, 0xE1)))
        self.assertIn(sps, avcc)
        self.assertIn(pps, avcc)

    def test_parse_aac_lc_44100_stereo_config(self):
        self.assertEqual(parse_aac_audio_specific_config(bytes.fromhex("1210")), (44100, "stereo"))


if __name__ == "__main__":
    unittest.main()
