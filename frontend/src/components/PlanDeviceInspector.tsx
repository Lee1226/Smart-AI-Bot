import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, Device, fetchDeviceCapabilities, getToken } from '../lib/api'
import H264Canvas from './H264Canvas'
import { websocketH264Source } from '../lib/h264Stream'
import { useT } from '../lib/i18n'

export interface PickedUiElement {
  index: number
  text: string
  className: string
  resourceId: string
  contentDescription?: string
  cx: number
  cy: number
  bounds?: { left: number; top: number; right: number; bottom: number } | null
  clickable?: boolean
  tap_candidate?: boolean
  tap_evidence?: string
  enabled?: boolean
  visible?: boolean
  editable?: boolean
  scrollable?: boolean
  parentPath?: string[]
  tap_target?: { text?: string; resourceId?: string; className?: string; cx?: number; cy?: number } | null
}

interface DeviceSnapshot {
  screenshot_b64: string
  ui_text: string
  elements: PickedUiElement[]
  screen_width: number
  screen_height: number
}

const buttonClass = 'rounded-lg border border-hairline bg-white px-3 py-2 text-xs font-medium text-ink-secondary hover:bg-canvas-cool disabled:cursor-not-allowed disabled:opacity-40'

export default function PlanDeviceInspector({ device, onElementPicked }: {
  device: Device | null
  onElementPicked: (element: (PickedUiElement & { screen_width: number; screen_height: number }) | null) => void
}) {
  const t = useT()
  const [snapshot, setSnapshot] = useState<DeviceSnapshot | null>(null)
  const [selected, setSelected] = useState<PickedUiElement | null>(null)
  const [mode, setMode] = useState<'select' | 'remote'>('select')
  const [filter, setFilter] = useState('')
  const [packageName, setPackageName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [videoFailed, setVideoFailed] = useState(false)
  const [videoSize, setVideoSize] = useState<{ width: number; height: number } | null>(null)
  const pointerStart = useRef<{ x: number; y: number } | null>(null)
  const screenRef = useRef<HTMLDivElement>(null)

  const deviceId = device?.id || ''
  const { data: capabilities } = useQuery({
    queryKey: ['execution-plan-device-capabilities', deviceId],
    queryFn: () => fetchDeviceCapabilities(deviceId),
    enabled: !!deviceId,
    refetchInterval: 10000,
  })
  const videoAvailable = !!capabilities?.video_available && !videoFailed

  const videoSource = useMemo(() => {
    if (!deviceId || !videoAvailable) return undefined
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    const query = `?source=device${token ? `&token=${encodeURIComponent(token)}` : ''}`
    return websocketH264Source(`${protocol}//${location.host}/v1/devices/${deviceId}/live${query}`)
  }, [deviceId, videoAvailable])

  const refreshSnapshot = useCallback(async () => {
    if (!deviceId) return
    setBusy(true)
    setError('')
    try {
      const { data } = await api.get<DeviceSnapshot>(`/recorder/snapshot?device_id=${encodeURIComponent(deviceId)}`)
      setSnapshot(data)
      if (selected) {
        const stillThere = data.elements.find(item => item.index === selected.index)
        if (!stillThere) {
          setSelected(null)
          onElementPicked(null)
        }
      }
    } catch (e) {
      setError((e as Error)?.message || t('获取手机状态失败', 'Failed to read device state'))
    } finally {
      setBusy(false)
    }
  }, [deviceId, selected, onElementPicked, t])

  useEffect(() => {
    setSnapshot(null)
    setSelected(null)
    setError('')
    setNotice('')
    setVideoFailed(false)
    onElementPicked(null)
    if (deviceId) void refreshSnapshot()
  }, [deviceId])

  useEffect(() => {
    if (!deviceId || videoAvailable) return
    let alive = true
    const poll = async () => {
      try {
        const { data } = await api.get<DeviceSnapshot>(`/recorder/snapshot?device_id=${encodeURIComponent(deviceId)}`)
        if (alive) setSnapshot(data)
      } catch { /* keep last successful screenshot */ }
    }
    const timer = window.setInterval(() => { void poll() }, 1600)
    return () => { alive = false; window.clearInterval(timer) }
  }, [deviceId, videoAvailable])

  const selectElement = (element: PickedUiElement | null) => {
    setSelected(element)
    onElementPicked(element && snapshot ? {
      ...element,
      screen_width: snapshot.screen_width,
      screen_height: snapshot.screen_height,
    } : null)
  }

  const filteredElements = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const elements = snapshot?.elements || []
    if (!q) return elements
    return elements.filter(el => [el.text, el.resourceId, el.contentDescription, el.className, ...(el.parentPath || [])]
      .some(value => String(value || '').toLowerCase().includes(q)))
  }, [snapshot, filter])

  const screenPoint = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    if (!snapshot || rect.width <= 0 || rect.height <= 0) return null
    const width = snapshot.screen_width || 1
    const height = snapshot.screen_height || 1
    const scale = Math.min(rect.width / width, rect.height / height)
    const renderedWidth = width * scale
    const renderedHeight = height * scale
    const offsetX = (rect.width - renderedWidth) / 2
    const offsetY = (rect.height - renderedHeight) / 2
    const x = event.clientX - rect.left - offsetX
    const y = event.clientY - rect.top - offsetY
    if (x < 0 || y < 0 || x > renderedWidth || y > renderedHeight) return null
    return { x: Math.round(x / scale), y: Math.round(y / scale) }
  }

  const elementAt = (x: number, y: number) => {
    const candidates = (snapshot?.elements || []).filter(el => el.visible !== false && el.bounds
      && x >= el.bounds.left && x <= el.bounds.right && y >= el.bounds.top && y <= el.bounds.bottom)
    candidates.sort((a, b) => {
      const areaA = a.bounds ? (a.bounds.right - a.bounds.left) * (a.bounds.bottom - a.bounds.top) : Infinity
      const areaB = b.bounds ? (b.bounds.right - b.bounds.left) * (b.bounds.bottom - b.bounds.top) : Infinity
      return areaA - areaB
    })
    return candidates[0] || null
  }

  const refreshTree = async () => {
    if (!deviceId) return
    setBusy(true)
    setError('')
    try {
      const { data } = await api.get<{ elements: PickedUiElement[]; ui_text?: string }>(`/recorder/elements?device_id=${encodeURIComponent(deviceId)}`)
      setSnapshot(current => current ? { ...current, elements: data.elements || [], ui_text: data.ui_text || current.ui_text } : current)
      setNotice(t(`已刷新，读取到 ${data.elements?.length || 0} 个 UI 元素`, `Refreshed ${data.elements?.length || 0} UI elements`))
    } catch (e) {
      setError((e as Error)?.message || t('刷新 UI Tree 失败', 'Failed to refresh UI tree'))
    } finally { setBusy(false) }
  }

  const tapSelected = async () => {
    if (!deviceId || !selected) return
    setBusy(true); setError(''); setNotice('')
    try {
      const { data } = await api.post('/recorder/action', {
        device_id: deviceId, action: 'tap_element', args: { index: selected.index },
      })
      setSnapshot(data)
      setSelected(null)
      onElementPicked(null)
      setNotice(t('已执行控件点击，并刷新页面状态', 'Element tapped; device state refreshed'))
    } catch (e) {
      setError((e as any)?.response?.data?.detail || (e as Error)?.message || 'Tap failed')
    } finally { setBusy(false) }
  }

  const launchPackage = async () => {
    if (!deviceId || !packageName.trim()) return
    setBusy(true); setError(''); setNotice('')
    try {
      const { data } = await api.post('/recorder/action', {
        device_id: deviceId, action: 'start_app', args: { package: packageName.trim() },
      })
      setSnapshot(data)
      setNotice(t('已发送系统启动指令，画面和 UI Tree 已更新；请确认目标应用是否在前台。', 'Launch command sent and device state refreshed; confirm the target app is foregrounded.'))
    } catch (e) {
      setError((e as any)?.response?.data?.detail || (e as Error)?.message || 'Launch failed')
    } finally { setBusy(false) }
  }

  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const point = screenPoint(event)
    if (!point) return
    pointerStart.current = point
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const pointerUp = async (event: PointerEvent<HTMLDivElement>) => {
    const start = pointerStart.current
    pointerStart.current = null
    if (!start) return
    const end = screenPoint(event)
    if (!end) return
    if (mode === 'select') {
      const target = elementAt(end.x, end.y)
      selectElement(target)
      setNotice(target ? t('已从手机画面选中控件，可应用到当前步骤。', 'Element selected from the device screen. Apply it to the current step.') : t('该位置没有匹配到 UI 元素，请在右侧 UI Tree 中选择。', 'No UI element found at this point. Select one from the UI tree.'))
      return
    }
    setBusy(true); setError('')
    try {
      if (Math.hypot(end.x - start.x, end.y - start.y) < 12) {
        await api.post('/recorder/gesture', { device_id: deviceId, action: 'tap', args: { x: end.x, y: end.y, coordinate_space: 'device_px' } })
      } else {
        await api.post('/recorder/gesture', { device_id: deviceId, action: 'swipe', args: { x1: start.x, y1: start.y, x2: end.x, y2: end.y, duration_ms: 450, coordinate_space: 'device_px' } })
      }
      await refreshSnapshot()
    } catch (e) {
      setError((e as any)?.response?.data?.detail || (e as Error)?.message || 'Gesture failed')
    } finally { setBusy(false) }
  }

  if (!device) return (
    <section className="rounded-xl border border-hairline bg-white p-6 text-center shadow-sm">
      <div className="text-sm font-semibold text-ink">{t('先选择一台在线设备', 'Select an online device first')}</div>
      <p className="mt-1 text-xs text-ink-mute">{t('连接设备后，这里会显示实时画面和 UI Tree。', 'The live screen and UI tree appear here after a device is connected.')}</p>
    </section>
  )

  return (
    <section className="overflow-hidden rounded-xl border border-hairline bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline bg-canvas-soft px-4 py-3">
        <div><h2 className="text-sm font-semibold text-ink">{t('真机交互与 UI Tree', 'Device interaction & UI tree')}</h2><p className="mt-0.5 text-xs text-ink-mute">{device.name} · {device.platform || 'Android'} · {videoAvailable ? 'H.264' : t('截图刷新模式', 'Screenshot refresh')}</p></div>
        <div className="flex flex-wrap gap-2">
          <button className={buttonClass} disabled={busy} onClick={() => void refreshSnapshot()}>{t('刷新画面', 'Refresh screen')}</button>
          <button className={buttonClass} disabled={busy} onClick={() => void refreshTree()}>{t('刷新 UI Tree', 'Refresh UI tree')}</button>
          <button className={buttonClass} disabled={busy} onClick={() => { setSelected(null); onElementPicked(null) }}>{t('清除选择', 'Clear selection')}</button>
        </div>
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-4 p-4 xl:grid-cols-[minmax(300px,0.9fr)_minmax(320px,1.1fr)]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <button className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'select' ? 'bg-primary text-white' : 'border border-hairline text-ink-secondary'}`} onClick={() => setMode('select')}>{t('选择控件', 'Select element')}</button>
            <button className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'remote' ? 'bg-primary text-white' : 'border border-hairline text-ink-secondary'}`} onClick={() => setMode('remote')}>{t('远程操作', 'Remote control')}</button>
            <span className="text-[11px] text-ink-faint">{mode === 'select' ? t('点击画面只选择控件，不会操作手机', 'Screen taps select only; they do not operate the phone') : t('点击或滑动将真实操作手机', 'Taps and swipes operate the phone')}</span>
          </div>

          <div ref={screenRef} className="relative mx-auto w-full max-w-[440px] overflow-hidden rounded-xl bg-[#101820]" style={{ aspectRatio: snapshot?.screen_width && snapshot?.screen_height ? `${snapshot.screen_width} / ${snapshot.screen_height}` : '9 / 19.5' }}
            onPointerDown={pointerDown} onPointerUp={pointerUp} style-touch-action="none">
            {videoSource && !videoFailed ? <H264Canvas source={videoSource} onFail={() => setVideoFailed(true)} onVideoSize={(width, height) => setVideoSize({ width, height })} /> :
              snapshot?.screenshot_b64 ? <img alt="Device screenshot" src={`data:image/png;base64,${snapshot.screenshot_b64}`} className="absolute inset-0 h-full w-full object-contain" /> :
              <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-400">{busy ? t('正在获取画面…', 'Loading screen…') : t('等待手机画面', 'Waiting for device screen')}</div>}
            {snapshot?.screen_width && snapshot.screen_height && (snapshot.elements || []).filter(el => el.bounds && el.visible !== false).map(el => {
              const b = el.bounds!
              const active = selected?.index === el.index
              return <div key={el.index} className={`pointer-events-none absolute border ${active ? 'border-amber-400 bg-amber-300/20' : el.clickable ? 'border-emerald-400/60' : 'border-sky-300/25'}`} style={{ left: `${b.left / snapshot.screen_width * 100}%`, top: `${b.top / snapshot.screen_height * 100}%`, width: `${Math.max(0, b.right - b.left) / snapshot.screen_width * 100}%`, height: `${Math.max(0, b.bottom - b.top) / snapshot.screen_height * 100}%` }} />
            })}
            <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/65 px-2 py-1 text-[10px] text-white">{mode === 'select' ? t('控件选择模式', 'Element selection') : t('真机操作模式', 'Device control')}</div>
          </div>
          {videoSize && videoAvailable && <p className="text-center text-[10px] text-ink-faint">Video {videoSize.width} × {videoSize.height}</p>}
          <div className="rounded-lg border border-hairline p-3">
            <div className="mb-2 text-xs font-semibold text-ink">{t('系统启动测试', 'System app launch test')}</div>
            <div className="flex gap-2">
              <input value={packageName} onChange={e => setPackageName(e.target.value)} placeholder="com.example.app" className="min-w-0 flex-1 rounded-md border border-hairline px-2.5 py-2 text-xs outline-none focus:border-primary" />
              <button disabled={busy || !packageName.trim()} onClick={() => void launchPackage()} className="rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{t('启动应用', 'Launch app')}</button>
            </div>
            <p className="mt-1 text-[10px] text-ink-faint">{t('通过现有设备适配器发送 start_app；当前展示启动后的画面，不自动判定前台包名。', 'Uses the existing device adapter. The resulting screen is shown; foreground package verification is not yet implemented.')}</p>
          </div>
        </div>

        <div className="min-w-0 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div><h3 className="text-sm font-semibold text-ink">{t('UI 元素树', 'UI element tree')}</h3><p className="text-[11px] text-ink-faint">{snapshot?.elements.length || 0} {t('个元素', 'elements')}</p></div>
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder={t('搜索文本 / ID / 类名', 'Search text / ID / class')} className="w-48 max-w-[55%] rounded-md border border-hairline px-2.5 py-2 text-xs outline-none focus:border-primary" />
          </div>
          {selected && (
            <div className="rounded-lg border border-primary/30 bg-primary-soft p-3">
              <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-primary-deep">{t('当前选中控件', 'Selected element')}</span><button className="text-[11px] text-primary-deep underline" onClick={() => void tapSelected()} disabled={busy || selected.enabled === false || selected.visible === false}>{t('测试点击', 'Test tap')}</button></div>
              <div className="mt-2 space-y-1 text-[11px] text-ink-secondary">
                <div>Text: {selected.text || '—'}</div><div>Resource ID: {selected.resourceId || '—'}</div>
                <div>Content description: {selected.contentDescription || '—'}</div><div>Class: {selected.className || '—'}</div>
                <div>{t('能力', 'Capabilities')}: {selected.clickable ? t('系统可点击', 'Clickable') : selected.tap_candidate ? t('点击候选', 'Tap candidate') : t('未确认可点击', 'Not confirmed clickable')} · {selected.enabled === false ? t('已禁用', 'Disabled') : t('已启用', 'Enabled')}</div>
                <div>{t('父级路径', 'Parent path')}: {(selected.parentPath || []).join(' / ') || '—'}</div>
                {selected.bounds && <div>Bounds: [{selected.bounds.left}, {selected.bounds.top}, {selected.bounds.right}, {selected.bounds.bottom}]</div>}
              </div>
            </div>
          )}
          <div className="max-h-[520px] overflow-auto rounded-lg border border-hairline">
            {filteredElements.map(el => {
              const active = selected?.index === el.index
              const label = el.text || el.contentDescription || el.resourceId || el.className || `Element #${el.index}`
              return <button key={el.index} onClick={() => selectElement(el)} className={`block w-full border-b border-hairline px-3 py-2.5 text-left last:border-0 ${active ? 'bg-primary-soft' : 'bg-white hover:bg-canvas-soft'}`}>
                <div className="flex items-center gap-2"><span className="w-7 shrink-0 text-[10px] tabular-nums text-ink-faint">#{el.index}</span><span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">{label}</span><span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] ${el.clickable ? 'bg-emerald-50 text-emerald-700' : el.tap_candidate ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-500'}`}>{el.clickable ? 'CLICK' : el.tap_candidate ? 'CANDIDATE' : 'VIEW'}</span></div>
                <div className="ml-9 mt-1 truncate text-[10px] text-ink-faint">{el.resourceId || el.className || '—'}{el.parentPath?.length ? ` · ${el.parentPath.join(' / ')}` : ''}</div>
              </button>
            })}
            {!filteredElements.length && <div className="p-6 text-center text-xs text-ink-faint">{t('没有匹配的 UI 元素', 'No matching UI elements')}</div>}
          </div>
          <div className="rounded-lg bg-canvas-soft p-3">
            <div className="mb-1 text-xs font-semibold text-ink">{t('页面文本摘要', 'Page text')}</div>
            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-4 text-ink-mute">{snapshot?.ui_text || t('暂无页面文本', 'No page text')}</pre>
          </div>
        </div>
      </div>
      {(error || notice) && <div className={`border-t px-4 py-2.5 text-xs ${error ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{error || notice}</div>}
    </section>
  )
}
