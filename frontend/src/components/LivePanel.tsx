import { useState, useEffect, useRef, useMemo, useSyncExternalStore } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchDeviceCapabilities, controlDevice, Device, getToken } from '../lib/api'
import { useT } from '../lib/i18n'
import H264Canvas from './H264Canvas'
import H264WebGLPlayer from './H264WebGLPlayer'
import H264Player from './H264Player'
import { browserUsbH264Source, subscribeUsb, usbSnapshot } from '../lib/browserUsb'
import { websocketH264Source } from '../lib/h264Stream'

type ControlIconName = 'hand' | 'power' | 'back' | 'home' | 'recent' | 'refresh'

function ControlIcon({ name, size = 16 }: { name: ControlIconName; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true as const }
  switch (name) {
    case 'hand':
      return <svg {...common}><path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11 4.5a1.5 1.5 0 0 1 3 0V11 6a1.5 1.5 0 0 1 3 0v6-1a1.5 1.5 0 0 1 3 0v4.5A5.5 5.5 0 0 1 14.5 21h-2a5 5 0 0 1-3.8-1.8L5 14.8a1.8 1.8 0 0 1 2.7-2.4L10 15" /></svg>
    case 'power':
      return <svg {...common}><path d="M12 2v10" /><path d="M5.6 5.6a9 9 0 1 0 12.8 0" /></svg>
    case 'back':
      return <svg {...common}><path d="m15 18-6-6 6-6" /><path d="M9 12h11" /></svg>
    case 'home':
      return <svg {...common}><path d="m3 10 9-7 9 7" /><path d="M5 9v12h14V9M9 21v-7h6v7" /></svg>
    case 'recent':
      return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M9 9h6M9 13h6M9 17h3" /></svg>
    case 'refresh':
      return <svg {...common}><path d="M20 7v5h-5" /><path d="M4 17v-5h5" /><path d="M5.6 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.4-3" /></svg>
  }
}

// WebCodecs (the smooth canvas path) only exists in a secure context
// (HTTPS / localhost). On plain http://<lan-ip> we fall back to the MSE/jmuxer
// path, which works over http but drifts a little.
const CAN_WEBCODECS =
  typeof window !== 'undefined' && window.isSecureContext && 'VideoDecoder' in window

type Mode = 'on' | 'off'

// H.264 screenrecord stream over WebSocket (under /v1 — already ws-proxied).
function h264WsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = getToken()
  const source = 'device'
  const qs = `?source=${source}${token ? `&token=${encodeURIComponent(token)}` : ''}`
  return `${proto}//${location.host}/v1/devices/${id}/live${qs}`
}

// Screenshot mode: poll single JPEG frames (chained on load/error) instead of a
// long-lived MJPEG stream — short requests that complete cleanly, so they never
// hog the browser's per-origin connection pool (which froze the whole UI).
export function ScreenshotView({ deviceId, source }: { deviceId: string; source: 'screenshot' | 'auto' }) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    let visible = true
    let pending = false
    const img = imgRef.current
    if (!img) return
    setStatus('loading')
    const load = () => {
      clearTimeout(timer)
      if (!alive || !visible || document.hidden || pending) return
      pending = true
      const token = getToken()
      const qs = token ? `&token=${encodeURIComponent(token)}` : ''
      img.src = `/api/devices/${deviceId}/screenshot.jpg?source=${source}${qs}&t=${Date.now()}`
    }
    const next = (event: Event) => {
      pending = false
      if (!alive) return
      setStatus(event.type === 'load' ? 'ready' : 'error')
      timer = window.setTimeout(load, event.type === 'load' ? 1000 : 3000)
    }
    const observer = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting
      if (visible) load()
      else clearTimeout(timer)
    })
    observer.observe(img)
    document.addEventListener('visibilitychange', load)
    img.addEventListener('load', next)
    img.addEventListener('error', next)
    load()
    return () => {
      alive = false
      clearTimeout(timer)
      observer.disconnect()
      document.removeEventListener('visibilitychange', load)
      img.removeEventListener('load', next)
      img.removeEventListener('error', next)
    }
  }, [deviceId, source])
  return <>
    <img ref={imgRef} alt="device screen" className={`absolute inset-0 w-full h-full object-contain ${status === 'ready' ? '' : 'invisible'}`} />
    {status !== 'ready' && <Placeholder text={status === 'loading' ? '正在获取画面…' : '画面暂不可用，正在重试…'} />}
  </>
}

