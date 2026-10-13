import { useState } from 'react'
import { Device } from '../lib/api'
import LivePanel, { ScreenshotView } from './LivePanel'
import { useT } from '../lib/i18n'

type Layout = 'auto' | '2' | '3' | '4'
const gridClasses: Record<Layout, string> = {
  auto: 'grid-cols-[repeat(auto-fill,minmax(140px,1fr))]',
  '2': 'grid-cols-2',
  '3': 'grid-cols-2 sm:grid-cols-3',
  '4': 'grid-cols-2 sm:grid-cols-3 xl:grid-cols-4',
}

export default function DeviceWall({ devices, selectedId, onSelect }: {
  devices: Device[]; selectedId: string | null; onSelect: (id: string) => void
}) {
  const t = useT()
  const [layout, setLayout] = useState<Layout>(() => {
    const saved = localStorage.getItem('device-wall-layout')
    return saved && saved in gridClasses ? saved as Layout : 'auto'
  })
  const [showOffline, setShowOffline] = useState(false)
  const online = devices.filter(d => d.status === 'online')
  const shown = showOffline ? devices : online
  const selected = devices.find(d => d.id === selectedId) || null

  return <section className="mb-8" aria-label={t('设备屏幕墙', 'Device screen wall')}>
    <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div>
        <h1 className="text-2xl font-bold">{t('设备总览', 'Device overview')}</h1>
        <p className="text-sm text-ink-mute mt-1">{t('在线', 'Online')} {online.length} / {devices.length} · {t('小屏默认截图流，点击设备切换右侧预览', 'Thumbnails show screenshots. Select a device for the large preview.')}</p>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={showOffline} onChange={e => setShowOffline(e.target.checked)} />{t('显示离线设备', 'Show offline devices')}</label>
        <label className="flex items-center gap-2">{t('排列', 'Layout')}
          <select aria-label={t('屏幕排列', 'Screen layout')} value={layout} className="border rounded px-2 py-1.5 bg-white" onChange={e => {
            const value = e.target.value as Layout
            setLayout(value); localStorage.setItem('device-wall-layout', value)
          }}>
            <option value="auto">{t('自适应', 'Auto')}</option>
            <option value="2">{t('每行 2 屏', '2 per row')}</option>
            <option value="3">{t('每行 3 屏', '3 per row')}</option>
            <option value="4">{t('每行 4 屏', '4 per row')}</option>
          </select>
        </label>
      </div>
    </div>
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px] items-start">
      <div className={`grid gap-3 ${gridClasses[layout]}`}>
        {shown.map(d => <button key={d.id} type="button" aria-label={`${t('预览设备', 'Preview device')} ${d.name}`} aria-pressed={d.id === selectedId}
          onClick={() => onSelect(d.id)} className={`min-w-0 bg-white border rounded-xl p-3 text-left transition-colors ${d.id === selectedId ? 'ring-2 ring-primary border-primary' : 'hover:border-primary'}`}>
          <div className="relative bg-terminal rounded-lg overflow-hidden mx-auto" style={{ aspectRatio: '9 / 19.5', maxWidth: 160 }}>
            {d.status === 'online' ? <ScreenshotView deviceId={d.id} source="screenshot" />
              : <span className="absolute inset-0 flex items-center justify-center text-xs text-terminal-mute">{t('设备离线', 'Offline')}</span>}
          </div>
          <div className="flex items-center gap-2 mt-3"><span className={`w-2 h-2 rounded-full shrink-0 ${d.status === 'online' ? 'bg-ok' : 'bg-gray-300'}`} /><span className="text-sm font-medium truncate" title={d.name}>{d.name}</span></div>
          <p className="text-xs text-ink-mute mt-1">{d.status === 'online' ? t('截图流', 'Screenshots') : t('离线', 'Offline')}{d.id === selectedId ? ` · ${t('已选中', 'Selected')}` : ''}</p>
        </button>)}
        {!shown.length && <div className="col-span-full bg-white border border-dashed rounded-xl p-10 text-center text-sm text-ink-mute">{t('暂无在线设备，请在下方连接手机。', 'No online devices. Connect a phone below.')}</div>}
      </div>
      <LivePanel device={selected} defaultMode="screenshot" storageKey="device-wall-live-mode" />
    </div>
  </section>
}
