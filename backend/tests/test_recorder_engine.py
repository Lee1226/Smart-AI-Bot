"""Unit tests for the unified recorder event/session core."""
import unittest

from core.recorder_engine import RecorderEngine


class RecorderEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = RecorderEngine()

    def test_normalizes_event_and_preserves_legacy_fields(self):
        self.engine.start("device-a", source="portal_touch")
        event = self.engine.append("device-a", {
            "kind": "touch",
            "x": 320,
            "y": 640,
            "text": "Wi-Fi",
        })

        self.assertEqual(event["action"], "tap")
        self.assertEqual(event["kind"], "touch")
        self.assertEqual(event["source"], "portal_touch")
        self.assertEqual(event["coordinates"]["space"], "device_px")
        self.assertEqual(event["sequence"], 0)
        self.assertEqual(event["seq"], 0)
        self.assertEqual(event["target"]["text"], "Wi-Fi")
        self.assertIn("event_id", event)
        self.assertIn("session_id", event)

    def test_session_is_isolated_per_device(self):
        self.engine.start("device-a", source="web_action")
        self.engine.start("device-b", source="portal_touch")
        self.engine.append("device-a", {"kind": "tap", "x": 10, "y": 20})
        self.engine.append("device-b", {"kind": "tap", "x": 30, "y": 40})

        self.assertEqual(len(self.engine.events("device-a")), 1)
        self.assertEqual(len(self.engine.events("device-b")), 1)
        self.assertEqual(
            self.engine.events("device-a")[0]["coordinates"]["space"],
            "model_image_px",
        )

    def test_stop_freezes_session(self):
        self.engine.start("device-a", source="portal_touch")
        self.engine.append("device-a", {"kind": "tap", "x": 1, "y": 2})
        stopped = self.engine.stop("device-a")

        self.assertEqual(len(stopped), 1)
        self.assertFalse(self.engine.is_recording("device-a"))
        self.assertIsNone(self.engine.append("device-a", {"kind": "tap", "x": 3, "y": 4}))
        self.assertEqual(len(self.engine.events("device-a")), 1)

    def test_rejects_overlapping_sessions_for_same_device(self):
        self.engine.start("device-a", source="web_action")
        with self.assertRaises(RuntimeError):
            self.engine.start("device-a", source="portal_touch")


if __name__ == "__main__":
    unittest.main()
