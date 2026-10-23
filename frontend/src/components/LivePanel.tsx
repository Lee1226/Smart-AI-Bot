import { useState, useEffect, useRef, useMemo, useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchDeviceCapabilities, Device, getToken } from '../lib/api'
import { useT } from '../lib/i18n'
import H264Canvas from './H264Canvas'
import H264Player from './H264Player'
import { subscribeUsb, usbSnapshot } from '../lib/browserUsb'
import { websocketH264Source } from '../lib/h264Stream'

// WebCodecs (the smooth canvas path) only exists in a secure context
// (HTTPS / localhost). On plain http://<lan-ip> we fall back to the MSE/jmuxer
// path, which works over http but drifts a little.
const CAN_WEBCODECS =
  typeof window !== 'undefined' && window.isSecureContext && 'VideoDecoder' in window

type Mode = 'auto' | 'video' | 'screenshot' | 'off'

const MODES: { key: Mode; label: string; labelEn: string }[] = [
  { key: 'auto', label: '自动', labelEn: 'Auto' },
  { key: 'video', label: '视频流', labelEn: 'Video' },
  { key: 'screenshot', label: '截图', labelEn: 'Screenshot' },
  { key: 'off', label: '关', labelEn: 'Off' },
]

// H.264 screenrecord stream over WebSocket (under /v1 — already ws-proxied).
function h264WsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = getToken()
  return `${proto}//${location.host}/v1/devices/${id}/live?source=device${token ? `&token=${encodeURIComponent(token)}` : ''}`
}

function scrcpyWsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = getToken()
  return `${proto}//${location.host}/v1/devices/${id}/scrcpy${token ? `?token=${encodeURIComponent(token)}` : ''}`
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

export default function LivePanel({ device, defaultMode = 'off', storageKey = 'live-mode' }: { device: Device | null; defaultMode?: Mode; storageKey?: string }) {
  const t = useT()
  const [mode, setMode] = useState<Mode>(
    () => { const saved = localStorage.getItem(storageKey); return MODES.some(m => m.key === saved) ? saved as Mode : defaultMode },
  )
  const [reloadKey, setReloadKey] = useState(0)
  const [adbFailed, setAdbFailed] = useState(false)
  const [streamError, setStreamError] = useState('')
  const usb = useSyncExternalStore(subscribeUsb, usbSnapshot)
  const localUsb = device?.connection_type === 'browser_usb' && usb.phase === 'online' && usb.deviceId === device.id

  const { data: caps } = useQuery({
    queryKey: ['device-caps', device?.id],
    queryFn: () => fetchDeviceCapabilities(device!.id),
    enabled: !!device,
    refetchInterval: 5000,
  })

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
  const wantsVideo = !screenshotsOnly && (mode === 'auto' || mode === 'video')
  const remoteVideo = !!caps?.video_available
  const usingScrcpy = wantsVideo && !!device && device.connection_type !== 'portal' && !screenshotsOnly && !adbFailed
  const usingRemote = wantsVideo && remoteVideo && device?.connection_type === 'portal' && !adbFailed
  const videoUnavailable = !screenshotsOnly && mode === 'video' && !(usingScrcpy || usingRemote)
  const videoSource = useMemo(() => device
    ? device.connection_type === 'portal' ? websocketH264Source(h264WsUrl(device.id)) : websocketH264Source(scrcpyWsUrl(device.id))
    : undefined, [device?.id, localUsb, remoteVideo, mode, reloadKey])
  const videoFailed = (message: string) => { setStreamError(message); setAdbFailed(true) }
  const canStream = !!device && mode !== 'off' && online

  return (
    <div className="bg-white border rounded-lg p-3 lg:sticky lg:top-6">
      <div className="flex flex-wrap gap-2 items-center justify-between mb-3">
        <span className="text-sm font-semibold">{t('实时画面', 'Live screen')}</span>
        <div className="flex rounded-md border overflow-hidden text-xs">
          {MODES.map(m => (
            <button
              key={m.key}
              onClick={() => pick(m.key)}
              disabled={screenshotsOnly && m.key === 'video'}
              title={screenshotsOnly && m.key === 'video' ? '当前 USB 桥接使用截图刷新' : undefined}
              className={`px-2.5 py-1 transition-colors ${
                mode === m.key
                  ? 'bg-primary text-white'
                  : 'bg-white text-ink-mute hover:bg-gray-50 disabled:opacity-40'
              }`}
            >
              {t(m.label, m.labelEn)}
            </button>
          ))}
        </div>
      </div>

      {device && <p className="text-sm font-medium mb-3 truncate" title={device.name}>{device.name}</p>}

      {/* Portrait phone frame */}
      <div
        className="relative mx-auto rounded-xl bg-terminal overflow-hidden border border-hairline-strong"
        style={{ aspectRatio: '9 / 19.5', maxWidth: 300 }}
      >
        {!device ? (
          <Placeholder text={t('选择左侧一台设备', 'Select a device on the left')} />
        ) : mode === 'off' ? (
          <Placeholder text={t('已关闭 — 选择视频流或截图开启', 'Off — choose Video or Screenshot to start')} />
        ) : !canStream ? (
          <Placeholder text={t('设备离线，无法取流', 'Device offline, cannot stream')} />
        ) : videoUnavailable ? (
          <Placeholder text={streamError || t('视频未就绪。远程 App 请先在手机开启远程视频并授权录屏；也可切换截图。', 'Video not ready. Enable remote video and authorize screen capture in the phone app, or select Screenshot.')} />
        ) : (usingScrcpy || usingRemote) && videoSource ? (
          CAN_WEBCODECS ? (
            <H264Canvas
              key={`${device.id}-h264-${reloadKey}`}
              source={videoSource}
              onFail={videoFailed}
            />
          ) : (
            <H264Player
              key={`${device.id}-h264-${reloadKey}`}
              source={videoSource}
              onFail={videoFailed}
            />
          )
        ) : (
          <ScreenshotView
            key={`${device.id}-shot-${reloadKey}`}
            deviceId={device.id}
            source="screenshot"
          />
        )}
      </div>

      {/* Status line */}
      <div className="mt-2 text-center text-xs text-gray-400 font-mono">
        {device && mode !== 'off' && canStream
          ? usingUsb
            ? t('本地 USB · 实时录屏 H.264', 'Local USB · live recording H.264')
            : usingRemote
            ? t('远程设备 · H.264 视频流', 'Remote device · H.264 video')
            : videoUnavailable ? t('视频流未就绪', 'Video not ready') : ios ? t('iOS USB · 截图刷新', 'iOS USB · screenshot refresh') : harmony ? t('鸿蒙 HDC · 截图刷新', 'Harmony HDC · screenshot refresh') : t('截图 · ~1 fps', 'Screenshot · ~1 fps')
            : ' '}
      </div>
      {device?.connection_type === 'portal' && !remoteVideo && wantsVideo && <p className="mt-1 text-center text-xs text-ink-mute">{t('远程视频需要新版 App，并在手机点击「开启远程视频」授权录屏。', 'Remote video requires the updated app and screen capture authorization on the phone.')}</p>}
      {streamError && mode === 'auto' && <p className="mt-1 text-center text-xs text-amber-600">{t('视频不可用，已回落截图；重试请再次点「自动」。', 'Video unavailable; using screenshots. Select Auto again to retry.')}</p>}
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
