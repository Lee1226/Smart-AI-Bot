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

    def test_preserves_structured_semantic_target(self):
        self.engine.start("device-a", source="web_action")
        event = self.engine.append("device-a", {
            "kind": "tap",
            "x": 160,
            "y": 260,
            "coordinate_space": "device_px",
            "target": {
                "bounds": {"left": 100, "top": 200, "right": 220, "bottom": 320},
                "clickable": True,
                "parentPath": ["Launcher", "Workspace"],
            },
        })

        self.assertEqual(event["coordinates"]["space"], "device_px")
        self.assertEqual(event["target"]["bounds"]["left"], 100)
        self.assertTrue(event["target"]["clickable"])
        self.assertEqual(event["target"]["parentPath"], ["Launcher", "Workspace"])


    def test_preserves_nested_target_when_root_fields_are_empty(self):
        self.engine.start("device-a", source="web_action")
        event = self.engine.append("device-a", {
            "kind": "tap",
            "x": 120,
            "y": 240,
            "target": {
                "text": "搜索",
                "resourceId": "app:id/search",
                "className": "android.widget.Button",
                "clickable": True,
                "enabled": True,
                "visible": True,
                "bounds": {"left": 100, "top": 200, "right": 160, "bottom": 280},
            },
        })
        self.assertEqual(event["target"]["text"], "搜索")
        self.assertEqual(event["target"]["resource_id"], "app:id/search")
        self.assertEqual(event["target_analysis"]["resolution"], "direct_clickable")
        self.assertEqual(event["target_analysis"]["status"], "actionable")

    def test_resolves_clickable_ancestor_from_event_time_snapshot(self):
        self.engine.start("device-a", source="web_action")
        event = self.engine.append("device-a", {
            "kind": "tap",
            "x": 150,
            "y": 250,
            "coordinates": {"x": 150, "y": 250, "space": "device_px"},
            "before": {"elements": [
                {
                    "text": "搜索图标",
                    "className": "ImageView",
                    "clickable": False,
                    "enabled": True,
                    "visible": True,
                    "bounds": {"left": 120, "top": 220, "right": 180, "bottom": 280},
                    "tap_target": {
                        "text": "搜索",
                        "resourceId": "app:id/search_row",
                        "className": "LinearLayout",
                        "clickable": True,
                        "enabled": True,
                        "visible": True,
                        "bounds": {"left": 80, "top": 200, "right": 300, "bottom": 320},
                    },
                },
            ]},
        })
        analysis = event["target_analysis"]
        self.assertEqual(analysis["resolution"], "clickable_ancestor")
        self.assertEqual(analysis["resolved_target"]["text"], "搜索")
        self.assertEqual(analysis["evidence"], "exact_before_snapshot")

    def test_does_not_promote_approximate_observation_to_executable_target(self):
        self.engine.start("device-a", source="portal_touch")
        event = self.engine.append("device-a", {
            "kind": "tap",
            "x": 150,
            "y": 250,
            "ui_observation": {
                "phase": "post_event_poll_approximate",
                "elements": [{
                    "text": "后来出现的按钮",
                    "clickable": True,
                    "enabled": True,
                    "visible": True,
                    "bounds": {"left": 100, "top": 200, "right": 200, "bottom": 300},
                }],
            },
        })
        self.assertEqual(event["target_analysis"]["resolution"], "unresolved")
        self.assertIsNone(event["target_analysis"]["resolved_target"])

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
