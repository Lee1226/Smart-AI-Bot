"""Private stdio protocol for the native app. No local HTTP server or shell RPC."""
import asyncio
import fcntl
import json
import logging
import os
import signal
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ios_host import MacHost, discover, pair, save_config, validate_server
from device_host import DeviceHost, tool_path


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


class DesktopHost(DeviceHost):
    builtin_usbmux = True
    online = False

    async def publish(self, ws):
        self.online = True
        try:
            await super().publish(ws)
        finally:
            self.online = False


class Service:
    def __init__(self, path):
        self.path = path
        self.config = json.loads(path.read_text()) if path.exists() else {}
        self.host = None
        self.task = None
        self.busy = False

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = self.host = None

    async def start(self):
        if self.task and not self.task.done():
            return
        if not self.config.get('token'):
            raise RuntimeError('请先从网页生成配对码，在此配对。')
        derived = Path(self.config.get('wda_derived_data', str(Path.home() / 'Library/Developer/Xcode/DerivedData/SAB-WDA')))
        self.host = DesktopHost(self.config, self.path, derived, None)
        self.task = asyncio.create_task(self.host.run())

    async def snapshot(self):
        if self.host:
            inventory = await self.host.inventory()
        else:
            try:
                inventory = await DeviceHost(self.config, self.path, Path(self.config.get('wda_derived_data', '.')), None).inventory()
            except (RuntimeError, OSError, asyncio.TimeoutError, ValueError, KeyError):
                inventory = dict(devices=[], error='请安装并打开 Xcode，完成首次初始化，再解锁 USB 设备。')
        derived = self.config.get('wda_derived_data', '')
        prepared = bool(derived and list(Path(derived).glob('Build/Products/WebDriverAgent*.xctestrun')))
        emit(dict(type='state', paired=bool(self.config.get('token')), server=self.config.get('server', ''),
                  derived=derived, prepared=prepared, online=bool(self.host and self.host.online),
                  tools=inventory.get('tools', {}), ios_error=inventory.get('ios_error', ''),
                  running=bool(self.task and not self.task.done()), **{k: inventory[k] for k in ('devices', 'error')}))

    async def monitor(self):
        while True:
            if not self.busy:
                await self.snapshot()
            await asyncio.sleep(5)

    async def command(self, message):
        action = message.get('action')
        if action == 'pair':
            server = validate_server(message['server'])
            code = message['code'].strip()
            if not 20 <= len(code) <= 100:
                raise RuntimeError('配对码格式不正确，请在网页重新复制。')
            if self.task and not self.task.done():
                raise RuntimeError('请先停止当前连接，再更换配对。')
            credentials = await asyncio.to_thread(pair, server, code, socket.gethostname())
            self.config = dict(**{k: self.config[k] for k in ('wda_derived_data', 'adb', 'hdc') if k in self.config},
                               server=server, **credentials, devices={})
            save_config(self.path, self.config)
            emit(dict(type='notice', message='配对成功，正在连接平台。', paired=True))
            await self.start()
        elif action == 'configure':
            kind = message.get('kind', 'wda')
            derived = Path(message['derived']).expanduser().resolve()
            if kind not in {'wda', 'adb', 'hdc'}:
                raise RuntimeError('不支持的工具类型')
            if kind == 'wda' and not list(derived.glob('Build/Products/WebDriverAgent*.xctestrun')):
                raise RuntimeError('该目录没有已编译的 WDA，请选择 DerivedData 根目录。')
            if kind != 'wda' and (not derived.is_file() or not os.access(derived, os.X_OK)):
                raise RuntimeError('请选择可执行的 ADB / HDC 文件')
            running = bool(self.task and not self.task.done())
            await self.stop()
            self.config['wda_derived_data' if kind == 'wda' else kind] = str(derived)
            save_config(self.path, self.config)
            if running:
                await self.start()
            emit(dict(type='notice', message='工具配置已保存，请在网页连接设备。'))
        elif action == 'start':
            await self.start()
        elif action == 'stop':
            await self.stop()
        elif action != 'refresh':
            raise RuntimeError('不支持的操作')

    async def run(self):
        if self.config.get('token'):
            await self.start()
        await self.snapshot()
        monitor = asyncio.create_task(self.monitor())
        try:
            while True:
                raw = await asyncio.to_thread(sys.stdin.readline)
                if not raw:
                    break
                try:
                    if len(raw) > 8192:
                        raise ValueError('Oversized command')
                    message = json.loads(raw)
                    if message.get('action') == 'quit':
                        break
                    self.busy = True
                    emit(dict(type='busy', busy=True))
                    await self.command(message)
                except (RuntimeError, ValueError, KeyError, OSError) as error:
                    emit(dict(type='error', message=str(error) if isinstance(error, RuntimeError) else '操作失败，请检查平台地址、网络或文件路径。'))
                finally:
                    self.busy = False
                    emit(dict(type='busy', busy=False))
                await self.snapshot()
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await self.stop()


def main():
    if getattr(sys, 'frozen', False):
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if sys.argv[1:] == ['--self-check']:
        import ios_bridge
        import ios_usbmux
        import ssl
        ssl.create_default_context()
        emit(dict(type='self-check', ok=True))
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--ios-bridge':
        sys.argv.pop(1)
        from ios_bridge import main as bridge_main
        asyncio.run(bridge_main())
        return
    if len(sys.argv) > 1 and sys.argv[1] in {'--adb-bridge', '--hdc-bridge'}:
        kind = sys.argv.pop(1)
        if kind == '--adb-bridge':
            from adb_bridge import main as bridge_main
        else:
            from hdc_bridge import main as bridge_main
        asyncio.run(bridge_main())
        return
    path = Path.home() / 'Library/Application Support/SAB Device Helper/host.json'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit(dict(type='error', message='本地助手已在运行，请使用已打开的窗口。'))
            return
        asyncio.run(Service(path).run())


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
