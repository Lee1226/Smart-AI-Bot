"""Screenshot-to-action mapping must use the image actually shown to the model."""
import io
import unittest
from unittest.mock import AsyncMock

from PIL import Image
from agent.ws_device import WebSocketDevice
from core.test_agent import _resize_screenshot


class ImageCoordinatesTest(unittest.IsolatedAsyncioTestCase):
    async def test_tap_and_swipe_map_full_and_legacy_bridge_images(self):
        # iPad logical points, Android, Harmony, odd dimensions, rotated screen.
        for width, height in [(820, 1180), (1080, 2400), (1320, 2856), (821, 1181), (2400, 1080)]:
            for legacy in (False, True):
                with self.subTest(size=(width, height), legacy=legacy):
                    source = Image.new('RGB', (width // 2, height // 2) if legacy else (width, height))
                    buf = io.BytesIO(); source.save(buf, 'PNG')
                    _, iw, ih = _resize_screenshot(buf.getvalue())
                    device = WebSocketDevice('fixture')
                    device.screen_width, device.screen_height = width, height
                    device.set_model_image_size(iw, ih)
                    device._rpc = AsyncMock()
                    x, y = round(iw * .88), round(ih * .12)
                    expected = {'x': round(x * width / iw), 'y': round(y * height / ih)}
                    await device.tap(x, y)
                    device._rpc.assert_awaited_with('tap', expected)
                    await device.swipe(x, y, iw // 2, ih // 2)
                    params = device._rpc.await_args.args[1]
                    self.assertEqual((expected['x'], expected['y']), (params['startX'], params['startY']))
                    self.assertAlmostEqual(width / 2, params['endX'], delta=3)
                    self.assertAlmostEqual(height / 2, params['endY'], delta=3)
