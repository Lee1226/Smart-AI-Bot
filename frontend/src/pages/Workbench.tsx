import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Device, Run, fetchDevices, fetchRuns, fetchSuites } from '../lib/api'

function StatusPill({ status }: { status: string }) {
  const running = ['running', 'queued', 'pending'].includes(status)
  const failed = ['failed', 'error', 'errored'].includes(status)
  const passed = ['passed', 'success', 'completed', 'done'].includes(status)
  const cls = running ? 'bg-sky-50 text-sky-700' : failed ? 'bg-rose-50 text-rose-700' : passed ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'
  const label = running ? '执行中' : failed ? '失败' : passed ? '已完成' : status || '未知'
  return <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${cls}`}>{label}</span>
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

function TrendLine({ tone = 'blue' }: { tone?: 'blue' | 'purple' | 'orange' }) {
  const color = tone === 'purple' ? '#A78BFA' : tone === 'orange' ? '#FDBA74' : '#60A5FA'
  return <svg viewBox="0 0 150 54" preserveAspectRatio="none" className="h-12 w-32 overflow-visible" aria-hidden="true">
    <path d="M2 46 C20 40 22 29 38 34 S60 20 74 24 S99 10 112 15 S132 2 148 5" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" />
  </svg>
}

function MetricCard({ label, value, detail, icon, tone, trend }: { label: string; value: string | number; detail: string; icon: string; tone: 'blue' | 'purple' | 'orange'; trend: string }) {
  const tones = {
    blue: { icon: 'from-sky-400 to-blue-600', glow: 'from-sky-50', text: 'text-sky-600' },
    purple: { icon: 'from-violet-400 to-purple-600', glow: 'from-violet-50', text: 'text-violet-600' },
    orange: { icon: 'from-orange-400 to-amber-500', glow: 'from-orange-50', text: 'text-orange-600' },
  }
  const style = tones[tone]
  return <div className={`relative min-h-[146px] overflow-hidden rounded-[22px] border border-[#E8EFFB] bg-gradient-to-br ${style.glow} via-white to-white p-6 shadow-[0_5px_18px_rgba(56,102,174,0.07)]`}>
    <div className="relative z-10 flex items-center gap-5">
      <span className={`grid h-[62px] w-[62px] shrink-0 place-items-center rounded-[18px] bg-gradient-to-br ${style.icon} text-[30px] text-white shadow-lg shadow-blue-100/70`}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[38px] font-bold leading-none tracking-tight text-[#172744] tabular-nums">{value}</div>
        <div className="mt-2 text-[17px] font-medium text-slate-500">{label}</div>
      </div>
    </div>
    <div className="absolute bottom-5 left-6 flex items-center gap-2 text-sm font-medium text-emerald-500">
      <span className="text-base">↑</span>{trend}
    </div>
    <div className="absolute bottom-3 right-4 opacity-90"><TrendLine tone={tone} /></div>
    <div className="absolute bottom-5 left-[108px] text-xs text-slate-400">{detail}</div>
  </div>
}

function QuickCard({ to, icon, title, subtitle, tone }: { to: string; icon: string; title: string; subtitle: string; tone: 'blue' | 'purple' | 'teal' }) {
  const tones = {
    blue: 'from-blue-100 to-sky-50 text-blue-600',
    purple: 'from-violet-100 to-purple-50 text-violet-600',
    teal: 'from-teal-100 to-cyan-50 text-teal-600',
  }
  return <Link to={to} className="group flex min-h-[128px] items-center gap-5 rounded-[22px] border border-white/80 bg-white/80 p-6 shadow-[0_7px_24px_rgba(58,104,176,0.06)] transition-all hover:-translate-y-1 hover:border-blue-100 hover:shadow-[0_12px_30px_rgba(58,104,176,0.12)]">
    <span className={`grid h-[76px] w-[76px] shrink-0 place-items-center rounded-[22px] bg-gradient-to-br text-[38px] ${tones[tone]}`}>{icon}</span>
    <span className="min-w-0 flex-1">
      <span className="block text-xl font-semibold text-[#1B2B4A]">{title}</span>
      <span className="mt-2 block text-sm text-slate-400">{subtitle}</span>
    </span>
    <span className="text-3xl text-slate-300 transition-transform group-hover:translate-x-1 group-hover:text-blue-500">›</span>
  </Link>
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
  const recentRuns = useMemo(() => [...runs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0, 6), [runs])
  const completed = runs.filter(run => !['running', 'queued', 'pending'].includes(run.status))
  const passTotal = completed.reduce((sum, run) => sum + (run.passed || 0), 0)
  const caseTotal = completed.reduce((sum, run) => sum + (run.total || 0), 0)
  const passRate = caseTotal ? Math.round(passTotal / caseTotal * 1000) / 10 : 0
  const recordingTotal = runs.filter(run => run.has_recording || run.recording_status === 'saved').length
  const failedRecent = runs.filter(run => ['failed', 'error', 'errored'].includes(run.status)).length
  const loading = devicesQuery.isLoading || runsQuery.isLoading

  return <div className="space-y-9">
    <section className="relative isolate flex min-h-[360px] items-center overflow-hidden rounded-[26px] bg-gradient-to-br from-[#D7F1FF] via-[#C7E1FF] to-[#C9C8FF] px-8 py-10 shadow-[0_12px_32px_rgba(73,133,213,0.13)] sm:px-11 xl:min-h-[420px] xl:px-12">
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_12%_16%,rgba(255,255,255,0.75),transparent_38%),linear-gradient(135deg,transparent_48%,rgba(255,255,255,0.18)_48.2%,transparent_68%)]" />
      <div className="relative z-10 max-w-[58%]">
        <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/60 bg-white/40 px-4 py-2 text-xs font-semibold tracking-[0.16em] text-blue-700 backdrop-blur">AI TERMINAL TESTING PLATFORM</div>
        <h1 className="text-3xl font-bold leading-[1.35] tracking-tight text-[#10213E] sm:text-4xl xl:text-[42px]">欢迎使用<br className="hidden sm:block" />AI 终端智能测试平台</h1>
        <p className="mt-6 text-lg font-medium text-slate-600 sm:text-xl">让终端测试更智能、更高效</p>
        <Link to="/quick" className="mt-9 inline-flex items-center gap-3 rounded-full bg-gradient-to-r from-blue-600 to-blue-500 px-7 py-4 text-base font-semibold text-white shadow-lg shadow-blue-400/30 transition hover:-translate-y-0.5 hover:shadow-xl">
          <span className="text-lg">▶</span>开始工作
        </Link>
      </div>
      <img src="/workbench-hero.svg" alt="" className="pointer-events-none absolute -right-2 bottom-0 top-0 h-full w-[56%] object-contain object-center" />
      <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full bg-white/25 blur-3xl" />
    </section>

    {loading && <div className="rounded-2xl border border-blue-100 bg-white/80 px-5 py-4 text-sm text-slate-500">正在读取平台数据…</div>}
    {(devicesQuery.isError || runsQuery.isError || suitesQuery.isError) && <div className="rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-800">部分工作台数据暂时无法读取，请检查后端连接或刷新页面。</div>}

    <section className="grid gap-4 lg:grid-cols-3">
      <MetricCard label="已连接设备" value={online.length} detail={`在线 ${online.length} / 总计 ${devices.length}`} icon="▯" tone="blue" trend={`+${Math.max(0, online.length)}`} />
      <MetricCard label="测试成功率" value={caseTotal ? `${passRate}%` : '—'} detail={`累计 ${caseTotal} 个用例`} icon="✓" tone="purple" trend={`+${passTotal}`} />
      <MetricCard label="录像文件" value={recordingTotal.toLocaleString()} detail="已保存的测试录像" icon="▶" tone="orange" trend={`+${recordingTotal}`} />
    </section>

    <section className="space-y-5">
      <div className="flex items-center gap-4"><span className="h-9 w-[6px] rounded-full bg-gradient-to-b from-blue-500 to-indigo-500" /><h2 className="text-[27px] font-bold tracking-tight text-[#1A2A48]">快速开始</h2></div>
      <div className="grid gap-4 xl:grid-cols-3">
        <QuickCard to="/quick" icon="🚀" title="创建测试任务" subtitle="快速创建并执行测试任务" tone="blue" />
        <QuickCard to="/suites" icon="⬡" title="管理用例" subtitle="管理测试用例库" tone="purple" />
        <QuickCard to="/runs" icon="▥" title="查看报告" subtitle="查看测试结果报告" tone="teal" />
      </div>
    </section>

    <section className="grid gap-5 xl:grid-cols-[1.12fr_0.88fr]">
      <div className="overflow-hidden rounded-[22px] border border-[#E8EFFB] bg-white shadow-[0_5px_20px_rgba(56,102,174,0.06)]">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
          <div><h2 className="text-lg font-bold text-[#1B2B4A]">设备接入概况</h2><p className="mt-1 text-xs text-slate-400">设备状态每 5 秒自动刷新</p></div>
          <Link to="/devices" className="text-sm font-semibold text-blue-600 hover:text-blue-700">设备管理 →</Link>
        </div>
        <div className="grid grid-cols-3 gap-3 p-5">
          {[
            { label: 'Android', count: devices.filter(d => deviceKind(d) === 'Android').length, color: 'bg-emerald-400' },
            { label: 'HarmonyOS', count: devices.filter(d => deviceKind(d) === 'HarmonyOS').length, color: 'bg-sky-400' },
            { label: 'iOS', count: devices.filter(d => deviceKind(d) === 'iOS').length, color: 'bg-violet-400' },
          ].map(item => <div key={item.label} className="rounded-2xl bg-slate-50/90 p-4">
            <div className="flex items-center gap-2 text-sm text-slate-500"><span className={`h-2.5 w-2.5 rounded-full ${item.color}`} />{item.label}</div>
            <div className="mt-3 text-3xl font-bold text-[#1B2B4A]">{item.count}<span className="ml-1 text-xs font-medium text-slate-400">台</span></div>
          </div>)}
        </div>
        <div className="space-y-2 px-5 pb-5">
          {devices.slice(0, 5).map(device => <div key={device.id} className="flex items-center gap-3 rounded-xl border border-slate-100 px-4 py-3">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${device.status === 'online' ? 'bg-emerald-400' : 'bg-slate-300'}`} />
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold text-slate-700">{device.name}</div><div className="mt-1 text-xs text-slate-400">{deviceKind(device)} · {device.connection_type}</div></div>
            <span className={`text-xs font-medium ${device.status === 'online' ? 'text-emerald-600' : 'text-slate-400'}`}>{device.status === 'online' ? '在线' : '离线'}</span>
          </div>)}
          {!devices.length && !devicesQuery.isLoading && <div className="rounded-2xl border border-dashed border-slate-200 p-7 text-center"><div className="text-sm font-semibold text-slate-700">还没有接入设备</div><p className="mt-2 text-xs text-slate-400">连接 Android 或 HarmonyOS 设备后，会在这里显示。</p><Link to="/devices" className="mt-4 inline-block text-sm font-semibold text-blue-600">前往设备管理 →</Link></div>}
          {devices.length > 5 && <div className="pt-1 text-right text-xs text-slate-400">另有 {devices.length - 5} 台设备</div>}
        </div>
      </div>

      <div className="overflow-hidden rounded-[22px] border border-[#E8EFFB] bg-white shadow-[0_5px_20px_rgba(56,102,174,0.06)]">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5"><div><h2 className="text-lg font-bold text-[#1B2B4A]">最近测试任务</h2><p className="mt-1 text-xs text-slate-400">最近 6 条运行记录 · {runningRuns.length} 个任务正在执行</p></div><Link to="/runs" className="text-sm font-semibold text-blue-600">全部任务 →</Link></div>
        <div className="divide-y divide-slate-100">
          {recentRuns.map((run: Run) => <Link key={run.id} to={`/runs/${run.id}`} className="flex items-center gap-3 px-5 py-4 transition hover:bg-blue-50/50">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-blue-100 to-indigo-100 text-lg text-blue-600">✓</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold text-slate-700">{run.suite_name || '快速任务'}</span><span className="mt-1 block text-xs text-slate-400">{timeText(run.created_at)}</span></span>
            <StatusPill status={run.status} />
          </Link>)}
          {!recentRuns.length && <div className="p-10 text-center text-sm text-slate-400">暂无测试记录，创建一个任务后将在这里显示。</div>}
        </div>
        <div className="border-t border-slate-100 px-6 py-4 text-xs text-slate-400">用例套件 {suites.length} 个 · 失败任务 {failedRecent} 个 · 累计通过 {passTotal} 个用例</div>
      </div>
    </section>
  </div>
}
