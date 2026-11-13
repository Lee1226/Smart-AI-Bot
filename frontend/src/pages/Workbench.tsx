import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Device, Run, fetchDevices, fetchRuns, fetchSuites } from '../lib/api'

type Tone = 'blue' | 'cyan' | 'green' | 'orange'
function MetricCard({ label, value, hint, tone = 'blue', icon }: { label: string; value: string | number; hint: string; tone?: Tone; icon: string }) {
  const styles: Record<Tone, { card: string; icon: string; value: string; hint: string }> = {
    blue: { card: 'surface-card-blue', icon: 'icon-tile-blue', value: 'text-[#205F9F]', hint: 'text-[#477BAE]' },
    cyan: { card: 'surface-card-cyan', icon: 'icon-tile-cyan', value: 'text-[#117F89]', hint: 'text-[#438B8C]' },
    green: { card: 'surface-card-green', icon: 'icon-tile-green', value: 'text-[#187A59]', hint: 'text-[#47866B]' },
    orange: { card: 'surface-card-orange', icon: 'icon-tile-orange', value: 'text-[#A96D20]', hint: 'text-[#A77D48]' },
  }
  const style = styles[tone]
  return <article className={`${style.card} surface-card-hover min-h-[142px] p-5`}>
    <div className="flex items-start justify-between gap-3">
      <div className="text-[13px] font-semibold text-ink-secondary">{label}</div>
      <span className={`grid h-10 w-10 place-items-center ${style.icon}`}><MetricIcon name={icon} /></span>
    </div>
    <div className={`mt-3 text-[32px] font-bold leading-none tracking-tight tabular-nums ${style.value}`}>{value}</div>
    <div className={`mt-3 text-[11px] font-medium ${style.hint}`}>{hint}</div>
  </article>
}
function MetricIcon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    devices: <><rect x="5" y="2.5" width="14" height="19" rx="2.2"/><path d="M10 18.5h4"/></>,
    online: <><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></>,
    run: <><path d="m9 6 10 6-10 6V6Z"/><path d="M4 5v14"/></>,
    cases: <><path d="M4 7.5h16v13H4z"/><path d="M8 7.5V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2.5M8 12h8"/></>,
  }
  return <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.devices}</svg>
}
function StatusPill({ status }: { status: string }) {
  const running = ['running', 'queued', 'pending'].includes(status)
  const failed = ['failed', 'error', 'errored'].includes(status)
  const passed = ['passed', 'success', 'completed', 'done'].includes(status)
  const cls = running ? 'bg-[#FFF3E3] text-[#A96D20] ring-1 ring-inset ring-[#F4E2C4]' : failed ? 'bg-[#FCECEC] text-[#B94B4B] ring-1 ring-inset ring-[#F3D6D6]' : passed ? 'bg-[#E8F6EF] text-[#187A59] ring-1 ring-inset ring-[#D7EBDD]' : 'bg-[#F0F3F6] text-[#687D91] ring-1 ring-inset ring-[#E1E9F1]'
  const label = running ? '执行中' : failed ? '失败' : passed ? '已完成' : status || '未知'
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ${cls}`}>{label}</span>
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
function SectionHeading({ title, description, to, action }: { title: string; description: string; to: string; action: string }) {
  return <div className="flex items-center justify-between gap-3">
    <div><h2 className="text-[15px] font-bold tracking-tight text-ink">{title}</h2><p className="mt-1 text-[11px] text-ink-mute">{description}</p></div>
    <Link to={to} className="shrink-0 rounded-lg px-3 py-2 text-[11px] font-semibold text-primary hover:bg-primary-soft transition-colors">{action} <span aria-hidden="true">→</span></Link>
  </div>
}
export default function Workbench() {
  const devicesQuery = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: () => fetchRuns(), refetchInterval: 15000 })
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

  return <div className="space-y-6">
    <section className="relative overflow-hidden rounded-[18px] border border-[#D8E7F5] bg-gradient-to-r from-[#EAF3FC] via-[#F4F9FE] to-[#E8F6F4] px-6 py-6 shadow-card xl:px-7">
      <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full bg-[#BBDCF8]/35 blur-3xl" />
      <div className="relative flex flex-wrap items-center justify-between gap-5">
        <div className="max-w-2xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-[#D5E5F4] bg-white/75 px-3 py-1 text-[10px] font-bold tracking-[0.14em] text-[#2878D0]">
            <span className="h-1.5 w-1.5 rounded-full bg-[#13A6B5]" /> SMART DEVICE TESTING PLATFORM
          </div>
          <h1 className="mt-3 text-[27px] font-bold tracking-tight text-[#1B3855]">AI 终端测试工作台</h1>
          <p className="mt-2 text-[13px] leading-6 text-[#617C96]">统一管理设备接入、自动化测试执行与智能评测，让终端测试状态一目了然。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/debug" className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#2878D0] to-[#2589D9] px-4 py-2.5 text-[12px] font-bold text-white shadow-[0_5px_12px_rgba(40,120,208,0.22)] transition hover:-translate-y-0.5 hover:shadow-lift">进入调试控制台 <span>↗</span></Link>
          <Link to="/quick" className="inline-flex items-center gap-2 rounded-xl border border-white bg-white/90 px-4 py-2.5 text-[12px] font-semibold text-[#365875] shadow-sm transition hover:border-[#BFD8F0] hover:bg-white">＋ 创建测试任务</Link>
        </div>
      </div>
    </section>

    {loading && <div className="surface-card px-4 py-3 text-[12px] text-ink-mute">正在读取平台数据…</div>}
    {(devicesQuery.isError || runsQuery.isError || suitesQuery.isError) && <div className="rounded-xl border border-[#F1DDBB] bg-[#FFF8EC] px-4 py-3 text-[12px] text-[#986A2C]">部分工作台数据暂时无法读取，请检查后端连接或刷新页面。</div>}

    <section className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-4">
      <MetricCard label="接入设备" value={devices.length} hint={`在线设备 ${online.length} 台`} tone="blue" icon="devices" />
      <MetricCard label="在线设备" value={online.length} hint={devices.length ? `离线设备 ${devices.length - online.length} 台` : '等待设备接入'} tone="green" icon="online" />
      <MetricCard label="运行中任务" value={runningRuns.length} hint="任务状态自动刷新" tone="orange" icon="run" />
      <MetricCard label="用例套件" value={suitesQuery.isLoading ? '—' : suites.length} hint="已纳入平台管理" tone="cyan" icon="cases" />
    </section>

    <section className="grid gap-5 2xl:grid-cols-[minmax(0,1.45fr)_minmax(330px,0.85fr)]">
      <article className="surface-card p-5">
        <SectionHeading title="设备接入概况" description="按设备平台分类 · 状态每 5 秒刷新" to="/devices" action="设备管理" />
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          {[{ label: 'Android', count: androidCount, color: 'blue', bg: 'bg-[#EAF3FC]', fg: 'text-[#2878D0]' }, { label: 'HarmonyOS', count: harmonyCount, color: 'cyan', bg: 'bg-[#E5F6F4]', fg: 'text-[#0E8794]' }, { label: 'iOS', count: iosCount, color: 'green', bg: 'bg-[#E8F6EF]', fg: 'text-[#21835F]' }].map(item =>
            <div key={item.label} className={`rounded-xl border border-white/80 ${item.bg} p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.8)]`}>
              <div className={`text-[11px] font-semibold ${item.fg}`}>{item.label}</div>
              <div className={`mt-2 text-[25px] font-bold tabular-nums ${item.fg}`}>{item.count}<span className="ml-1 text-[11px] font-medium opacity-70">台</span></div>
            </div>
          )}
        </div>
        <div className="mt-5 space-y-2">
          {devices.slice(0, 6).map(device => <div key={device.id} className="flex items-center gap-3 rounded-xl border border-hairline bg-white px-3.5 py-3 transition hover:border-[#C9DDED] hover:bg-[#FBFDFF]">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${device.status === 'online' ? 'bg-[#22A477] shadow-[0_0_0_3px_rgba(34,164,119,0.12)]' : 'bg-slate-300'}`} />
            <div className="min-w-0 flex-1"><div className="truncate text-[12px] font-semibold text-ink">{device.name}</div><div className="mt-1 text-[10px] text-ink-mute">{deviceKind(device)} <span className="mx-1 text-ink-faint">·</span> {device.connection_type}</div></div>
            <span className={`text-[10px] font-semibold ${device.status === 'online' ? 'text-[#21835F]' : 'text-ink-mute'}`}>{device.status === 'online' ? '在线' : '离线'}</span>
          </div>)}
          {!devices.length && !devicesQuery.isLoading && <div className="rounded-xl border border-dashed border-hairline bg-[#FBFDFF] p-7 text-center"><div className="text-[13px] font-semibold text-ink">还没有接入设备</div><p className="mt-1 text-[11px] text-ink-mute">连接 Android 或 HarmonyOS 设备后，设备状态会显示在这里。</p><Link to="/devices" className="mt-3 inline-flex rounded-lg bg-primary-soft px-3 py-2 text-[11px] font-semibold text-primary-deep">前往设备管理 →</Link></div>}
          {devices.length > 6 && <div className="pt-1 text-right text-[10px] text-ink-mute">另有 {devices.length - 6} 台设备 · <Link to="/devices" className="font-semibold text-primary">查看全部</Link></div>}
        </div>
      </article>

      <article className="surface-card p-5">
        <SectionHeading title="测试质量概览" description="基于已结束的测试运行记录" to="/runs" action="运行结果" />
        <div className="mt-7 flex items-end gap-3"><div className="text-[42px] font-bold leading-none tracking-tight text-[#205F9F] tabular-nums">{caseTotal ? `${passRate}%` : '—'}</div><div className="pb-1 text-[11px] font-medium text-ink-mute">用例通过率</div></div>
        <div className="mt-4 h-2.5 overflow-hidden rounded-full bg-[#EAF0F5]"><div className="h-full rounded-full bg-gradient-to-r from-[#13A6B5] to-[#22A477] transition-all" style={{ width: `${passRate}%` }} /></div>
        <div className="mt-5 grid grid-cols-3 gap-2">
          <div className="rounded-xl border border-[#D7EBDD] bg-[#E8F6EF] p-3 text-center"><div className="text-[21px] font-bold text-[#187A59]">{passTotal}</div><div className="mt-1 text-[10px] font-semibold text-[#47866B]">通过</div></div>
          <div className="rounded-xl border border-[#F3D6D6] bg-[#FCECEC] p-3 text-center"><div className="text-[21px] font-bold text-[#B94B4B]">{completed.reduce((sum, run) => sum + (run.failed || 0), 0)}</div><div className="mt-1 text-[10px] font-semibold text-[#B96A6A]">失败</div></div>
          <div className="rounded-xl border border-[#F4E2C4] bg-[#FFF3E3] p-3 text-center"><div className="text-[21px] font-bold text-[#A96D20]">{completed.reduce((sum, run) => sum + (run.errored || 0), 0)}</div><div className="mt-1 text-[10px] font-semibold text-[#A77D48]">异常</div></div>
        </div>
        <p className="mt-5 border-t border-hairline pt-3 text-[10px] text-ink-mute">累计统计 {completed.length} 次已结束运行 · {caseTotal} 个用例结果</p>
      </article>
    </section>

    <section className="grid gap-5 2xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.8fr)]">
      <article className="surface-card overflow-hidden">
        <div className="border-b border-hairline px-5 py-4"><SectionHeading title="最近测试任务" description="最近 7 条运行记录" to="/runs" action="全部记录" /></div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-[12px]">
            <thead className="bg-[#F5F8FB] text-[10px] font-semibold text-ink-mute"><tr><th className="px-5 py-3">任务 / 套件</th><th className="px-3 py-3">设备</th><th className="px-3 py-3">结果</th><th className="px-3 py-3">用例统计</th><th className="px-5 py-3">创建时间</th></tr></thead>
            <tbody className="divide-y divide-hairline">
              {recentRuns.map((run: Run) => <tr key={run.id} className="transition hover:bg-[#F8FBFE]"><td className="px-5 py-3.5"><Link to={`/runs/${run.id}`} className="font-semibold text-ink hover:text-primary">{run.suite_name || '快速任务'}</Link><div className="mt-1 max-w-[240px] truncate font-mono text-[9px] text-ink-faint">{run.id}</div></td><td className="px-3 py-3.5 text-[10px] text-ink-mute">{devices.find(d => d.id === run.device_id)?.name || run.device_id.slice(0, 8)}</td><td className="px-3 py-3.5"><StatusPill status={run.status} /></td><td className="px-3 py-3.5 text-[10px] tabular-nums"><span className="font-semibold text-[#21835F]">{run.passed} 通过</span><span className="mx-1 text-ink-faint">/</span><span className="text-[#B94B4B]">{run.failed} 失败</span><span className="mx-1 text-ink-faint">/</span><span className="text-[#A96D20]">{run.errored} 异常</span></td><td className="whitespace-nowrap px-5 py-3.5 text-[10px] text-ink-mute">{timeText(run.created_at)}</td></tr>)}
              {!recentRuns.length && <tr><td colSpan={5} className="px-5 py-12 text-center text-[12px] text-ink-mute">暂无测试记录，创建一个任务后将在这里显示。</td></tr>}
            </tbody>
          </table>
        </div>
      </article>
      <div className="space-y-5">
        <article className="surface-card p-5">
          <div className="flex items-center justify-between"><h2 className="text-[15px] font-bold text-ink">异常与告警</h2><span className="rounded-full bg-[#F3F6FA] px-2.5 py-1 text-[10px] font-medium text-ink-mute">实时汇总</span></div>
          <div className="mt-5 space-y-4">
            <div className="flex items-center gap-3"><span className="grid h-9 w-9 place-items-center rounded-xl bg-[#FFF3E3] text-[16px] font-bold text-[#B9771D]">!</span><div className="min-w-0 flex-1"><div className="text-[12px] font-semibold text-ink">离线设备</div><div className="mt-0.5 text-[10px] text-ink-mute">检查连接或设备端 Agent</div></div><div className="text-[20px] font-bold tabular-nums text-[#A96D20]">{devices.length - online.length}</div></div>
            <div className="flex items-center gap-3"><span className="grid h-9 w-9 place-items-center rounded-xl bg-[#FCECEC] text-[16px] font-bold text-[#B94B4B]">×</span><div className="min-w-0 flex-1"><div className="text-[12px] font-semibold text-ink">失败任务</div><div className="mt-0.5 text-[10px] text-ink-mute">当前运行记录中的失败状态</div></div><div className="text-[20px] font-bold tabular-nums text-[#B94B4B]">{failedRecent}</div></div>
          </div>
          <p className="mt-5 border-t border-hairline pt-3 text-[10px] leading-5 text-ink-mute">告警由设备在线状态和测试运行结果汇总生成；后续可扩展独立告警规则与通知中心。</p>
        </article>
        <article className="rounded-[14px] border border-[#D7E7F7] bg-gradient-to-br from-[#EAF3FC] to-[#F3F9FE] p-5 shadow-card">
          <div className="text-[10px] font-bold tracking-[0.13em] text-[#2878D0]">QUICK ACCESS</div>
          <h3 className="mt-1 text-[14px] font-bold text-[#254E73]">常用功能入口</h3>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Link to="/debug" className="rounded-xl border border-white bg-white/90 px-3 py-3 text-[11px] font-semibold text-[#365875] shadow-sm transition hover:-translate-y-0.5 hover:border-[#BFD8F0]">⌘ Agent 调试</Link>
            <Link to="/devices" className="rounded-xl border border-white bg-white/90 px-3 py-3 text-[11px] font-semibold text-[#365875] shadow-sm transition hover:-translate-y-0.5 hover:border-[#BFD8F0]">▣ 设备管理</Link>
            <Link to="/suites" className="rounded-xl border border-white bg-white/90 px-3 py-3 text-[11px] font-semibold text-[#365875] shadow-sm transition hover:-translate-y-0.5 hover:border-[#BFD8F0]">◇ 用例中心</Link>
            <Link to="/quick" className="rounded-xl border border-white bg-white/90 px-3 py-3 text-[11px] font-semibold text-[#365875] shadow-sm transition hover:-translate-y-0.5 hover:border-[#BFD8F0]">＋ 新建任务</Link>
          </div>
        </article>
      </div>
    </section>
  </div>
}
