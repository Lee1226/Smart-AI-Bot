import unittest

from agent.local_adb import parse_screen_size


class LocalAdbScreenDimensionsTests(unittest.TestCase):
    def test_parses_physical_size(self):
        self.assertEqual((1080, 2400), parse_screen_size("Physical size: 1080x2400"))

    def test_override_size_takes_precedence(self):
        output = "Physical size: 1080x2400\\nOverride size: 720x1600"
        self.assertEqual((720, 1600), parse_screen_size(output))

    def test_rejects_missing_or_invalid_size(self):
        for output in ("", "Physical size: unknown", "Physical size: 0x2400"):
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    parse_screen_size(output)


if __name__ == "__main__":
    unittest.main()
