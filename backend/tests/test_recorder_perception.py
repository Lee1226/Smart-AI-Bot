"""Tests for semantic UI metadata emitted by the accessibility parser."""
import unittest

from agent.perception import format_ui_state


class RecorderPerceptionTests(unittest.TestCase):
    def test_exposes_bounds_state_and_parent_path(self):
        raw = {
            "device_context": {"screen_bounds": {"width": 1080, "height": 2400}},
            "phone_state": {"packageName": "com.android.launcher"},
            "a11y_tree": {
                "className": "android.widget.FrameLayout",
                "boundsInScreen": {"left": 0, "top": 0, "right": 1080, "bottom": 2400},
                "children": [{
                    "className": "android.widget.ImageView",
                    "text": "",
                    "contentDescription": "Settings",
                    "resourceId": "com.android.launcher:id/settings",
                    "boundsInScreen": {"left": 100, "top": 200, "right": 220, "bottom": 320},
                    "isClickable": True,
                    "isEnabled": True,
                    "isVisibleToUser": True,
                    "children": [],
                }],
            },
        }

        _, elements, width, height, _ = format_ui_state(raw)
        self.assertEqual((width, height), (1080, 2400))
        self.assertEqual(len(elements), 1)
        element = elements[0]
        self.assertEqual(element["text"], "Settings")
        self.assertEqual(element["bounds"], {"left": 100, "top": 200, "right": 220, "bottom": 320})
        self.assertTrue(element["clickable"])
        self.assertTrue(element["enabled"])
        self.assertEqual(element["parentPath"], ["FrameLayout"])


if __name__ == "__main__":
    unittest.main()
