import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Device, Run, fetchDevices, fetchRuns, fetchSuites } from '../lib/api'

function MetricCard({ label, value, hint, tone = 'blue' }: { label: string; value: string | number; hint: string; tone?: 'blue' | 'green' | 'amber' | 'red' }) {
  const tones = {
    blue: 'bg-sky-50 text-sky-700 border-sky-100',
    green: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    amber: 'bg-amber-50 text-amber-700 border-amber-100',
    red: 'bg-rose-50 text-rose-700 border-rose-100',
  }
  return <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm">
    <div className="flex items-start justify-between gap-3">
      <div className="text-sm text-ink-mute">{label}</div>
      <span className={`rounded-md border px-2 py-1 text-[11px] font-medium ${tones[tone]}`}>{hint}</span>
    </div>
    <div className="mt-3 text-3xl font-semibold tracking-tight tabular-nums text-ink">{value}</div>
  </div>
}

function StatusPill({ status }: { status: string }) {
  const running = ['running', 'queued', 'pending'].includes(status)
  const failed = ['failed', 'error', 'errored'].includes(status)
  const passed = ['passed', 'success', 'completed', 'done'].includes(status)
  const cls = running ? 'bg-sky-50 text-sky-700' : failed ? 'bg-rose-50 text-rose-700' : passed ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'
  const label = running ? '执行中' : failed ? '失败' : passed ? '已完成' : status || '未知'
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${cls}`}>{label}</span>
}

function timeText(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function deviceKind(device: Device) {
  if (device.platform === 'harmony' || device.connection_type === 'hdc_bridge') return 'HarmonyOS'
  if (device.platform === 'ios' || device.connection_type === 'ios_bridge') return 'iOS'
  return 'Android'
}

export default function Workbench() {
  const devicesQuery = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: fetchRuns, refetchInterval: 15000 })
  const suitesQuery = useQuery({ queryKey: ['suites', 'workbench'], queryFn: () => fetchSuites({ page: 1, page_size: 100 }), refetchInterval: 60000 })
  const devices = devicesQuery.data || []
  const runs = runsQuery.data || []
  const suites = suitesQuery.data?.items || []
  const online = devices.filter(device => device.status === 'online')
  const runningRuns = runs.filter(run => ['running', 'queued', 'pending'].includes(run.status))
  const recentRuns = useMemo(() => [...runs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0, 7), [runs])
  const completed = runs.filter(run => !['running', 'queued', 'pending'].includes(run.status))
  const passTotal = completed.reduce((sum, run) => sum + (run.passed || 0), 0)
  const caseTotal = completed.reduce((sum, run) => sum + (run.total || 0), 0)
  const passRate = caseTotal ? Math.round(passTotal / caseTotal * 100) : 0
  const androidCount = devices.filter(device => deviceKind(device) === 'Android').length
  const harmonyCount = devices.filter(device => deviceKind(device) === 'HarmonyOS').length
  const iosCount = devices.filter(device => deviceKind(device) === 'iOS').length
  const failedRecent = runs.filter(run => ['failed', 'error', 'errored'].includes(run.status)).length
  const loading = devicesQuery.isLoading || runsQuery.isLoading

  return <div className="space-y-7">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">SMART DEVICE TESTING PLATFORM</div>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">测试工作台</h1>
        <p className="mt-2 text-sm text-ink-mute">统一查看设备接入、Agent 调试与自动化测试运行状态。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link to="/debug" className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-deep">进入调试控制台 <span aria-hidden="true">↗</span></Link>
        <Link to="/quick" className="rounded-lg border border-hairline bg-white px-4 py-2.5 text-sm font-medium text-ink hover:bg-canvas-cool">创建测试任务</Link>
      </div>
    </header>

    {loading && <div className="rounded-lg border border-hairline bg-white px-4 py-3 text-sm text-ink-mute">正在读取平台数据…</div>}
    {(devicesQuery.isError || runsQuery.isError || suitesQuery.isError) && <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">部分工作台数据暂时无法读取，请检查后端连接或刷新页面。</div>}

    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="接入设备" value={devices.length} hint={`在线 ${online.length}`} tone="blue" />
      <MetricCard label="在线设备" value={online.length} hint={devices.length ? `离线 ${devices.length - online.length}` : '等待接入'} tone="green" />
      <MetricCard label="运行中任务" value={runningRuns.length} hint="实时更新" tone={runningRuns.length ? 'amber' : 'blue'} />
      <MetricCard label="用例套件" value={suitesQuery.isLoading ? '—' : suites.length} hint="已纳入管理" tone="blue" />
    </section>

    <section className="grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.85fr)]">
      <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between gap-3">
          <div><h2 className="font-semibold text-ink">设备接入概况</h2><p className="mt-1 text-xs text-ink-mute">按设备平台分类，状态每 5 秒刷新</p></div>
          <Link to="/devices" className="text-sm font-medium text-primary hover:underline">设备管理 →</Link>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          {[{ label: 'Android', count: androidCount, dot: 'bg-emerald-500' }, { label: 'HarmonyOS', count: harmonyCount, dot: 'bg-sky-500' }, { label: 'iOS', count: iosCount, dot: 'bg-violet-500' }].map(item =>
            <div key={item.label} className="rounded-lg bg-canvas-soft p-4">
              <div className="flex items-center gap-2 text-sm text-ink-mute"><span className={`h-2 w-2 rounded-full ${item.dot}`} />{item.label}</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">{item.count}<span className="ml-1 text-xs font-normal text-ink-mute">台</span></div>
            </div>
          )}
        </div>
        <div className="mt-5 space-y-2">
          {devices.slice(0, 6).map(device => <div key={device.id} className="flex items-center gap-3 rounded-lg border border-hairline px-3 py-2.5">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${device.status === 'online' ? 'bg-emerald-500' : 'bg-slate-300'}`} />
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{device.name}</div><div className="mt-0.5 text-xs text-ink-mute">{deviceKind(device)} · {device.connection_type}</div></div>
            <span className={`text-xs ${device.status === 'online' ? 'text-emerald-700' : 'text-ink-mute'}`}>{device.status === 'online' ? '在线' : '离线'}</span>
          </div>)}
          {!devices.length && !devicesQuery.isLoading && <div className="rounded-lg border border-dashed border-hairline p-6 text-center"><div className="text-sm font-medium">还没有接入设备</div><p className="mt-1 text-xs text-ink-mute">连接 Android 或 HarmonyOS 设备后，设备状态会显示在这里。</p><Link to="/devices" className="mt-3 inline-block text-sm text-primary">前往设备管理 →</Link></div>}
          {devices.length > 6 && <div className="text-right text-xs text-ink-mute">另有 {devices.length - 6} 台设备 · <Link to="/devices" className="text-primary">查看全部</Link></div>}
        </div>
      </div>

      <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between"><div><h2 className="font-semibold">测试质量概览</h2><p className="mt-1 text-xs text-ink-mute">基于已结束的测试运行记录</p></div><Link to="/runs" className="text-sm text-primary hover:underline">运行结果 →</Link></div>
        <div className="mt-6 flex items-end gap-3"><div className="text-4xl font-semibold tabular-nums">{caseTotal ? `${passRate}%` : '—'}</div><div className="pb-1 text-xs text-ink-mute">用例通过率</div></div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${passRate}%` }} /></div>
        <div className="mt-4 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg bg-emerald-50 p-3"><div className="text-xl font-semibold text-emerald-700">{passTotal}</div><div className="mt-1 text-[11px] text-emerald-700">通过</div></div>
          <div className="rounded-lg bg-rose-50 p-3"><div className="text-xl font-semibold text-rose-700">{completed.reduce((sum, run) => sum + (run.failed || 0), 0)}</div><div className="mt-1 text-[11px] text-rose-700">失败</div></div>
          <div className="rounded-lg bg-amber-50 p-3"><div className="text-xl font-semibold text-amber-700">{completed.reduce((sum, run) => sum + (run.errored || 0), 0)}</div><div className="mt-1 text-[11px] text-amber-700">异常</div></div>
        </div>
        <p className="mt-4 text-xs text-ink-mute">累计统计 {completed.length} 次已结束运行 · {caseTotal} 个用例结果</p>
      </div>
    </section>

    <section className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.8fr)]">
      <div className="overflow-hidden rounded-xl border border-hairline bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-hairline px-5 py-4"><div><h2 className="font-semibold">最近测试任务</h2><p className="mt-1 text-xs text-ink-mute">最近 7 条运行记录</p></div><Link to="/runs" className="text-sm text-primary hover:underline">全部记录 →</Link></div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-sm">
            <thead className="bg-canvas-soft text-xs text-ink-mute"><tr><th className="px-5 py-3 font-medium">任务 / 套件</th><th className="px-3 py-3 font-medium">设备</th><th className="px-3 py-3 font-medium">结果</th><th className="px-3 py-3 font-medium">用例统计</th><th className="px-5 py-3 font-medium">创建时间</th></tr></thead>
            <tbody className="divide-y divide-hairline">
              {recentRuns.map((run: Run) => <tr key={run.id} className="hover:bg-canvas-soft/70"><td className="px-5 py-3"><Link to={`/runs/${run.id}`} className="font-medium text-ink hover:text-primary">{run.suite_name || '快速任务'}</Link><div className="mt-1 max-w-[240px] truncate font-mono text-[10px] text-ink-mute">{run.id}</div></td><td className="px-3 py-3 text-xs text-ink-mute">{devices.find(d => d.id === run.device_id)?.name || run.device_id.slice(0, 8)}</td><td className="px-3 py-3"><StatusPill status={run.status} /></td><td className="px-3 py-3 text-xs tabular-nums"><span className="text-emerald-700">{run.passed} 通过</span><span className="mx-1 text-ink-faint">/</span><span className="text-rose-700">{run.failed} 失败</span><span className="mx-1 text-ink-faint">/</span><span className="text-amber-700">{run.errored} 异常</span></td><td className="px-5 py-3 whitespace-nowrap text-xs text-ink-mute">{timeText(run.created_at)}</td></tr>)}
              {!recentRuns.length && <tr><td colSpan={5} className="px-5 py-10 text-center text-sm text-ink-mute">暂无测试记录，创建一个任务后将在这里显示。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <div className="space-y-5">
        <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between"><h2 className="font-semibold">异常与告警</h2><span className="text-xs text-ink-mute">实时汇总</span></div>
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-3"><span className="grid h-8 w-8 place-items-center rounded-lg bg-amber-50 text-amber-700">!</span><div className="min-w-0 flex-1"><div className="text-sm font-medium">离线设备</div><div className="text-xs text-ink-mute">需要检查连接或设备端 Agent</div></div><div className="text-lg font-semibold tabular-nums">{devices.length - online.length}</div></div>
            <div className="flex items-center gap-3"><span className="grid h-8 w-8 place-items-center rounded-lg bg-rose-50 text-rose-700">×</span><div className="min-w-0 flex-1"><div className="text-sm font-medium">失败任务</div><div className="text-xs text-ink-mute">当前返回的运行记录中失败状态</div></div><div className="text-lg font-semibold tabular-nums">{failedRecent}</div></div>
          </div>
          <p className="mt-4 border-t border-hairline pt-3 text-[11px] leading-5 text-ink-mute">告警目前由设备在线状态和测试运行结果汇总生成；后续可扩展为独立告警规则与通知中心。</p>
        </div>
        <div className="rounded-xl border border-primary/15 bg-primary-soft/50 p-5">
          <div className="text-xs font-semibold tracking-wide text-primary-deep">快速入口</div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Link to="/debug" className="rounded-lg border border-primary/15 bg-white px-3 py-3 text-sm font-medium hover:border-primary">⌘ Agent 调试</Link>
            <Link to="/devices" className="rounded-lg border border-primary/15 bg-white px-3 py-3 text-sm font-medium hover:border-primary">▣ 设备管理</Link>
            <Link to="/suites" className="rounded-lg border border-primary/15 bg-white px-3 py-3 text-sm font-medium hover:border-primary">◇ 用例中心</Link>
            <Link to="/quick" className="rounded-lg border border-primary/15 bg-white px-3 py-3 text-sm font-medium hover:border-primary">＋ 新建任务</Link>
          </div>
        </div>
      </div>
    </section>
  </div>
}
