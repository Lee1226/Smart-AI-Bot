import { parseUiXml } from './browserUsbUi'
import { Adb, AdbDaemonTransport } from '@yume-chan/adb'
import { AdbDaemonWebUsbDeviceManager } from '@yume-chan/adb-daemon-webusb'
import AdbWebCredentialStore from '@yume-chan/adb-credential-web'
import { createDevice, fetchDevices } from './api'
import { usbH264Source } from './browserUsbVideo'
import type { H264StreamSource } from './h264Stream'

// One connection per tab, outside page components so SPA navigation keeps it alive.
type Status = { phase: 'idle' | 'connecting' | 'online'; deviceId?: string; message: string }
let status: Status = { phase: 'idle', message: '' }
const listeners = new Set<() => void>()
const videoStops = new Set<() => void>()
let adb: Adb | undefined
let ws: WebSocket | undefined
let rawUsb: USBDevice | undefined
let generation = 0
const publish = (next: Status) => { status = next; listeners.forEach(fn => fn()) }
export const usbSnapshot = () => status
export const subscribeUsb = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
export const usbSupported = () => window.isSecureContext && !!AdbDaemonWebUsbDeviceManager.BROWSER
export function browserUsbH264Source(deviceId: string): H264StreamSource {
  return handlers => {
    if (!adb || status.phase !== 'online' || status.deviceId !== deviceId) {
      handlers.fail('此 USB 设备未连接在当前标签页')
      return () => {}
    }
    const stop = usbH264Source(adb)(handlers)
    const disconnect = () => { stop(); handlers.fail('USB 已断开') }
    videoStops.add(disconnect)
    return () => { videoStops.delete(disconnect); stop() }
  }
}
const quote = (value: string) => `'${value.replace(/'/g, "'\\''")}'`
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e)

export function disconnectUsb(message = '') {
  generation++
  videoStops.forEach(stop => stop()); videoStops.clear()
  ws?.close(); ws = undefined
  const previous = adb; adb = undefined
  if (previous) void Promise.resolve(previous.close()).catch(() => {})
  const raw = rawUsb; rawUsb = undefined
  if (raw) void raw.close().catch(() => {})
  publish({ phase: 'idle', message })
}

async function runShell(device: Adb, command: string, binary = false, signal?: AbortSignal): Promise<string | Uint8Array> {
  const service = device.subprocess.shellProtocol
  if (!service) throw new Error('本地模式需要 Android 7+ 的 shell v2 支持')
  const controller = new AbortController()
  const abort = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  const timer = window.setTimeout(() => controller.abort(), 25000)
  try {
    const process = await service.spawn(command, controller.signal)
    const read = async (stream: typeof process.stdout) => {
      const chunks: Uint8Array[] = []
      const reader = stream.getReader()
      let length = 0
      try {
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          length += value.length
          if (length > 32 * 1024 * 1024) throw new Error('ADB 输出超过 32MB')
          chunks.push(value)
        }
      } finally { reader.releaseLock() }
      const bytes = new Uint8Array(length)
      let offset = 0
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
      return bytes
    }
    const [stdout, stderr, exitCode] = await Promise.all([read(process.stdout), read(process.stderr), process.exited])
    if (exitCode !== 0) throw new Error(new TextDecoder().decode(stderr) || `ADB exit ${exitCode}`)
    return binary ? stdout : new TextDecoder().decode(stdout)
  } catch (e) { controller.abort(e); throw e }
  finally { clearTimeout(timer); signal?.removeEventListener('abort', abort) }
}

async function screenshot(device: Adb, signal: AbortSignal) {
  const bytes = await runShell(device, 'screencap -p', true, signal) as Uint8Array
  const bitmap = await createImageBitmap(new Blob([Uint8Array.from(bytes).buffer], { type: 'image/png' }))
  try {
    const canvas = document.createElement('canvas')
    // Preserve device dimensions. The backend alone downsizes for the model.
    canvas.width = bitmap.width; canvas.height = bitmap.height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('截图 Canvas 不可用')
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/png').split(',')[1]
  } finally { bitmap.close() }
}

