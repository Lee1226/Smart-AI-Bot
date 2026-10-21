"""Exercise the USB forwarding protocol without needing a physical device."""
import asyncio
import plistlib
import socket
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
from ios_usbmux import forward, usb_connection


class UsbMuxTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_selects_usb_serial_and_tunnels_bytes(self):
        requests = []
        async def fake(reader, writer):
            try:
                for _ in range(2):
                    size, _, _, _ = struct.unpack('<IIII', await reader.readexactly(16))
                    request = plistlib.loads(await reader.readexactly(size - 16))
                    requests.append(request)
                    value = {'DeviceList': [
                        {'DeviceID': 1, 'Properties': {'SerialNumber': 'other', 'ConnectionType': 'USB'}},
                        {'DeviceID': 2, 'Properties': {'SerialNumber': '00008120-0004150211820032', 'ConnectionType': 'Network'}},
                        {'DeviceID': 3, 'Properties': {'SerialNumber': '000081200004150211820032', 'ConnectionType': 'USB'}}
                    ]} if request['MessageType'] == 'ListDevices' else {'Number': 0}
                    body = plistlib.dumps(value)
                    writer.write(struct.pack('<IIII', 16 + len(body), 1, 8, 1) + body)
                    await writer.drain()
                data = await reader.readexactly(5)
                writer.write(data.upper()); await writer.drain()
            finally:
                writer.close(); await writer.wait_closed()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'usbmuxd')
            server = await asyncio.start_unix_server(fake, path)
            tunnel = await forward('00008120-0004150211820032', 0, path)
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', tunnel.sockets[0].getsockname()[1])
                writer.write(b'hello'); await writer.drain()
                self.assertEqual(b'HELLO', await asyncio.wait_for(reader.readexactly(5), 2))
                self.assertEqual(3, requests[-1]['DeviceID'])
                self.assertEqual(socket.htons(8100), requests[-1]['PortNumber'])
                self.assertEqual('127.0.0.1', tunnel.sockets[0].getsockname()[0])
                writer.close(); await writer.wait_closed()
            finally:
                tunnel.close(); await tunnel.wait_closed()
                server.close(); await server.wait_closed()

    async def test_missing_device_never_connects_another_device(self):
        async def fake(reader, writer):
            await reader.read(4096)
            body = plistlib.dumps({'DeviceList': []})
            writer.write(struct.pack('<IIII', 16 + len(body), 1, 8, 1) + body)
            await writer.drain()
            self.assertEqual(b'', await reader.read())
            writer.close(); await writer.wait_closed()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'usbmuxd')
            server = await asyncio.start_unix_server(fake, path)
            try:
                with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                    await usb_connection('missing', 8100, path)
            finally:
                server.close(); await server.wait_closed()
