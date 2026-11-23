import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelRun, deleteRun, fetchDevices, fetchRunsPage, startRun, Run } from '../lib/api'

const ACTIVE = new Set(['queued', 'preparing', 'pending', 'running'])
const TERMINAL = new Set(['done', 'error', 'cancelled', 'interrupted'])

const STATUS: Record<string, { label: string; tone: string; dot: string }> = {
  queued: { label: '排队中', tone: 'bg-amber-50 text-amber-700 ring-amber-200', dot: 'bg-amber-500' },
  preparing: { label: '环境准备', tone: 'bg-amber-50 text-amber-700 ring-amber-200', dot: 'bg-amber-500' },
  pending: { label: '等待执行', tone: 'bg-slate-100 text-slate-600 ring-slate-200', dot: 'bg-slate-400' },
  running: { label: '执行中', tone: 'bg-sky-50 text-sky-700 ring-sky-200', dot: 'bg-sky-500' },
  done: { label: '已完成', tone: 'bg-emerald-50 text-emerald-700 ring-emerald-200', dot: 'bg-emerald-500' },
  error: { label: '执行异常', tone: 'bg-rose-50 text-rose-700 ring-rose-200', dot: 'bg-rose-500' },
  cancelled: { label: '已取消', tone: 'bg-slate-100 text-slate-600 ring-slate-200', dot: 'bg-slate-400' },
  interrupted: { label: '中断', tone: 'bg-orange-50 text-orange-700 ring-orange-200', dot: 'bg-orange-500' },
}

type Filter = 'all' | 'active' | 'done' | 'problem'

function formatTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function progressOf(run: Run) {
  if (!run.total) return 0
  return Math.min(100, Math.round(((run.passed || 0) + (run.failed || 0) + (run.errored || 0) + (run.skipped || 0)) / run.total * 100))
}

function StatusBadge({ status }: { status: string }) {
  const item = STATUS[status] || { label: status || '未知', tone: 'bg-slate-100 text-slate-600 ring-slate-200', dot: 'bg-slate-400' }
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${item.tone}`}><span className={`h-1.5 w-1.5 rounded-full ${item.dot} ${ACTIVE.has(status) ? 'animate-pulse' : ''}`} />{item.label}</span>
}

function RunCard({ run, deviceName }: { run: Run; deviceName: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const active = ACTIVE.has(run.status)
  const rerun = useMutation({
    mutationFn: () => startRun({ suite_id: run.suite_id, device_id: run.device_id, provider: run.provider, model: run.model }),
    onSuccess: next => { qc.invalidateQueries({ queryKey: ['runs'] }); navigate(`/runs/${next.id}`) },
  })
  const cancel = useMutation({
    mutationFn: () => cancelRun(run.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
  const remove = useMutation({
    mutationFn: () => deleteRun(run.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['runs'] }); qc.invalidateQueries({ queryKey: ['trends', run.suite_id] }) },
  })
  const progress = progressOf(run)
  const title = run.suite_name || (run.suite_id ? `套件 ${run.suite_id.slice(0, 8)}` : '快速任务')
  return <article className="group rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] transition-all hover:border-blue-200 hover:shadow-[0_12px_32px_rgba(37,99,235,0.08)] sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge status={run.status} /><span className="text-xs text-ink-mute">{formatTime(run.created_at)}</span></div>
        <button onClick={() => navigate(`/runs/${run.id}`)} className="mt-2 block max-w-full truncate text-left text-base font-semibold text-slate-800 transition hover:text-blue-700">{title}</button>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-mute">
          <span>设备：{deviceName}</span><span>模型：{run.provider}/{run.model}</span>
          {run.test_account_name && <span>测试账号：{run.test_account_name}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Link to={`/runs/${run.id}`} className="rounded-xl border border-slate-200 px-3.5 py-2 text-xs font-semibold text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700">查看详情</Link>
        {active && <button disabled={cancel.isPending} onClick={() => { if (confirm('确定取消这次测试任务吗？')) cancel.mutate() }} className="rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-2 text-xs font-semibold text-amber-700 transition hover:bg-amber-100 disabled:opacity-40">{cancel.isPending ? '取消中…' : '取消任务'}</button>}
        {TERMINAL.has(run.status) && <button disabled={rerun.isPending} onClick={() => rerun.mutate()} className="rounded-xl border border-blue-200 bg-blue-50 px-3.5 py-2 text-xs font-semibold text-blue-700 transition hover:bg-blue-100 disabled:opacity-40">{rerun.isPending ? '启动中…' : '重新运行'}</button>}
        <button disabled={remove.isPending} title="删除整次运行记录及其用例结果" onClick={() => { if (confirm('删除整次运行记录？该次运行的所有用例结果和派生记忆都会一并删除。')) remove.mutate() }} className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-40">{remove.isPending ? '…' : '删除'}</button>
      </div>
    </div>
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs"><span className="text-ink-mute">用例执行进度</span><span className="font-medium tabular-nums text-ink">{run.total ? `${progress}%` : '等待结果'} · {run.total || 0} 个用例</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full transition-all ${run.status === 'error' ? 'bg-rose-500' : 'bg-primary'}`} style={{ width: `${progress}%` }} /></div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
        <span className="text-emerald-700">通过 {run.passed || 0}</span><span className="text-rose-700">失败 {run.failed || 0}</span><span className="text-amber-700">异常 {run.errored || 0}</span><span className="text-ink-mute">跳过 {run.skipped || 0}</span>
        {run.total_tokens > 0 && <span className="ml-auto text-ink-mute">{(run.total_tokens / 1000).toFixed(1)}k tokens</span>}
      </div>
    </div>
  </article>
}