export async function executeBrowserUsb(device: Adb, method: string, p: Record<string, unknown>, signal: AbortSignal) {
  const shell = (d: Adb, command: string) => runShell(d, command, false, signal)
  const num = (key: string, min = 0, max = 100000) => {
    const value = p[key]
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw new Error(`无效参数 ${key}`)
    return Math.round(value)
  }
  const pkg = () => {
    if (typeof p.package !== 'string' || !/^[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+$/.test(p.package)) throw new Error('无效应用包名')
    return p.package
  }
  switch (method) {
    case 'ping': return 'pong'
    case 'screenshot': return screenshot(device, signal)
    case 'state': {
      // Unique per connection; cleanup even if parsing fails. Never reuse a stale dump.
      const path = `/data/local/tmp/sab-${generation}.xml`
      const xml = await shell(device, `rm -f ${path}; uiautomator dump ${path} >/dev/null && cat ${path}; result=$?; rm -f ${path}; exit $result`) as string
      const state = parseUiXml(xml)
      const size = await shell(device, 'wm size') as string
      const matches = Array.from(size.matchAll(/(\d+)x(\d+)/g))
      const match = matches[matches.length - 1]
      if (!match) throw new Error('无法读取屏幕尺寸')
      const rotated = state.rotation % 2 === 1
      const width = Number(match[rotated ? 2 : 1]), height = Number(match[rotated ? 1 : 2])
      return { ...state, device_context: { screen_bounds: { width, height } } }
    }
    case 'tap': await shell(device, `input tap ${num('x')} ${num('y')}`); return 'OK'
    case 'swipe': await shell(device, `input swipe ${num('startX')} ${num('startY')} ${num('endX')} ${num('endY')} ${num('duration', 0, 10000)}`); return 'OK'
    case 'keyboard/key': await shell(device, `input keyevent ${num('key_code', 0, 300)}`); return 'OK'
    case 'keyboard/input': {
      const text = new TextDecoder().decode(Uint8Array.from(atob(String(p.base64_text || '')), c => c.charCodeAt(0)))
      if (p.clear) throw new Error('USB 本地模式暂不支持自动清空输入框')
      if (!/^[\x20-\x7e]*$/.test(text) || text.includes('%')) throw new Error('USB 本地模式暂只支持不含 % 的英文和数字输入；中文请手动输入')
      await shell(device, `input text ${quote(text.replace(/ /g, '%s'))}`); return 'OK'
    }
    case 'app': // Portal/SAB's start_app RPC name.
    case 'app/launch': {
      const name = pkg()
      const explicit = p.activity
      if (explicit && (typeof explicit !== 'string' || !/^[\w.$]+$/.test(explicit))) throw new Error('无效应用 Activity')
      if (p.stopBeforeLaunch) await shell(device, `am force-stop ${quote(name)}`)
      const activity = explicit ? `${name}/${explicit}` : (await shell(device, `cmd package resolve-activity --brief ${quote(name)}`) as string).trim().split('\n').pop() || ''
      if (!/^[\w.$]+\/[\w.$]+$/.test(activity)) throw new Error(`找不到应用入口：${name}`)
      const result = await shell(device, `am start -W -n ${quote(activity)}`) as string
      if (/Error:|Exception|Status: (?!ok)/i.test(result)) throw new Error(result)
      return `Launched ${name}`
    }
    case 'app/stop': await shell(device, `am force-stop ${quote(pkg())}`); return 'OK'
    case 'packages': return (await shell(device, 'pm list packages') as string).split('\n').filter(l => l.startsWith('package:')).map(l => ({ packageName: l.slice(8).trim(), appName: l.slice(8).trim() }))
    case 'globalAction':
      if (p.action !== 4) throw new Error('USB 本地模式只支持通知栏 globalAction=4')
      await shell(device, 'cmd statusbar expand-notifications'); return 'OK'
    default: throw new Error(`USB 本地模式暂不支持 ${method}（录制、安装和音视频需使用远程 App 模式）`)
  }
}

export async function connectUsb(existingId?: string) {
  if (status.phase !== 'idle') return
  const manager = AdbDaemonWebUsbDeviceManager.BROWSER
  if (!usbSupported() || !manager) { publish({ phase: 'idle', message: '请使用桌面 Chrome/Edge，通过 HTTPS 或 localhost 打开页面' }); return }
  const current = ++generation
  let rawDevice: Awaited<ReturnType<typeof manager.requestDevice>>
  try {
    // Must happen before any network await to preserve the user gesture.
    const selected = manager.requestDevice()
    publish({ phase: 'connecting', message: '请选择 USB 手机，并在手机上允许 USB 调试' })
    rawDevice = await selected
    if (!rawDevice || current !== generation) throw new Error('已取消 USB 连接')
    rawUsb = rawDevice.raw
    const connection = await rawDevice.connect()
    const closeRaw = async () => { await rawDevice?.raw.close().catch(() => {}) }
    let transport: AdbDaemonTransport
    const authTimer = window.setTimeout(() => { void closeRaw() }, 60000)
    try {
      transport = await AdbDaemonTransport.authenticate({ serial: rawDevice.serial, connection, credentialStore: new AdbWebCredentialStore('SAB Browser USB') })
    } catch (e) { await closeRaw(); throw e }
    finally { clearTimeout(authTimer) }
    const localAdb = new Adb(transport)
    if (current !== generation) { await localAdb.close(); return }
    adb = localAdb
    void transport.disconnected.then(() => { if (current === generation) disconnectUsb('USB 已断开，请重新连接') }, () => { if (current === generation) disconnectUsb('USB 连接异常') })
    const devices = await fetchDevices()
    if (current !== generation) return
    const device = existingId ? devices.find(d => d.id === existingId && d.connection_type === 'browser_usb') : undefined
    if (existingId && !device) throw new Error('找不到本地设备，请刷新页面')
    const registered = device || await createDevice(`${rawDevice.name || 'Android'} · USB`, 'browser_usb')
    if (current !== generation) return
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/v1/providers/browser-usb`)
    ws = socket
    let queue = Promise.resolve()
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => { reject(new Error('服务端连接超时')) }, 15000)
      socket.onopen = () => socket.send(JSON.stringify({ type: 'auth', token: registered.token }))
      socket.onerror = () => { clearTimeout(timer); reject(new Error('服务端 WebSocket 连接失败')) }
      socket.onclose = e => {
        clearTimeout(timer); reject(new Error(`服务端已断开 (${e.code})`))
        if (current === generation) disconnectUsb(`服务端已断开 (${e.code})，请重新连接`)
      }
      socket.onmessage = event => {
        let msg: { type?: string; id?: string; method?: string; params?: Record<string, unknown> }
        try { msg = JSON.parse(event.data) } catch { return }
        if (msg.type === 'ready') {
          clearTimeout(timer); publish({ phase: 'online', deviceId: registered.id, message: `本地 USB 已连接：${registered.name}。请保持此页面打开。` }); resolve(); return
        }
        if (!msg.id || !msg.method) return
        const arrived = Date.now()
        const respond = (response: Record<string, unknown>) => {
          if (current === generation && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ id: msg.id, ...response }))
        }
        // Serialize device operations; reject stale queued commands instead of late taps after RPC timeout.
        queue = queue.then(async () => {
          if (current !== generation) return
          const controller = new AbortController()
          const timer = window.setTimeout(() => controller.abort(), Math.max(0, 25000 - (Date.now() - arrived)))
          try {
            if (Date.now() - arrived > 20000) throw new Error('设备忙，操作排队超时')
            respond({ result: await executeBrowserUsb(localAdb, msg.method!, msg.params || {}, controller.signal) })
          } catch (e) { respond({ status: 'error', result: errorText(e) }) }
          finally { clearTimeout(timer) }
        })
      }
    })
  } catch (e) {
    if (current === generation) {
      const message = errorText(e)
      const busy = /already in use|already in used|DeviceBusy/i.test(message)
      disconnectUsb(busy ? `${message}。WebUSB 无法共享已被本机 ADB 使用的接口；可选择 ADB 桥接与 Android Studio 共存。` : message)
    }
  }
}
