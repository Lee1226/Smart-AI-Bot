"""Loopback-only USB forwarding through macOS usbmuxd; no external CLI needed."""
import asyncio
import plistlib
import socket
import struct


async def exchange(reader, writer, message):
    body = plistlib.dumps(dict(MessageType=message.pop('MessageType'), ClientVersionString='SAB',
                              ProgName='SAB Device Helper', kLibUSBMuxVersion=3, **message))
    writer.write(struct.pack('<IIII', len(body) + 16, 1, 8, 1) + body)
    await writer.drain()
    size, version, kind, tag = struct.unpack('<IIII', await reader.readexactly(16))
    if not 16 <= size <= 1024 * 1024 or version != 1 or kind != 8 or tag != 1:
        raise RuntimeError('Invalid usbmuxd response')
    return plistlib.loads(await reader.readexactly(size - 16))


async def usb_connection(serial, remote_port, socket_path='/var/run/usbmuxd'):
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        inventory = await exchange(reader, writer, {'MessageType': 'ListDevices'})
        device = next((d for d in inventory.get('DeviceList', [])
                       if d.get('Properties', {}).get('SerialNumber', '').replace('-', '') == serial.replace('-', '')
                       and d.get('Properties', {}).get('ConnectionType') == 'USB'), None)
        if not device:
            raise RuntimeError('USB device disconnected')
        result = await exchange(reader, writer, {'MessageType': 'Connect', 'DeviceID': device['DeviceID'],
                                                 'PortNumber': socket.htons(remote_port)})
        if result.get('Number') != 0:
            raise RuntimeError('WDA USB port is not ready')
        return reader, writer
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


async def connected_serials(socket_path='/var/run/usbmuxd'):
    """Physical USB inventory, independent of the Xcode developer tunnel."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        inventory = await exchange(reader, writer, {'MessageType': 'ListDevices'})
        return {d['Properties']['SerialNumber'].replace('-', '')
                for d in inventory.get('DeviceList', [])
                if d.get('Properties', {}).get('ConnectionType') == 'USB'
                and isinstance(d['Properties'].get('SerialNumber'), str)}
    finally:
        writer.close()
        await writer.wait_closed()


async def forward(serial, local_port, socket_path='/var/run/usbmuxd'):
    async def handle(local_reader, local_writer):
        remote_writer = None
        tasks = []
        try:
            remote_reader, remote_writer = await asyncio.wait_for(usb_connection(serial, 8100, socket_path), 10)
            async def copy(reader, writer):
                while True:
                    data = await reader.read(65536)
                    if not data:
                        return
                    writer.write(data)
                    await writer.drain()
            tasks = [asyncio.create_task(copy(local_reader, remote_writer)),
                     asyncio.create_task(copy(remote_reader, local_writer))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, RuntimeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for writer in (local_writer, remote_writer):
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
    return await asyncio.start_server(handle, '127.0.0.1', local_port)
