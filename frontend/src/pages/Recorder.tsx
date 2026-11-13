import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, fetchDevices, getToken } from '../lib/api'
import H264WebGLPlayer from '../components/H264WebGLPlayer'
import H264Canvas from '../components/H264Canvas'
import { websocketH264Source } from '../lib/h264Stream'
import { useT } from '../lib/i18n'

interface Element {
  index: number
  text: string
  className: string
  resourceId: string
  cx: number
  cy: number
  contentDescription?: string
  bounds?: { left: number; top: number; right: number; bottom: number } | null
  clickable?: boolean
  enabled?: boolean
  visible?: boolean
  scrollable?: boolean
  selected?: boolean
  checked?: boolean
  parentPath?: string[]
}

interface Step {
  action: string
  args: Record<string, unknown>
  description: string
}

interface RecordEvent {
  kind: string
  action?: string
  source?: string
  sequence?: number
  seq?: number
  timestamp_ms?: number
  received_at_ms?: number
  coordinates?: { space?: string; x?: number; y?: number; x1?: number; y1?: number; x2?: number; y2?: number }
  target?: { text?: string; content_description?: string; resource_id?: string; class_name?: string; css_selector?: string; test_id?: string; bounds?: { left: number; top: number; right: number; bottom: number }; clickable?: boolean; parentPath?: string[]; parent_path?: string[] }
  text?: string
  contentDescription?: string
  resourceId?: string
  className?: string
  x?: number
  y?: number
  x1?: number
  y1?: number
  x2?: number
  y2?: number
  ts?: number
  screenshot_b64?: string
  screenshot_captured_at_ms?: number
  description?: string
  [key: string]: unknown
}

interface SnapshotData {
  screenshot_b64: string
  ui_text: string
  elements: Element[]
  screen_width?: number
  screen_height?: number
}

interface ActionData extends SnapshotData {
  result: string
  description: string
}

const BTN = 'px-3 py-1.5 text-xs rounded border font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors'
const BTN_GRAY = `${BTN} border-gray-300 text-gray-700 hover:bg-gray-100`
const BTN_BLUE = `${BTN} border-primary bg-primary text-white hover:bg-primary-deep`
const BTN_RED  = `${BTN} border-red-400 text-red-600 hover:bg-red-50`
const BTN_GREEN = `${BTN} border-green-500 bg-green-500 text-white hover:bg-green-600`