export default function LivePanel({ device, defaultMode = 'off', storageKey = 'live-mode', forceCanvas2D = false }: { device: Device | null; defaultMode?: Mode; storageKey?: string; forceCanvas2D?: boolean }) {
  const t = useT()
  const [mode, setMode] = useState<Mode>(() => {
    const saved = localStorage.getItem(storageKey)
    // Migrate the previous auto/video/screenshot selections to the new On state.
    if (saved === 'off') return 'off'
    if (saved === 'auto' || saved === 'video' || saved === 'screenshot' || saved === 'on') return 'on'
    return defaultMode
  })
  const [reloadKey, setReloadKey] = useState(0)
  const [videoAspectRatio, setVideoAspectRatio] = useState<number | null>(null)
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number } | null>(null)
  const [touchEnabled, setTouchEnabled] = useState(true)
  const [controlError, setControlError] = useState('')
  const pointerStart = useRef<{ x: number; y: number; at: number } | null>(null)
  const controlMutation = useMutation({ mutationFn: (data: Parameters<typeof controlDevice>[1]) => controlDevice(device!.id, data), onError: (error: any) => setControlError(error?.response?.data?.detail || error?.message || '设备操作失败'), onSuccess: () => setControlError('') })
  const [adbFailed, setAdbFailed] = useState(false)
  const [streamError, setStreamError] = useState('')
  const [renderer, setRenderer] = useState<'canvas' | 'webgl'>(() => localStorage.getItem('h264-renderer') === 'canvas' ? 'canvas' : 'webgl')
  const activeRenderer = forceCanvas2D ? 'canvas' : renderer
  const usb = useSyncExternalStore(subscribeUsb, usbSnapshot)
  const localUsb = device?.connection_type === 'browser_usb' && usb.phase === 'online' && usb.deviceId === device.id
  const localAdb = device?.connection_type === 'adb_usb'

  const { data: caps } = useQuery({
    queryKey: ['device-caps', device?.id],
    queryFn: () => fetchDeviceCapabilities(device!.id),
    enabled: !!device,
    refetchInterval: 5000,
  })

  // Keep the debug preview frame aligned with the decoded device frame dimensions.
  useEffect(() => { setVideoAspectRatio(null); setVideoDimensions(null); setControlError(''); pointerStart.current = null }, [device?.id, mode, reloadKey])
  const handleVideoSize = (width: number, height: number) => {
    if (!width || !height) return
    setVideoDimensions(current => current?.width === width && current?.height === height ? current : { width, height })
    const next = width / height
    setVideoAspectRatio(current => current !== null && Math.abs(current - next) < 0.001 ? current : next)
  }

  // Reset the video fallback whenever the device or mode changes.
  useEffect(() => { setAdbFailed(false); setStreamError('') }, [device?.id, mode, localUsb, reloadKey])

  const pick = (m: Mode) => {
    setMode(m)
    localStorage.setItem(storageKey, m)
    setReloadKey(k => k + 1) // force the stream to reconnect
  }

  const online = device?.status === 'online' || localUsb
  const harmony = device?.connection_type === 'hdc_bridge'
  const ios = device?.connection_type === 'ios_bridge'
  const screenshotsOnly = harmony || ios
  const wantsVideo = mode === 'on' && !screenshotsOnly
  const remoteVideo = !!caps?.video_available
  const usingUsb = wantsVideo && localUsb && !adbFailed
  const usingLocalAdb = wantsVideo && !!localAdb && !adbFailed
  const usingRemote = wantsVideo && remoteVideo && !localUsb && !localAdb && !adbFailed
  const videoUnavailable = mode === 'on' && (screenshotsOnly || !(usingUsb || usingLocalAdb || usingRemote))
  const videoSource = useMemo(() => device
    ? localUsb ? browserUsbH264Source(device.id) : websocketH264Source(h264WsUrl(device.id))
    : undefined, [device?.id, localUsb, remoteVideo, mode, reloadKey])
  const videoFailed = (message: string) => { setStreamError(message); setAdbFailed(true) }
  const webglFailed = (message: string) => { setStreamError(message); setRenderer('canvas'); localStorage.setItem('h264-renderer', 'canvas') }
  const chooseRenderer = (value: 'canvas' | 'webgl') => { setRenderer(value); localStorage.setItem('h264-renderer', value); setReloadKey(k => k + 1) }
  const canStream = !!device && mode === 'on' && online
  const supportsManualControl = !!device && online && ['portal', 'adb_usb', 'adb_bridge'].includes(device.connection_type)
  const mapPointer = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    if (!videoDimensions || rect.width <= 0 || rect.height <= 0) return null
    const relativeX = event.clientX - rect.left
    const relativeY = event.clientY - rect.top
    // Reject presses in any letterbox area instead of clamping them onto an edge.
    if (relativeX < 0 || relativeY < 0 || relativeX > rect.width || relativeY > rect.height) return null
    return {
      x: Math.max(0, Math.min(videoDimensions.width - 1, Math.round(relativeX / rect.width * videoDimensions.width))),
      y: Math.max(0, Math.min(videoDimensions.height - 1, Math.round(relativeY / rect.height * videoDimensions.height))),
    }
  }
  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!forceCanvas2D || !touchEnabled || !supportsManualControl || !videoDimensions || controlMutation.isPending || event.button !== 0) return
    const point = mapPointer(event)
    if (!point) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    pointerStart.current = { ...point, at: Date.now() }
  }
  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = pointerStart.current
    pointerStart.current = null
    if (!start || !touchEnabled || !supportsManualControl || !device) return
    const end = mapPointer(event)
    if (!end) return
    // Match ScrcpyPlayer: classify movement in displayed CSS pixels, not
    // source-video pixels, so changing stream resolution does not change feel.
    const rect = event.currentTarget.getBoundingClientRect()
    const startCssX = start.x / videoDimensions!.width * rect.width
    const startCssY = start.y / videoDimensions!.height * rect.height
    const endCssX = end.x / videoDimensions!.width * rect.width
    const endCssY = end.y / videoDimensions!.height * rect.height
    const moved = Math.hypot(endCssX - startCssX, endCssY - startCssY) > 10
    if (!moved) controlMutation.mutate({ action: 'tap', x: end.x, y: end.y, source_width: videoDimensions!.width, source_height: videoDimensions!.height })
    else controlMutation.mutate({ action: 'swipe', x: start.x, y: start.y, x2: end.x, y2: end.y, source_width: videoDimensions!.width, source_height: videoDimensions!.height, duration_ms: Math.max(100, Math.min(1200, Date.now() - start.at)) })
  }
  const sendKey = (action: 'back' | 'home' | 'recent' | 'power') => { if (device) controlMutation.mutate({ action }) }

  return (
    <div className={forceCanvas2D ? 'overflow-hidden rounded-xl border border-hairline bg-white shadow-sm' : 'bg-white border rounded-lg p-3 lg:sticky lg:top-6'}>
      <div className={forceCanvas2D ? 'flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-4 py-3' : 'flex flex-wrap gap-2 items-center justify-between mb-3'}>
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink"><span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-primary-soft text-primary">▣</span>{t('实时设备画面', 'Live device screen')}</div>
          {forceCanvas2D && <div className="mt-1 truncate text-xs text-ink-mute">{device?.name || t('等待选择设备', 'Waiting for device')}</div>}
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          {forceCanvas2D && <>
            <button type="button" title={touchEnabled ? '触控已启用' : '触控已禁用'} aria-label={touchEnabled ? '禁用触控' : '启用触控'} disabled={!supportsManualControl || !videoDimensions || controlMutation.isPending} onClick={() => setTouchEnabled(v => !v)} className={touchEnabled ? 'inline-flex h-8 w-8 items-center justify-center rounded-lg border border-primary/30 bg-primary-soft text-primary-deep hover:bg-primary-soft/80 disabled:opacity-40' : 'inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink-mute hover:bg-canvas-soft disabled:opacity-40'}><ControlIcon name="hand" /></button>
            <button type="button" title="电源键" aria-label="电源键" disabled={!supportsManualControl || controlMutation.isPending} onClick={() => sendKey('power')} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink hover:bg-canvas-soft disabled:opacity-40"><ControlIcon name="power" /></button>
            <button type="button" title="返回" aria-label="返回" disabled={!supportsManualControl || controlMutation.isPending} onClick={() => sendKey('back')} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink hover:bg-canvas-soft disabled:opacity-40"><ControlIcon name="back" /></button>
            <button type="button" title="主页" aria-label="主页" disabled={!supportsManualControl || controlMutation.isPending} onClick={() => sendKey('home')} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink hover:bg-canvas-soft disabled:opacity-40"><ControlIcon name="home" /></button>
            <button type="button" title="最近任务" aria-label="最近任务" disabled={!supportsManualControl || controlMutation.isPending} onClick={() => sendKey('recent')} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink hover:bg-canvas-soft disabled:opacity-40"><ControlIcon name="recent" /></button>
            <button type="button" title="刷新画面" aria-label="刷新画面" disabled={!device || mode !== 'on'} onClick={() => setReloadKey(k => k + 1)} className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-hairline text-ink-mute transition hover:bg-canvas-soft disabled:cursor-not-allowed disabled:opacity-40"><ControlIcon name="refresh" size={15} /></button>
          </>}
          <div className="flex rounded-md border overflow-hidden text-xs">
            <button onClick={() => pick('on')} className={`px-3 py-1 transition-colors ${mode === 'on' ? 'bg-primary text-white' : 'bg-white text-ink-mute hover:bg-gray-50'}`}>
              {t('开启', 'On')}
            </button>
            <button onClick={() => pick('off')} className={`px-3 py-1 transition-colors ${mode === 'off' ? 'bg-primary text-white' : 'bg-white text-ink-mute hover:bg-gray-50'}`}>
              {t('关闭', 'Off')}
            </button>
          </div>
        </div>
      </div>

      {!forceCanvas2D && device && <p className="text-sm font-medium mb-3 truncate" title={device.name}>{device.name}</p>}
      {!forceCanvas2D && CAN_WEBCODECS && wantsVideo && <label className="flex items-center justify-end gap-2 mb-2 text-xs"><span className="text-ink-mute">渲染器</span><select aria-label="视频渲染器" value={renderer} onChange={e => chooseRenderer(e.target.value as 'canvas' | 'webgl')} className="border rounded px-2 py-1 bg-white text-ink"><option value="webgl">WebGL（默认）</option><option value="canvas">Canvas 2D</option></select></label>}

      <div className={forceCanvas2D ? 'flex min-h-[520px] items-center justify-center overflow-hidden bg-[#f3f6fa] px-4 py-6 sm:min-h-[640px]' : ''}>
        <div
          onPointerDown={forceCanvas2D ? handlePointerDown : undefined}
          onPointerUp={forceCanvas2D ? handlePointerUp : undefined}
          onPointerCancel={forceCanvas2D ? () => { pointerStart.current = null } : undefined}
          className={forceCanvas2D ? 'relative mx-auto w-full overflow-hidden rounded-[1.1rem] border border-slate-300 bg-transparent shadow-[0_12px_32px_rgba(15,23,42,0.10)] ' + (touchEnabled && supportsManualControl && videoDimensions ? 'cursor-pointer select-none touch-none' : '') : 'relative mx-auto rounded-xl bg-terminal overflow-hidden border border-hairline-strong'}
          style={{
            aspectRatio: forceCanvas2D && videoAspectRatio ? String(videoAspectRatio) : '9 / 19.5',
            maxWidth: forceCanvas2D ? 340 : 300,
            width: '100%',
            maxHeight: forceCanvas2D ? 680 : undefined,
          }}
        >
          {!device ? (
            <Placeholder text={t('选择左侧一台设备', 'Select a device on the left')} />
          ) : mode === 'off' ? (
            <Placeholder text={t('实时画面已关闭', 'Live view is off')} />
          ) : !canStream ? (
            <Placeholder text={t('设备离线，无法取流', 'Device offline, cannot stream')} />
          ) : videoUnavailable ? (
            <Placeholder text={streamError || (screenshotsOnly ? t('当前连接方式暂不支持实时视频', 'Live video is not supported by this connection type') : t('实时视频流不可用。远程 App 请先在手机开启远程视频并授权录屏。', 'Live video is unavailable. Enable remote video and authorize screen capture in the phone app.'))} />
          ) : (usingUsb || usingLocalAdb || usingRemote) && videoSource ? (
            CAN_WEBCODECS ? (
              activeRenderer === 'webgl' ? <H264WebGLPlayer key={`${device.id}-webgl-${reloadKey}`} source={videoSource} onFail={webglFailed} /> : <H264Canvas
                key={`${device.id}-h264-${reloadKey}`}
                source={videoSource}
                onFail={videoFailed}
                onVideoSize={forceCanvas2D ? handleVideoSize : undefined}
              />
            ) : (
              <H264Player
                key={`${device.id}-h264-${reloadKey}`}
                source={videoSource}
                onFail={videoFailed}
              />
            )
          ) : (
            <Placeholder text={t('实时视频流不可用', 'Live video unavailable')} />
          )}
        </div>
      </div>

      {forceCanvas2D && (controlMutation.isPending || controlError || !supportsManualControl || (!videoDimensions && mode === 'on')) && <div className="border-t border-hairline bg-white px-4 py-2">
        {!supportsManualControl && <p className="text-center text-[11px] text-amber-700">当前连接方式暂不支持手动触控控制（支持 Portal、服务端 ADB USB、ADB Bridge）</p>}
        {supportsManualControl && !videoDimensions && mode === 'on' && <p className="text-center text-[11px] text-ink-mute">等待视频分辨率就绪后可启用屏幕触控</p>}
        {controlMutation.isPending && <p className="text-center text-[11px] text-primary">正在发送设备操作…</p>}
        {controlError && <p className="text-center text-[11px] text-rose-600">{controlError}</p>}
      </div>}
      <div className={forceCanvas2D ? 'flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-t border-hairline bg-white px-4 py-3 text-xs text-ink-mute' : 'mt-2 text-center text-xs text-gray-400 font-mono'}>
        {forceCanvas2D && <span className={`inline-flex items-center gap-1.5 ${canStream ? 'text-emerald-700' : 'text-ink-mute'}`}><span className={`h-1.5 w-1.5 rounded-full ${canStream ? 'bg-emerald-500' : 'bg-slate-300'}`} />{canStream ? t('已连接', 'Connected') : mode === 'off' ? t('画面已关闭', 'Screen off') : t('等待连接', 'Waiting')}</span>}
        {device && mode !== 'off' && canStream
          ? usingLocalAdb
            ? t('本地 USB · Scrcpy H.264', 'Local USB · Scrcpy H.264')
            : usingUsb
            ? t('浏览器 USB · H.264 视频流', 'Browser USB · H.264 video')
            : usingRemote
            ? t('远程设备 · H.264 视频流', 'Remote device · H.264 video')
            : videoUnavailable ? (screenshotsOnly ? t('当前连接不支持实时视频', 'Live video unsupported') : t('视频流未就绪', 'Video not ready')) : ' '
          : ' '}
      </div>
      {device?.connection_type === 'portal' && !remoteVideo && wantsVideo && <p className="mt-1 text-center text-xs text-ink-mute">{t('远程视频需要新版 App，并在手机点击「开启远程视频」授权录屏。', 'Remote video requires the updated app and screen capture authorization on the phone.')}</p>}
      {streamError && mode === 'on' && <p className="mt-1 text-center text-xs text-amber-600">{streamError}</p>}
    </div>
  )
}

function Placeholder({ text }: { text: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-terminal-mute font-mono">
      {text}
    </div>
  )
}
