import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchDevices, fetchDeviceApps, updateDeviceApp, launchDeviceApp, type DeviceApp } from '../lib/api'

const inputClass = 'w-full rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10'

export default function AppManagement() {
  const qc = useQueryClient()
  const [deviceId, setDeviceId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [filter, setFilter] = useState<'all' | 'marked' | 'unmarked'>('all')
  const [editing, setEditing] = useState<DeviceApp | null>(null)
  const [draftName, setDraftName] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
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
  const totalPages = Math.max(1, Math.ceil(filteredApps.length / pageSize))
  const pagedApps = filteredApps.slice((page - 1) * pageSize, page * pageSize)
  useEffect(() => { setPage(1) }, [keyword, filter, pageSize, activeId])
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [page, totalPages])
  const beginEdit = (app: DeviceApp) => { setEditing(app); setDraftName(app.app_name || '') }
  const save = (app: DeviceApp, is_test_app = app.is_test_app) => saveMutation.mutate({ app, app_name: draftName.trim(), is_test_app })
  const toggleTestApp = (app: DeviceApp) => saveMutation.mutate({ app, app_name: app.app_name, is_test_app: !app.is_test_app })

  return <div className="mx-auto max-w-[1600px] space-y-6">
    <header className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-5 py-5 text-white shadow-[0_18px_45px_rgba(37,99,235,0.16)] sm:px-6">
      <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" /><div className="relative"><div className="mb-3 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-blue-50">AI 终端 · 应用管理</div>
        <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">设备应用库</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-50/90">发现设备上的可启动应用，确认应用身份并标记为测试应用，供执行步骤统一调用。</p></div>
      <button onClick={() => void refetch()} disabled={!activeId || isFetching} className="relative rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/20 disabled:opacity-50">{isFetching ? '正在刷新…' : '↻ 刷新应用'}</button>
    </header>
    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)]">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div><div className="text-sm font-semibold text-slate-800">当前设备</div><div className="mt-1 text-xs leading-5 text-slate-500">应用名称和测试标记按账号共享；安装状态按设备独立记录。</div></div>
        <select className="min-w-[260px] rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={activeId} onChange={e => { setDeviceId(e.target.value); setNotice('') }}>
          {androidDevices.length === 0 && <option value="">暂无本机 ADB Android 设备</option>}
          {androidDevices.map(d => <option key={d.id} value={d.id}>{d.name} · {d.status === 'online' ? '在线' : '离线'}</option>)}
        </select>
      </div>
    </section>
    <section className="grid gap-4 sm:grid-cols-3">
      {[{ label: '发现应用', value: apps.length, note: '设备可启动应用' }, { label: '测试应用', value: markedCount, note: '可在执行步骤中选择' }, { label: '待标记', value: apps.length - markedCount, note: '尚未加入测试应用库' }].map(item => <div key={item.label} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)]"><div className="text-xs text-ink-mute">{item.label}</div><div className="mt-2 text-2xl font-semibold tabular-nums text-ink">{item.value}</div><div className="mt-1 text-[11px] text-ink-faint">{item.note}</div></div>)}
    </section>
    <section className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_10px_32px_rgba(31,75,135,0.06)]">
      <div className="border-b border-slate-100 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-slate-800">应用清单</h2><p className="mt-1 text-xs text-ink-mute">先打开应用确认身份，再填写易识别的名称并加入测试应用。</p></div><span className="text-xs text-ink-mute">{filteredApps.length} / {apps.length} 个应用</span></div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input className={inputClass} value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索应用名称或包名，例如 com.tencent.mm" />
          <div className="flex shrink-0 rounded-xl border border-slate-200 bg-slate-50 p-1">{([{ value: 'all', label: '全部' }, { value: 'marked', label: '测试应用' }, { value: 'unmarked', label: '待标记' }] as const).map(item => <button key={item.value} onClick={() => setFilter(item.value)} className={'rounded-lg px-3 py-2 text-xs font-semibold transition ' + (filter === item.value ? 'bg-white text-primary shadow-sm' : 'text-ink-mute hover:text-ink')}>{item.label}</button>)}</div>
        </div>
      </div>
      {!activeDevice ? <div className="p-12 text-center"><div className="text-sm font-medium text-ink">没有可管理的 Android ADB 设备</div><p className="mt-2 text-xs text-ink-mute">请先在设备管理中通过 USB 连接 Android 手机。</p></div>
        : activeDevice.status !== 'online' ? <div className="p-12 text-center text-sm text-ink-mute">当前设备离线，请重新连接后刷新应用清单。</div>
        : isLoading ? <div className="p-12 text-center text-sm text-ink-mute">正在读取设备应用…</div>
        : isError ? <div className="p-12 text-center"><p className="text-sm text-red-600">读取应用失败，请确认 ADB 授权和设备连接状态。</p><button onClick={() => void refetch()} className="mt-3 rounded-lg border px-3 py-2 text-xs">重试</button></div>
        : filteredApps.length === 0 ? <div className="p-12 text-center text-sm text-ink-mute">没有符合条件的应用。</div>
        : <div className="divide-y divide-slate-100">{pagedApps.map(app => <div key={app.package_name} className="flex flex-wrap items-center gap-4 px-5 py-4 transition hover:bg-blue-50/40">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 to-cyan-50 text-sm font-bold uppercase text-blue-700">{(app.app_name || app.package_name.split('.').filter(Boolean).pop() || 'A').slice(0, 2)}</div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="truncate text-sm font-semibold text-ink">{app.app_name || '未识别应用'}</span>{app.is_test_app && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">测试应用</span>}{!app.installed && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">设备已卸载</span>}</div>
            <div className="mt-1 break-all font-mono text-[11px] text-ink-faint">{app.package_name}</div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button onClick={() => launchMutation.mutate(app)} disabled={!app.installed || launchMutation.isPending} className="rounded-xl border border-blue-200 bg-blue-50 px-3.5 py-2 text-xs font-semibold text-blue-700 transition hover:bg-blue-100 disabled:opacity-40">{launchMutation.isPending && launchMutation.variables?.package_name === app.package_name ? '启动中…' : '打开'}</button>
            <button onClick={() => beginEdit(app)} className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-600 transition hover:border-blue-200 hover:bg-blue-50">{app.app_name ? '编辑名称' : '识别名称'}</button>
            <button onClick={() => app.app_name ? toggleTestApp(app) : beginEdit(app)} disabled={saveMutation.isPending} className={app.is_test_app ? 'rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-xs font-semibold text-emerald-700' : 'rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-500 transition hover:border-blue-200 hover:text-blue-700'}>{app.is_test_app ? '已加入测试' : app.app_name ? '加入测试' : '先识别名称'}</button>
          </div>
          {editing?.package_name === app.package_name && <div className="basis-full rounded-xl border border-blue-100 bg-blue-50/60 p-4">
            <div className="mb-2 text-sm font-semibold text-slate-800">确认应用名称</div>
            <div className="flex flex-col gap-2 sm:flex-row"><input autoFocus className={inputClass} value={draftName} onChange={e => setDraftName(e.target.value)} placeholder="例如：微信、淘宝、系统设置" onKeyDown={e => { if (e.key === 'Enter') save(app); if (e.key === 'Escape') setEditing(null) }} /><button onClick={() => save(app)} disabled={saveMutation.isPending || !draftName.trim()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-40">保存名称</button><button onClick={() => setEditing(null)} className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-medium text-slate-600">取消</button></div>
            <p className="mt-2 text-[11px] text-ink-mute">包名是执行时使用的唯一标识；名称仅用于界面展示和步骤选择。</p>
          </div>}
        </div>)}</div>}
      {filteredApps.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-4">
        <div className="text-xs text-ink-mute">显示 {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, filteredApps.length)} 条，共 {filteredApps.length} 条</div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-ink-mute">每页</label>
          <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600" value={pageSize} onChange={e => setPageSize(Number(e.target.value))}>
            {[10, 20, 50, 100].map(size => <option key={size} value={size}>{size} 条</option>)}
          </select>
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 disabled:opacity-40">上一页</button>
          <span className="min-w-[64px] text-center text-xs tabular-nums text-ink-mute">{page} / {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="rounded-md border border-hairline px-3 py-1.5 text-xs disabled:opacity-40">下一页</button>
        </div>
      </div>}
    </section>
    {notice && <div className="rounded-xl border border-blue-100 bg-blue-50/70 px-4 py-3 text-sm text-blue-700">{notice}</div>}
  </div>
}