export default function Recorder() {
  const navigate = useNavigate()
  const t = useT()

  const { data: allDevices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const onlineDevices = allDevices.filter(d => d.status === 'online')

  const [deviceId, setDeviceId] = useState('')
  const [recording, setRecording] = useState(false)
  const [screenshot, setScreenshot] = useState('')
  const [elements, setElements] = useState<Element[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Text input action state
  const [inputText, setInputText] = useState('')
  const [inputClear, setInputClear] = useState(false)

  // Save form
  const [suiteName, setSuiteName] = useState('')
  const [expected, setExpected] = useState('')
  const [saved, setSaved] = useState<{ suiteId: string } | null>(null)

  // ── Real recording (user operates the phone, we capture taps/scrolls) ──────
  const [realRecording, setRealRecording] = useState(false)
  const [realEvents, setRealEvents] = useState<RecordEvent[]>([])
  const [webEvents, setWebEvents] = useState<RecordEvent[]>([])
  const [selectedEventIndex, setSelectedEventIndex] = useState(0)
  const [previewSize, setPreviewSize] = useState({ width: 0, height: 0 })
  const [screenSize, setScreenSize] = useState({ width: 1080, height: 2400 })
  const [videoAvailable, setVideoAvailable] = useState(false)
  const [renderer, setRenderer] = useState<'webgl' | 'canvas'>(() => localStorage.getItem('h264-renderer') === 'canvas' ? 'canvas' : 'webgl')
  const pointerStart = useRef<{ x: number; y: number } | null>(null)
  const screenRef = useRef<HTMLDivElement>(null)
  const realEventsCursor = useRef(0)

  // Poll captured events while real-recording is active.
  useEffect(() => {
    if (!realRecording || !deviceId) return
    const poll = setInterval(async () => {
      try {
        const { data } = await api.get(`/recorder/record/events?device_id=${deviceId}&since=${realEventsCursor.current}`)
        const events = (data.events ?? []) as RecordEvent[]
        if (events.length) {
          realEventsCursor.current += events.length
          // Capture a nearby frame for this polling batch. It is deliberately
          // labelled as an approximate event frame, not an exact per-touch frame.
          let nearbyScreenshot = ''
          try {
            const snapshot = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
            nearbyScreenshot = snapshot.data.screenshot_b64
            setScreenshot(snapshot.data.screenshot_b64)
            setElements(snapshot.data.elements)
          } catch { /* event capture must continue if screenshot capture fails */ }
          setRealEvents(prev => [...prev, ...events.map((event, index) => ({
            ...event,
            ...(index === events.length - 1 && nearbyScreenshot
              ? { screenshot_b64: nearbyScreenshot, screenshot_captured_at_ms: Date.now() }
              : {}),
          }))])
        }
      } catch { /* transient — ignore */ }
    }, 800)
    return () => clearInterval(poll)
  }, [realRecording, deviceId])

  // ── Device selection helpers ────────────────────────────────────────────────
  const selectedDevice = allDevices.find(d => d.id === deviceId)
  const ios = selectedDevice?.connection_type === 'ios_bridge'
  const harmony = selectedDevice?.connection_type === 'hdc_bridge'

  const applySnapshot = (data: SnapshotData) => {
    setScreenshot(data.screenshot_b64)
    setElements(data.elements)
    if ((data.screen_width ?? 0) > 0 && (data.screen_height ?? 0) > 0) setScreenSize({ width: data.screen_width!, height: data.screen_height! })
  }

  const liveVideoSource = useMemo(() => {
    if (!deviceId) return undefined
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    const query = `?source=device${token ? `&token=${encodeURIComponent(token)}` : ''}`
    return websocketH264Source(`${protocol}//${location.host}/v1/devices/${deviceId}/live${query}`)
  }, [deviceId])

  useEffect(() => {
    if (!deviceId) { setVideoAvailable(false); return }
    let alive = true
    api.get<{ video_available?: boolean }>(`/devices/${deviceId}/capabilities`)
      .then(({ data }) => { if (alive) setVideoAvailable(!!data.video_available) })
      .catch(() => { if (alive) setVideoAvailable(false) })
    return () => { alive = false }
  }, [deviceId])

  // Some USB/Harmony bridges expose screenshots but not the H.264 live stream.
  // Keep the same interaction surface usable with a rate-limited screenshot fallback.
  useEffect(() => {
    if (!recording || videoAvailable || !deviceId) return
    let alive = true
    const refresh = async () => {
      try {
        const { data } = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
        if (alive) applySnapshot(data)
      } catch { /* retry on the next interval */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [recording, videoAvailable, deviceId])

  const screenPoint = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(screenSize.width, Math.round((event.clientX - rect.left) / rect.width * screenSize.width))),
      y: Math.max(0, Math.min(screenSize.height, Math.round((event.clientY - rect.top) / rect.height * screenSize.height))),
    }
  }
  const handleScreenPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!recording || loading) return
    pointerStart.current = screenPoint(event)
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const handleScreenPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = pointerStart.current
    pointerStart.current = null
    if (!start || !recording || loading) return
    const end = screenPoint(event)
    if (Math.hypot(end.x - start.x, end.y - start.y) < 12) {
      void doAction('tap', { ...end, coordinate_space: 'device_px' })
    } else {
      void doAction('swipe', { x1: start.x, y1: start.y, x2: end.x, y2: end.y, duration_ms: 450, coordinate_space: 'device_px' })
    }
  }

  // ── API calls ───────────────────────────────────────────────────────────────
  const fetchSnapshot = async () => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
      applySnapshot(data)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Snapshot failed')
    } finally {
      setLoading(false)
    }
  }

  const doAction = async (action: string, args: Record<string, unknown>) => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.post<ActionData>('/recorder/action', { device_id: deviceId, action, args })
      applySnapshot(data)
      setSteps(prev => [...prev, { action, args, description: data.description }])
      const now = Date.now()
      const coords = ['x', 'y', 'x1', 'y1', 'x2', 'y2'].some(key => args[key] !== undefined)
      setWebEvents(prev => [...prev, {
        kind: action,
        action,
        source: 'web_action',
        sequence: prev.length,
        timestamp_ms: now,
        description: data.description,
        ...args,
        ...(coords ? {
          coordinates: {
            x: typeof args.x === 'number' ? args.x : undefined,
            y: typeof args.y === 'number' ? args.y : undefined,
            x1: typeof args.x1 === 'number' ? args.x1 : undefined,
            y1: typeof args.y1 === 'number' ? args.y1 : undefined,
            x2: typeof args.x2 === 'number' ? args.x2 : undefined,
            y2: typeof args.y2 === 'number' ? args.y2 : undefined,
            space: typeof args.coordinate_space === 'string' ? args.coordinate_space : 'model_image_px',
          },
        } : {}),
        screenshot_b64: data.screenshot_b64,
        screenshot_captured_at_ms: now,
        ...(action === 'tap' || action === 'tap_element' ? (() => {
          const target = action === 'tap_element'
            ? elements.find(el => el.index === args.index)
            : [...elements].reverse().find(el => !!el.bounds && typeof args.x === 'number' && typeof args.y === 'number'
              && el.bounds.left <= (args.x as number) && el.bounds.right >= (args.x as number)
              && el.bounds.top <= (args.y as number) && el.bounds.bottom >= (args.y as number))
          return target ? { target: { text: target.text, resource_id: target.resourceId, class_name: target.className, bounds: target.bounds, clickable: target.clickable, parent_path: target.parentPath } } : {}
        })() : {}),
      }])
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Action failed')
    } finally {
      setLoading(false)
    }
  }

  const startRecording = async () => {
    if (!deviceId) return
    setSteps([])
    setWebEvents([])
    setRealEvents([])
    setSelectedEventIndex(0)
    setSaved(null)
    setError('')
    setLoading(true)
    try {
      const { data } = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
      applySnapshot(data)
      await api.post(`/recorder/session/start?device_id=${deviceId}`)
      setRecording(true)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Failed to connect to device')
    } finally {
      setLoading(false)
    }
  }

  const stopRecording = async () => {
    setRecording(false)
    try {
      const { data } = await api.post<{ events: RecordEvent[] }>(`/recorder/session/stop?device_id=${deviceId}`)
      const normalized = data.events ?? []
      setWebEvents(normalized.map((event, index) => ({
        ...event,
        screenshot_b64: webEvents[index]?.screenshot_b64,
        screenshot_captured_at_ms: webEvents[index]?.screenshot_captured_at_ms,
      })))
      setSelectedEventIndex(0)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Failed to stop recording')
    }
  }

  const resetRecording = () => {
    setRecording(false)
    setScreenshot('')
    setElements([])
    setSteps([])
    setRealEvents([])
    setWebEvents([])
    setSelectedEventIndex(0)
    setPreviewSize({ width: 0, height: 0 })
    setSaved(null)
    setError('')
    setSuiteName('')
    setExpected('')
  }

  const saveRecording = async () => {
    if (!suiteName.trim() || !expected.trim() || steps.length === 0) return
    setLoading(true)
    setError('')
    try {
      const { data } = await api.post<{ suite_id: string }>('/recorder/save', {
        device_id: deviceId,
        suite_name: suiteName.trim(),
        expected: expected.trim(),
        steps,
      })
      setSaved({ suiteId: data.suite_id })
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Save failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Real recording ──────────────────────────────────────────────────────────

  const eventToDescription = (e: RecordEvent): string => {
    const label = e.text || e.contentDescription
    switch (e.action || e.kind) {
      case 'tap':
      case 'click':
      case 'touch':
        return label ? `点击 "${label}"` : `点击坐标 (${e.x}, ${e.y})`
      case 'scroll':
        return `滚动${e.className ? ` (${e.className})` : ''}`
      case 'input':
      case 'input_text':
        return `输入文本`
      default:
        return e.action || e.kind
    }
  }

  const startRealRecording = async () => {
    if (!deviceId) return
    setSteps([])
    setSaved(null)
    setError('')
    setRealEvents([])
    setWebEvents([])
    setSelectedEventIndex(0)
    realEventsCursor.current = 0
    try {
      const snapshot = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
      applySnapshot(snapshot.data)
      const { data } = await api.post<{ mode?: string; message?: string }>(`/recorder/record/start?device_id=${deviceId}`)
      if (data.mode === 'manual') {
        setError(data.message || '当前连接方式不支持真机触摸采集，请使用网页操作录制。')
        return
      }
      setRealRecording(true)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Failed to start recording')
    }
  }

  const stopRealRecording = async () => {
    setRealRecording(false)
    try {
      const { data } = await api.post<{ events: RecordEvent[] }>(`/recorder/record/stop?device_id=${deviceId}`)
      const events = data.events ?? []
      setRealEvents(events.map((event, index) => ({
        ...event,
        screenshot_b64: realEvents[index]?.screenshot_b64,
        screenshot_captured_at_ms: realEvents[index]?.screenshot_captured_at_ms,
      })))
      setSelectedEventIndex(0)
      // Convert captured events into recordable steps (reuse the save flow),
      // deriving inter-action delay_ms from each event's wall-clock timestamp.
      setSteps(events.map((e, i) => {
        const currentTs = e.timestamp_ms ?? e.received_at_ms ?? (typeof e.ts === 'number' ? e.ts * 1000 : 0)
        const previous = events[i - 1]
        const previousTs = previous
          ? (previous.timestamp_ms ?? previous.received_at_ms ?? (typeof previous.ts === 'number' ? previous.ts * 1000 : currentTs))
          : currentTs
        const delayMs = i === 0 ? 0 : Math.max(0, Math.round(currentTs - previousTs))
        const args: Record<string, unknown> = {
          ...e,
          delay_ms: delayMs,
          coordinate_space: e.coordinates?.space || 'device_px',
        }
        // Keep Portal coordinates in their native device-pixel space. Replay
        // dispatches them through tap_absolute/swipe_absolute; never assume 2x.
        return {
          action: e.action || e.kind,
          args,
          description: eventToDescription(e),
        }
      }))
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Failed to stop recording')
    }
  }

  // ── Unified event inspector ─────────────────────────────────────────────────
  const displayedEvents = realEvents.length > 0 ? realEvents : webEvents
  const selectedEvent = displayedEvents[selectedEventIndex]
  const selectedCoordinates = selectedEvent?.coordinates
  const coordinateSpace = selectedCoordinates?.space
    || (selectedEvent?.source === 'portal_touch' ? 'device_px' : selectedEvent?.source === 'web_action' ? 'model_image_px' : 'unknown')
  const eventAction = selectedEvent?.action || selectedEvent?.kind || 'unknown'
  const eventX = selectedCoordinates?.x ?? selectedEvent?.x
  const eventY = selectedCoordinates?.y ?? selectedEvent?.y
  const eventX1 = selectedCoordinates?.x1 ?? selectedEvent?.x1
  const eventY1 = selectedCoordinates?.y1 ?? selectedEvent?.y1
  const eventX2 = selectedCoordinates?.x2 ?? selectedEvent?.x2
  const eventY2 = selectedCoordinates?.y2 ?? selectedEvent?.y2
  const eventScreenshot = selectedEvent?.screenshot_b64 || ''
  const eventTarget = selectedEvent?.target
  const canOverlayCoordinates = coordinateSpace === 'device_px' || coordinateSpace === 'screenshot_px'
  const formatTime = (value?: number) => value ? new Date(value).toLocaleTimeString() : '—'
  const describeEvent = (event: RecordEvent) => event.description || eventToDescription(event)

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">{t('录制测试用例', 'Record test case')}</h1>

      {/* Device selector (always visible) */}
      {!recording && (
        <div className="bg-white border rounded-lg p-4 shadow-sm mb-6 flex items-end gap-4 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm font-medium mb-1">{t('选择设备', 'Select device')}</label>
            {onlineDevices.length === 0 ? (
              <p className="text-sm text-gray-400">{t('暂无在线设备', 'No online devices')}</p>
            ) : (
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={deviceId}
                onChange={e => setDeviceId(e.target.value)}
              >
                <option value="">{t('— 请选择 —', '— Select —')}</option>
                {onlineDevices.map(d => (
                  <option key={d.id} value={d.id}>{d.name} ({d.id.slice(0, 8)}…)</option>
                ))}
              </select>
            )}
          </div>
          <button
            className={BTN_BLUE}
            disabled={!deviceId || loading}
            onClick={startRecording}
          >
            {loading ? t('连接中…', 'Connecting…') : t('▶ 开始录制', '▶ Start recording')}
          </button>
          <button
            className={BTN_GREEN}
            disabled={!deviceId || loading || realRecording || ios || harmony}
            onClick={startRealRecording}
          >
            {t('📱 真机录制', '📱 Record on device')}
          </button>
          <p className="w-full text-xs text-gray-400">
            {t('录制模式：选择设备后，在网页上操作即可控制手机并同步记录步骤，完成后保存为可重复运行的测试用例。', 'Recording mode: after selecting a device, operate in the browser to control the phone and record steps in sync, then save as a repeatable test case.')}
          </p>
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Recording UI */}
      {recording && (
        <>
          {/* Control bar */}
          <div className="flex items-center gap-3 mb-4">
            <span className="text-sm font-medium">
              <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse mr-1.5" />
              {t('录制中 — ', 'Recording — ')}{selectedDevice?.name ?? deviceId}
            </span>
            <button className={BTN_GRAY} disabled={loading} onClick={fetchSnapshot}>
              {loading ? '…' : t('⟳ 刷新截图', '⟳ Refresh screenshot')}
            </button>
            <button className={BTN_RED} onClick={stopRecording}>
              {t('⏹ 停止录制', '⏹ Stop recording')}
            </button>
            <span className="ml-auto text-sm text-gray-400">{steps.length} {t('步已录制', 'steps recorded')}</span>
          </div>

          {/* Main 2-column layout */}
          <div className="grid grid-cols-[1fr_340px] gap-5 mb-6">
            {/* Left: screenshot + action bar */}
            <div>
              <div className="mb-3 rounded-xl border bg-slate-950 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-300">
                  <span>{t('实时手机画面 · 鼠标直接操作', 'Live device screen · click or drag to control')}</span>
                  <div className="flex items-center gap-2"><span>{screenSize.width} × {screenSize.height}</span>
                    <select aria-label="视频渲染器" value={renderer} onChange={event => { const value = event.target.value as 'webgl' | 'canvas'; setRenderer(value); localStorage.setItem('h264-renderer', value) }} className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-white">
                      <option value="webgl">WebGL</option><option value="canvas">Canvas 2D</option></select></div></div>
                <div ref={screenRef} className="relative mx-auto overflow-hidden rounded-lg bg-black touch-none select-none" style={{ aspectRatio: `${screenSize.width} / ${screenSize.height}`, width: 'min(100%, 420px)' }} onPointerDown={handleScreenPointerDown} onPointerUp={handleScreenPointerUp} onPointerCancel={() => { pointerStart.current = null }}>
                  {videoAvailable && liveVideoSource ? (renderer === 'webgl' ? <H264WebGLPlayer source={liveVideoSource} onFail={() => setVideoAvailable(false)} /> : <H264Canvas source={liveVideoSource} onFail={() => setVideoAvailable(false)} />) : screenshot ? <img src={`data:image/png;base64,${screenshot}`} alt="device screen" className="absolute inset-0 h-full w-full object-fill" /> : null}
                  {!videoAvailable && !screenshot && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">{t('正在获取手机画面…', 'Waiting for device screen…')}</div>}
                  {!recording && <div className="absolute inset-0 flex items-center justify-center bg-black/35 text-sm text-white">{t('开始录制后可用鼠标操作手机', 'Start recording to control the phone with your mouse')}</div>}
                  {recording && <div className="pointer-events-none absolute left-2 top-2 rounded bg-emerald-600/90 px-2 py-1 text-[10px] text-white">LIVE · CONTROL ENABLED</div>}
                </div>
                <p className="mt-2 text-center text-[11px] text-slate-400">{t('单击 = 点击 · 按住拖动 = 滑动；点击后自动关联 UI Tree 控件信息。', 'Click = tap · drag = swipe; UI tree metadata is attached automatically.')}</p>
              </div>

              {/* Action bar */}
              <div className="bg-white border rounded-lg p-3 shadow-sm space-y-2">
                {/* Scroll */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">{t('滚动', 'Scroll')}</div>
                  <div className="flex gap-2 flex-wrap">
                    {(['down', 'up', 'left', 'right'] as const).map(dir => {
                      const labels: Record<string, string> = { down: t('↓ 向下', '↓ Down'), up: t('↑ 向上', '↑ Up'), left: t('← 向左', '← Left'), right: t('→ 向右', '→ Right') }
                      return (
                        <button key={dir} className={BTN_GRAY} disabled={loading}
                          onClick={() => doAction('scroll', { direction: dir, distance: 'medium' })}>
                          {labels[dir]}
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* System keys */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">{t('系统操作', 'System actions')}</div>
                  <div className="flex gap-2 flex-wrap">
                    {[
                      { action: 'back', label: t('↩ 返回', '↩ Back') },
                      { action: 'home', label: t('⌂ 主页', '⌂ Home') },
                      { action: 'recent', label: t('⊞ 最近', '⊞ Recent') },
                    ].filter(({ action }) => ios ? action === 'home' : harmony ? action !== 'recent' : true).map(({ action, label }) => (
                      <button key={action} className={BTN_GRAY} disabled={loading}
                        onClick={() => doAction('global_action', { action })}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Text input */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">{t('输入文本', 'Input text')}</div>
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      className="flex-1 border rounded px-2 py-1 text-sm"
                      placeholder={t('输入内容后点击「输入」', 'Type text then click "Input"')}
                      value={inputText}
                      onChange={e => setInputText(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && inputText) {
                          doAction('input_text', { text: inputText, clear: inputClear })
                          setInputText('')
                        }
                      }}
                    />
                    <label className="flex items-center gap-1 text-xs text-gray-500 cursor-pointer select-none">
                      <input type="checkbox" checked={inputClear} onChange={e => setInputClear(e.target.checked)} />
                      {t('清空', 'Clear')}
                    </label>
                    <button
                      className={BTN_BLUE}
                      disabled={!inputText || loading}
                      onClick={() => {
                        doAction('input_text', { text: inputText, clear: inputClear })
                        setInputText('')
                      }}
                    >
                      {t('输入', 'Input')}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Right: element list */}
            <div>
              <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
                {t('UI 元素', 'UI elements')} ({elements.length})
              </div>
              <div className="bg-white border rounded-lg shadow-sm overflow-y-auto" style={{ maxHeight: '70vh' }}>
                {elements.length === 0 && (
                  <p className="text-sm text-gray-400 px-4 py-6 text-center">{t('暂无可交互元素', 'No interactive elements')}</p>
                )}
                {elements.map((el, i) => (
                  <button
                    key={el.index}
                    disabled={loading}
                    onClick={() => doAction('tap_element', { index: el.index })}
                    className={`w-full text-left px-3 py-2 hover:bg-primary-soft disabled:opacity-50 transition-colors ${
                      i > 0 ? 'border-t' : ''
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <span className="shrink-0 mt-0.5 text-xs font-mono bg-gray-100 text-gray-500 rounded px-1">
                        {el.index}
                      </span>
                      <div className="min-w-0">
                        <div className="text-sm truncate">
                          {el.text || <span className="text-gray-400 italic">{el.className || 'element'}</span>}
                        </div>
                        {el.resourceId && (
                          <div className="text-xs text-gray-400 truncate">{el.resourceId}</div>
                        )}
                        {el.bounds && <div className="mt-1 text-[10px] text-gray-400 font-mono">[{el.bounds.left},{el.bounds.top}]–[{el.bounds.right},{el.bounds.bottom}]</div>}
                        <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-gray-400">
                          {el.clickable && <span className="rounded bg-emerald-50 px-1 text-emerald-700">clickable</span>}
                          {el.scrollable && <span className="rounded bg-blue-50 px-1 text-blue-700">scrollable</span>}
                          {el.selected && <span className="rounded bg-amber-50 px-1 text-amber-700">selected</span>}
                        </div>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Unified recording workbench: event list, screenshot and payload inspector */}
      {(realRecording || recording || displayedEvents.length > 0) && (
        <section className="mb-6 rounded-xl border bg-white shadow-sm overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
            <div>
              <div className="text-sm font-semibold">{t('录制事件工作台', 'Recording event workbench')}</div>
              <div className="text-xs text-gray-400 mt-0.5">
                {realRecording ? t('真机触摸采集', 'Device touch capture') : t('网页操作录制', 'Browser action recording')}
                {' · '}{displayedEvents.length}{t(' 条事件', ' events')}
              </div>
            </div>
            <span className="ml-auto text-xs rounded-full px-2 py-1 bg-gray-100 text-gray-600">
              {realRecording ? 'portal_touch' : 'web_action'}
            </span>
            {realRecording && <button className={BTN_RED} onClick={stopRealRecording} disabled={loading}>{t('停止真机录制', 'Stop device recording')}</button>}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(220px,0.85fr)_minmax(320px,1.35fr)_minmax(260px,1fr)]">
            <div className="border-b xl:border-b-0 xl:border-r">
              <div className="px-3 py-2 text-xs font-semibold text-gray-500">{t('事件列表', 'EVENTS')}</div>
              <div className="max-h-[560px] overflow-y-auto">
                {displayedEvents.length === 0 && <div className="px-4 py-10 text-center text-sm text-gray-400">{t('等待操作事件…', 'Waiting for interaction events…')}</div>}
                {displayedEvents.map((event, index) => {
                  const action = event.action || event.kind
                  const coords = event.coordinates
                  const x = coords?.x ?? event.x
                  const y = coords?.y ?? event.y
                  return (
                    <button key={event.event_id as string || index} onClick={() => setSelectedEventIndex(index)}
                      className={`w-full text-left border-t px-3 py-3 transition-colors ${selectedEventIndex === index ? 'bg-primary-soft border-l-2 border-l-primary' : 'hover:bg-gray-50'}`}>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] text-gray-400">#{String(index + 1).padStart(2, '0')}</span>
                        <span className="text-sm font-medium text-gray-800">{describeEvent(event)}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-gray-400">
                        <span>{action}</span>
                        {x !== undefined && y !== undefined && <span>{x}, {y}</span>}
                        <span>{event.source || (realRecording ? 'portal_touch' : 'web_action')}</span>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="border-b xl:border-b-0 xl:border-r p-3 min-w-0">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-semibold text-gray-500">{t('事件画面', 'EVENT FRAME')}</div>
                {selectedEvent && <span className="text-[11px] text-gray-400">{eventScreenshot ? (selectedEvent.source === 'portal_touch' ? t('附近截图 · 非精确帧', 'Nearby frame · approximate') : t('操作后截图', 'Post-action frame')) : t('暂无事件截图', 'No event screenshot')}</span>}
              </div>
              {eventScreenshot ? (
                <div className="flex items-center justify-center rounded-lg bg-slate-100 p-2 min-h-[260px] overflow-hidden">
                  <div className="relative inline-block max-w-full">
                    <img
                      src={`data:image/png;base64,${eventScreenshot}`}
                      alt="Selected event screenshot"
                      className="block max-h-[520px] max-w-full object-contain rounded"
                      onLoad={e => setPreviewSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
                    />
                    {canOverlayCoordinates && previewSize.width > 0 && eventX !== undefined && eventY !== undefined && (
                      <div className="absolute h-5 w-5 rounded-full border-[3px] border-white bg-red-500/80 shadow-lg -translate-x-1/2 -translate-y-1/2"
                        style={{ left: `${eventX / previewSize.width * 100}%`, top: `${eventY / previewSize.height * 100}%` }} />
                    )}
                    {canOverlayCoordinates && previewSize.width > 0 && eventX1 !== undefined && eventY1 !== undefined && eventX2 !== undefined && eventY2 !== undefined && (
                      <svg className="absolute inset-0 h-full w-full pointer-events-none" viewBox={`0 0 ${previewSize.width} ${previewSize.height}`} preserveAspectRatio="none">
                        <line x1={eventX1} y1={eventY1} x2={eventX2} y2={eventY2} stroke="#ef4444" strokeWidth={Math.max(4, previewSize.width / 100)} strokeLinecap="round" />
                        <circle cx={eventX1} cy={eventY1} r={Math.max(7, previewSize.width / 80)} fill="#22c55e" stroke="white" strokeWidth="3" />
                        <circle cx={eventX2} cy={eventY2} r={Math.max(7, previewSize.width / 80)} fill="#ef4444" stroke="white" strokeWidth="3" />
                      </svg>
                    )}
                  </div>
                </div>
              ) : (
                <div className="min-h-[260px] rounded-lg bg-slate-50 border border-dashed flex flex-col items-center justify-center text-center px-5">
                  <span className="text-sm text-gray-500">{t('该事件尚未关联截图', 'No screenshot is linked to this event yet')}</span>
                  <span className="mt-1 text-xs text-gray-400">{realRecording ? t('真机采集按轮询批次尽力关联附近画面；历史精确帧需要后续接入录屏时间戳抽帧。', 'Device events are linked to nearby polling-batch frames; exact historical frames require timestamp-based video extraction.') : t('网页操作事件将在动作返回截图后关联画面。', 'Browser actions are linked to the screenshot returned by the action.')}</span>
                </div>
              )}
              {selectedEvent && (
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
                  <span>{t('坐标', 'Coordinates')}: {eventX !== undefined && eventY !== undefined ? `(${eventX}, ${eventY})` : eventX1 !== undefined ? `(${eventX1}, ${eventY1}) → (${eventX2}, ${eventY2})` : '—'}</span>
                  <span>{t('坐标空间', 'Space')}: {coordinateSpace}</span>
                </div>
              )}
            </div>

            <div className="p-3 min-w-0">
              <div className="text-xs font-semibold text-gray-500 mb-3">{t('事件详情', 'EVENT DETAILS')}</div>
              {!selectedEvent ? <div className="text-sm text-gray-400 py-8 text-center">{t('选择一条事件查看详情', 'Select an event to inspect')}</div> : (
                <div className="space-y-3">
                  <div>
                    <div className="text-xs text-gray-400">{t('操作类型', 'Action')}</div>
                    <div className="text-sm font-semibold mt-0.5">{eventAction}</div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-md bg-gray-50 p-2"><div className="text-[11px] text-gray-400">X</div><div className="font-mono text-sm">{eventX ?? '—'}</div></div>
                    <div className="rounded-md bg-gray-50 p-2"><div className="text-[11px] text-gray-400">Y</div><div className="font-mono text-sm">{eventY ?? '—'}</div></div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div><div className="text-gray-400">{t('坐标空间', 'Coordinate space')}</div><div className="font-mono mt-1 break-all">{coordinateSpace}</div></div>
                    <div><div className="text-gray-400">{t('事件来源', 'Source')}</div><div className="font-mono mt-1 break-all">{selectedEvent.source || '—'}</div></div>
                    <div><div className="text-gray-400">{t('事件序号', 'Sequence')}</div><div className="font-mono mt-1">{selectedEvent.sequence ?? selectedEvent.seq ?? selectedEventIndex}</div></div>
                    <div><div className="text-gray-400">{t('采集时间', 'Timestamp')}</div><div className="font-mono mt-1">{formatTime(selectedEvent.timestamp_ms ?? selectedEvent.received_at_ms)}</div></div>
                  </div>
                  {eventTarget && <div className="text-xs space-y-2"><div className="text-gray-400">{t('目标控件', 'Target')}</div><div className="rounded bg-gray-50 p-2 break-all">{eventTarget.text || eventTarget.content_description || eventTarget.resource_id || eventTarget.css_selector || t('尚未识别语义控件', 'No semantic target detected')}</div>
                    {eventTarget.resource_id && <div><span className="text-gray-400">Resource ID：</span><span className="font-mono">{eventTarget.resource_id}</span></div>}
                    {eventTarget.class_name && <div><span className="text-gray-400">Class：</span><span className="font-mono">{eventTarget.class_name}</span></div>}
                    {eventTarget.bounds && <div><span className="text-gray-400">Bounds：</span><span className="font-mono">{JSON.stringify(eventTarget.bounds)}</span></div>}
                    {(eventTarget.parent_path || eventTarget.parentPath)?.length ? <div><span className="text-gray-400">Parent：</span>{(eventTarget.parent_path || eventTarget.parentPath)?.join(' / ')}</div> : null}
                  </div>}
                  <details className="rounded-lg border">
                    <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600">{t('查看原始事件 JSON', 'View raw event JSON')}</summary>
                    <pre className="max-h-64 overflow-auto border-t bg-slate-50 p-3 text-[11px] leading-relaxed whitespace-pre-wrap break-all">{JSON.stringify(selectedEvent, null, 2)}</pre>
                  </details>
                </div>
              )}
            </div>
          </div>

          <div className="border-t bg-gray-50 px-4 py-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs font-semibold text-gray-500">{t('操作时间线', 'TIMELINE')}</div>
              <div className="text-xs text-gray-400">{displayedEvents.length} {t('个事件', 'events')}</div>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {displayedEvents.map((event, index) => (
                <button key={event.event_id as string || index} onClick={() => setSelectedEventIndex(index)}
                  className={`shrink-0 min-w-[100px] rounded-lg border px-3 py-2 text-left ${selectedEventIndex === index ? 'border-primary bg-primary-soft' : 'bg-white hover:bg-gray-50'}`}>
                  <div className="text-[10px] text-gray-400">#{index + 1} · {formatTime(event.timestamp_ms ?? event.received_at_ms)}</div>
                  <div className="text-xs font-medium mt-1">{event.action || event.kind}</div>
                </button>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Recorded steps */}
      {steps.length > 0 && (
        <div className="bg-white border rounded-lg shadow-sm p-4 mb-5">
          <div className="text-sm font-semibold mb-2">{t('已录制步骤', 'Recorded steps')}（{steps.length}）</div>
          <ol className="space-y-1">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className="shrink-0 font-mono text-xs text-gray-400 mt-0.5 w-5 text-right">{i + 1}.</span>
                <span className="text-gray-700">{s.description}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Save form (shown when stopped with steps, or while recording) */}
      {(steps.length > 0) && !saved && (
        <div className="bg-white border rounded-lg shadow-sm p-4 space-y-3">
          <div className="text-sm font-semibold">{t('保存为测试用例', 'Save as test case')}</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('套件名称', 'Suite name')}</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm"
                placeholder={t('例：登录流程测试', 'e.g. Login flow test')}
                value={suiteName}
                onChange={e => setSuiteName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('期望结果', 'Expected result')}</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm"
                placeholder={t('例：成功进入主页并显示欢迎语', 'e.g. Successfully reach the home page and show a welcome message')}
                value={expected}
                onChange={e => setExpected(e.target.value)}
              />
            </div>
          </div>
          <div className="flex gap-3">
            <button
              className={BTN_BLUE}
              disabled={!suiteName.trim() || !expected.trim() || steps.length === 0 || loading}
              onClick={saveRecording}
            >
              {loading ? t('保存中…', 'Saving…') : t('✓ 保存为测试用例', '✓ Save as test case')}
            </button>
            <button className={BTN_GRAY} onClick={resetRecording}>
              {t('重置', 'Reset')}
            </button>
          </div>
        </div>
      )}

      {/* Success state */}
      {saved && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-center gap-4">
          <span className="text-ok font-medium">{t('✓ 测试用例已保存', '✓ Test case saved')}</span>
          <button
            className="text-sm text-primary hover:underline"
            onClick={() => navigate(`/suites/${saved.suiteId}`)}
          >
            {t('查看套件 →', 'View suite →')}
          </button>
          <button className={`${BTN_GRAY} ml-auto`} onClick={resetRecording}>
            {t('继续录制', 'Continue recording')}
          </button>
        </div>
      )}
    </div>
  )
}