function Metric({ label, value, note, tone = 'neutral' }: { label: string; value: number; note: string; tone?: 'neutral' | 'blue' | 'green' | 'amber' | 'red' }) {
  const valueTone = { neutral: 'text-slate-800', blue: 'text-blue-700', green: 'text-emerald-700', amber: 'text-amber-700', red: 'text-rose-700' }
  return <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)]"><div className="text-xs font-medium text-slate-500">{label}</div><div className={`mt-2 text-3xl font-bold tracking-tight tabular-nums ${valueTone[tone]}`}>{value}</div><div className="mt-1 text-[11px] text-ink-mute">{note}</div></div>
}

export default function Runs() {
  const [filter, setFilter] = useState<Filter>('all')
  const [keyword, setKeyword] = useState('')
  const [deviceFilter, setDeviceFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data: runPage, isLoading, isError, refetch } = useQuery({
    queryKey: ['runs', filter, deviceFilter, keyword.trim(), page, pageSize],
    queryFn: () => fetchRunsPage({
      status: filter,
      device_id: deviceFilter === 'all' ? undefined : deviceFilter,
      q: keyword.trim() || undefined,
      page,
      page_size: pageSize,
    }),
    refetchInterval: query => query.state.data?.has_active ? 3000 : false,
  })
  const runs = runPage?.items ?? []
  const totalRuns = runPage?.total ?? 0
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 10000 })
  const deviceNames = useMemo(() => new Map(devices.map(device => [device.id, device.name])), [devices])
  const activeCount = runs.filter(run => ACTIVE.has(run.status)).length
  const completed = runs.filter(run => TERMINAL.has(run.status))
  const failedCount = runs.filter(run => ['error', 'interrupted'].includes(run.status)).length
  const passedCases = completed.reduce((sum, run) => sum + (run.passed || 0), 0)
  const totalCases = completed.reduce((sum, run) => sum + (run.total || 0), 0)
  const passRate = totalCases ? Math.round(passedCases / totalCases * 100) : 0
  const filtered = runs
  const totalPages = Math.max(1, Math.ceil(totalRuns / pageSize))

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  return <div className="mx-auto max-w-[1600px] space-y-6">
    <header className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-7 py-7 text-white shadow-[0_18px_45px_rgba(37,99,235,0.16)] sm:px-9">
      <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" /><div className="relative"><div className="mb-3 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-blue-50">AI 终端 · 任务管理</div><h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">测试任务</h1><p className="mt-2 text-sm leading-6 text-blue-50/90">统一管理单设备任务、多设备回归任务及执行记录。</p></div>
      <div className="flex flex-wrap gap-2"><Link to="/quick" className="rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-blue-700 shadow-sm transition hover:bg-blue-50">＋ 创建快速任务</Link><Link to="/regressions" className="rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/20">创建多设备回归</Link></div>
    </header>

    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="任务总数" value={totalRuns} note="全部运行记录" />
      <Metric label="当前页执行中" value={activeCount} note="本页排队、准备或运行中" tone="blue" />
      <Metric label="当前页异常" value={failedCount} note="本页错误或服务中断" tone={failedCount ? 'red' : 'neutral'} />
      <Metric label="当前页用例通过率" value={totalCases ? passRate : 0} note={totalCases ? `基于本页 ${totalCases} 个已结束用例` : '本页暂无已结束用例'} tone="green" />
    </section>

    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-slate-800">任务运行列表</h2><p className="mt-1 text-xs text-ink-mute">执行中的任务每 3 秒自动刷新</p></div><button onClick={() => void refetch()} className="rounded-xl border border-slate-200 px-3.5 py-2 text-xs font-semibold text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700">↻ 刷新</button></div>
      <div className="mt-4 flex flex-col gap-3 lg:flex-row">
        <div className="flex flex-1 flex-wrap gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1">
          {([{ id: 'all', label: '全部任务' }, { id: 'active', label: '执行中' }, { id: 'done', label: '已完成' }, { id: 'problem', label: '异常中断' }] as { id: Filter; label: string }[]).map(item => <button key={item.id} onClick={() => { setFilter(item.id); setPage(1) }} className={`rounded-lg px-3.5 py-2 text-xs font-semibold transition-colors ${filter === item.id ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-500 hover:bg-white hover:text-blue-700'}`}>{item.label}</button>)}
        </div>
        <input value={keyword} onChange={e => { setKeyword(e.target.value); setPage(1) }} placeholder="搜索任务名称、模型或运行 ID" className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-2.5 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10 lg:max-w-xs" />
        <select value={deviceFilter} onChange={e => { setDeviceFilter(e.target.value); setPage(1) }} className="rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-2.5 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10"><option value="all">全部设备</option>{devices.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}</select>
      </div>
    </section>

    {isLoading && <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center text-sm text-slate-500">正在加载任务记录…</div>}
    {isError && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50/80 p-5 text-sm text-rose-700">任务记录读取失败，请检查后端连接后重试。</div>}
    {!isLoading && !isError && filtered.length === 0 && <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center shadow-[0_8px_28px_rgba(31,75,135,0.03)]"><div className="text-3xl text-ink-faint">▤</div><h3 className="mt-3 text-sm font-semibold text-ink">{runs.length ? '没有符合筛选条件的任务' : '还没有测试任务'}</h3><p className="mt-1 text-xs text-ink-mute">{runs.length ? '试试调整状态、设备或搜索条件。' : '创建快速任务，或选择多设备回归计划开始测试。'}</p>{!runs.length && <div className="mt-4 flex justify-center gap-2"><Link to="/quick" className="rounded-xl bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700">创建快速任务</Link><Link to="/regressions" className="rounded-xl border border-slate-200 px-4 py-2.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">多设备回归</Link></div>}</div>}
    <section className="space-y-3">{filtered.map(run => <RunCard key={run.id} run={run} deviceName={deviceNames.get(run.device_id) || run.device_id.slice(0, 12)} />)}</section>
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-ink-mute">
      <span>当前页显示 {filtered.length} 条 / 共 {totalRuns} 条任务记录</span>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-2">每页显示
          <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600"><option value={10}>10 条</option><option value={20}>20 条</option><option value={50}>50 条</option><option value={100}>100 条</option></select>
        </label>
        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 font-medium text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 disabled:opacity-40">上一页</button>
        <span>第 {page} / {totalPages} 页</span>
        <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="rounded border border-hairline px-2 py-1 disabled:opacity-40">下一页</button>
      </div>
    </div>
  </div>
}
