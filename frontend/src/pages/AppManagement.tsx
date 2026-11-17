import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchDevices, fetchDeviceApps, updateDeviceApp, launchDeviceApp, type DeviceApp } from '../lib/api'

const inputClass = 'w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/10'

export default function AppManagement() {
  const qc = useQueryClient()
  const [deviceId, setDeviceId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [filter, setFilter] = useState<'all' | 'marked' | 'unmarked'>('all')
  const [editing, setEditing] = useState<DeviceApp | null>(null)
  const [draftName, setDraftName] = useState('')
  const [notice, setNotice] = useState('')
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const androidDevices = devices.filter(d => d.connection_type === 'adb_usb' && d.platform === 'android')
  const activeDevice = androidDevices.find(d => d.id === deviceId) || androidDevices.find(d => d.status === 'online') || androidDevices[0]
  const activeId = activeDevice?.id || ''
  const { data: apps = [], isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ['device-apps', activeId], queryFn: () => fetchDeviceApps(activeId),
    enabled: !!activeId && activeDevice?.status === 'online', staleTime: 15000, retry: false,
  })
  const saveMutation = useMutation({
    mutationFn: ({ app, app_name, is_test_app }: { app: DeviceApp; app_name: string; is_test_app: boolean }) =>
      updateDeviceApp(activeId, app.package_name, { app_name, is_test_app }),
    onSuccess: async () => { setEditing(null); setNotice('应用信息已保存'); await qc.invalidateQueries({ queryKey: ['device-apps', activeId] }) },
    onError: (error: any) => setNotice(error?.response?.data?.detail || '保存失败，请重试'),
  })
  const launchMutation = useMutation({
    mutationFn: (app: DeviceApp) => launchDeviceApp(activeId, app.package_name),
    onSuccess: (_data, app) => setNotice('已发送启动请求：' + (app.app_name || app.package_name)),
    onError: (error: any) => setNotice(error?.response?.data?.detail || '应用启动失败'),
  })
  const markedCount = apps.filter(app => app.is_test_app && app.installed).length
  const filteredApps = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    return apps.filter(app => {
      if (filter === 'marked' && !app.is_test_app) return false
      if (filter === 'unmarked' && app.is_test_app) return false
      return !q || app.package_name.toLowerCase().includes(q) || app.app_name.toLowerCase().includes(q)
    }).sort((a, b) => Number(b.is_test_app) - Number(a.is_test_app) || (a.app_name || a.package_name).localeCompare(b.app_name || b.package_name))
  }, [apps, filter, keyword])
  const beginEdit = (app: DeviceApp) => { setEditing(app); setDraftName(app.app_name || '') }
  const save = (app: DeviceApp, is_test_app = app.is_test_app) => saveMutation.mutate({ app, app_name: draftName.trim(), is_test_app })
  const toggleTestApp = (app: DeviceApp) => saveMutation.mutate({ app, app_name: app.app_name, is_test_app: !app.is_test_app })

  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">DEVICE APP CATALOG</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">设备应用库</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-mute">发现设备上的可启动应用，确认应用身份并标记为测试应用，供执行步骤统一调用。</p></div>
      <button onClick={() => void refetch()} disabled={!activeId || isFetching} className="rounded-lg border border-hairline bg-white px-4 py-2.5 text-sm font-medium text-ink hover:bg-canvas-cool disabled:opacity-50">{isFetching ? '正在刷新…' : '↻ 刷新应用'}</button>
    </header>
    <section className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><div className="text-sm font-semibold text-ink">当前设备</div><div className="mt-1 text-xs text-ink-mute">应用清单按设备维护，标记不会因浏览器或电脑更换而丢失。</div></div>
        <select className="min-w-[260px] rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm" value={activeId} onChange={e => { setDeviceId(e.target.value); setNotice('') }}>
          {androidDevices.length === 0 && <option value="">暂无本机 ADB Android 设备</option>}
          {androidDevices.map(d => <option key={d.id} value={d.id}>{d.name} · {d.status === 'online' ? '在线' : '离线'}</option>)}
        </select>
      </div>
    </section>
    <section className="grid gap-3 sm:grid-cols-3">
      {[{ label: '发现应用', value: apps.length, note: '设备可启动应用' }, { label: '测试应用', value: markedCount, note: '可在执行步骤中选择' }, { label: '待标记', value: apps.length - markedCount, note: '尚未加入测试应用库' }].map(item => <div key={item.label} className="rounded-xl border border-hairline bg-white p-4 shadow-sm"><div className="text-xs text-ink-mute">{item.label}</div><div className="mt-2 text-2xl font-semibold tabular-nums text-ink">{item.value}</div><div className="mt-1 text-[11px] text-ink-faint">{item.note}</div></div>)}
    </section>
    <section className="overflow-hidden rounded-xl border border-hairline bg-white shadow-sm">
      <div className="border-b border-hairline p-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-ink">应用清单</h2><p className="mt-1 text-xs text-ink-mute">先打开应用确认身份，再填写易识别的名称并加入测试应用。</p></div><span className="text-xs text-ink-mute">{filteredApps.length} / {apps.length} 个应用</span></div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input className={inputClass} value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索应用名称或包名，例如 com.tencent.mm" />
          <div className="flex shrink-0 rounded-lg border border-hairline bg-canvas-soft p-1">{([{ value: 'all', label: '全部' }, { value: 'marked', label: '测试应用' }, { value: 'unmarked', label: '待标记' }] as const).map(item => <button key={item.value} onClick={() => setFilter(item.value)} className={'rounded-md px-3 py-2 text-xs font-medium transition ' + (filter === item.value ? 'bg-white text-primary shadow-sm' : 'text-ink-mute hover:text-ink')}>{item.label}</button>)}</div>
        </div>
      </div>
      {!activeDevice ? <div className="p-12 text-center"><div className="text-sm font-medium text-ink">没有可管理的 Android ADB 设备</div><p className="mt-2 text-xs text-ink-mute">请先在设备管理中通过 USB 连接 Android 手机。</p></div>
        : activeDevice.status !== 'online' ? <div className="p-12 text-center text-sm text-ink-mute">当前设备离线，请重新连接后刷新应用清单。</div>
        : isLoading ? <div className="p-12 text-center text-sm text-ink-mute">正在读取设备应用…</div>
        : isError ? <div className="p-12 text-center"><p className="text-sm text-red-600">读取应用失败，请确认 ADB 授权和设备连接状态。</p><button onClick={() => void refetch()} className="mt-3 rounded-lg border px-3 py-2 text-xs">重试</button></div>
        : filteredApps.length === 0 ? <div className="p-12 text-center text-sm text-ink-mute">没有符合条件的应用。</div>
        : <div className="divide-y divide-hairline">{filteredApps.map(app => <div key={app.package_name} className="flex flex-wrap items-center gap-3 px-4 py-3.5 transition hover:bg-canvas-soft/60">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-hairline bg-canvas-soft text-sm font-semibold uppercase text-ink-mute">{(app.app_name || app.package_name.split('.').filter(Boolean).pop() || 'A').slice(0, 2)}</div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="truncate text-sm font-semibold text-ink">{app.app_name || '未识别应用'}</span>{app.is_test_app && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">测试应用</span>}{!app.installed && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">设备已卸载</span>}</div>
            <div className="mt-1 break-all font-mono text-[11px] text-ink-faint">{app.package_name}</div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button onClick={() => launchMutation.mutate(app)} disabled={!app.installed || launchMutation.isPending} className="rounded-lg border border-primary/20 bg-primary-soft px-3 py-2 text-xs font-medium text-primary-deep hover:bg-primary/10 disabled:opacity-40">{launchMutation.isPending && launchMutation.variables?.package_name === app.package_name ? '启动中…' : '打开'}</button>
            <button onClick={() => beginEdit(app)} className="rounded-lg border border-hairline bg-white px-3 py-2 text-xs font-medium text-ink hover:bg-canvas-cool">{app.app_name ? '编辑名称' : '识别名称'}</button>
            <button onClick={() => toggleTestApp(app)} disabled={saveMutation.isPending} className={app.is_test_app ? 'rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700' : 'rounded-lg border border-hairline bg-white px-3 py-2 text-xs font-medium text-ink-mute hover:border-primary/40 hover:text-primary'}>{app.is_test_app ? '已加入测试' : '加入测试'}</button>
          </div>
          {editing?.package_name === app.package_name && <div className="basis-full rounded-lg border border-primary/20 bg-primary-soft/30 p-3">
            <div className="mb-2 text-xs font-semibold text-ink">确认应用名称</div>
            <div className="flex flex-col gap-2 sm:flex-row"><input autoFocus className={inputClass} value={draftName} onChange={e => setDraftName(e.target.value)} placeholder="例如：微信、淘宝、系统设置" onKeyDown={e => { if (e.key === 'Enter') save(app); if (e.key === 'Escape') setEditing(null) }} /><button onClick={() => save(app)} disabled={saveMutation.isPending || !draftName.trim()} className="rounded-lg bg-primary px-4 py-2 text-xs font-medium text-white disabled:opacity-40">保存名称</button><button onClick={() => setEditing(null)} className="rounded-lg border border-hairline bg-white px-4 py-2 text-xs">取消</button></div>
            <p className="mt-2 text-[11px] text-ink-mute">包名是执行时使用的唯一标识；名称仅用于界面展示和步骤选择。</p>
          </div>}
        </div>)}</div>}
    </section>
    {notice && <div className="rounded-lg border border-hairline bg-white px-4 py-3 text-sm text-ink-mute">{notice}</div>}
  </div>
}
