import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelRun, deleteRun, fetchDevices, fetchRuns, startRun, Run } from '../lib/api'

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
  return <article className="group rounded-xl border border-hairline bg-white p-4 shadow-sm transition-shadow hover:shadow-md sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge status={run.status} /><span className="text-xs text-ink-mute">{formatTime(run.created_at)}</span></div>
        <button onClick={() => navigate(`/runs/${run.id}`)} className="mt-2 block max-w-full truncate text-left text-base font-semibold text-ink hover:text-primary">{title}</button>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-mute">
          <span>设备：{deviceName}</span><span>模型：{run.provider}/{run.model}</span>
          {run.test_account_name && <span>测试账号：{run.test_account_name}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Link to={`/runs/${run.id}`} className="rounded-lg border border-hairline px-3 py-2 text-xs font-medium text-ink hover:bg-canvas-cool">查看详情</Link>
        {active && <button disabled={cancel.isPending} onClick={() => { if (confirm('确定取消这次测试任务吗？')) cancel.mutate() }} className="rounded-lg border border-amber-200 px-3 py-2 text-xs font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-40">{cancel.isPending ? '取消中…' : '取消任务'}</button>}
        {TERMINAL.has(run.status) && <button disabled={rerun.isPending} onClick={() => rerun.mutate()} className="rounded-lg border border-primary/20 px-3 py-2 text-xs font-medium text-primary hover:bg-primary-soft disabled:opacity-40">{rerun.isPending ? '启动中…' : '重新运行'}</button>}
        <button disabled={remove.isPending} title="删除整次运行记录及其用例结果" onClick={() => { if (confirm('删除整次运行记录？该次运行的所有用例结果和派生记忆都会一并删除。')) remove.mutate() }} className="rounded-lg border border-hairline px-2.5 py-2 text-xs text-ink-mute hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 disabled:opacity-40">{remove.isPending ? '…' : '删除'}</button>
      </div>
    </div>
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs"><span className="text-ink-mute">用例执行进度</span><span className="font-medium tabular-nums text-ink">{run.total ? `${progress}%` : '等待结果'} · {run.total || 0} 个用例</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full transition-all ${run.status === 'error' ? 'bg-rose-500' : 'bg-primary'}`} style={{ width: `${progress}%` }} /></div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
        <span className="text-emerald-700">通过 {run.passed || 0}</span><span className="text-rose-700">失败 {run.failed || 0}</span><span className="text-amber-700">异常 {run.errored || 0}</span><span className="text-ink-mute">跳过 {run.skipped || 0}</span>
        {run.total_tokens > 0 && <span className="ml-auto text-ink-mute">{(run.total_tokens / 1000).toFixed(1)}k tokens</span>}
      </div>
    </div>
  </article>
}

function Metric({ label, value, note, tone = 'neutral' }: { label: string; value: number; note: string; tone?: 'neutral' | 'blue' | 'green' | 'amber' | 'red' }) {
  const valueTone = { neutral: 'text-ink', blue: 'text-sky-700', green: 'text-emerald-700', amber: 'text-amber-700', red: 'text-rose-700' }
  return <div className="rounded-xl border border-hairline bg-white p-4 shadow-sm"><div className="text-xs text-ink-mute">{label}</div><div className={`mt-2 text-2xl font-semibold tabular-nums ${valueTone[tone]}`}>{value}</div><div className="mt-1 text-[11px] text-ink-mute">{note}</div></div>
}

export default function Runs() {
  const [filter, setFilter] = useState<Filter>('all')
  const [keyword, setKeyword] = useState('')
  const [deviceFilter, setDeviceFilter] = useState('all')
  const { data: runs = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['runs'],
    queryFn: () => fetchRuns(),
    refetchInterval: query => (query.state.data ?? []).some((run: Run) => ACTIVE.has(run.status)) ? 3000 : false,
  })
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 10000 })
  const deviceNames = useMemo(() => new Map(devices.map(device => [device.id, device.name])), [devices])
  const activeCount = runs.filter(run => ACTIVE.has(run.status)).length
  const completed = runs.filter(run => TERMINAL.has(run.status))
  const failedCount = runs.filter(run => ['error', 'interrupted'].includes(run.status)).length
  const passedCases = completed.reduce((sum, run) => sum + (run.passed || 0), 0)
  const totalCases = completed.reduce((sum, run) => sum + (run.total || 0), 0)
  const passRate = totalCases ? Math.round(passedCases / totalCases * 100) : 0
  const filtered = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    return [...runs].filter(run => {
      if (filter === 'active' && !ACTIVE.has(run.status)) return false
      if (filter === 'done' && run.status !== 'done') return false
      if (filter === 'problem' && !['error', 'interrupted'].includes(run.status)) return false
      if (deviceFilter !== 'all' && run.device_id !== deviceFilter) return false
      if (q && ![run.suite_name || '', run.suite_id, run.provider, run.model, run.id].some(value => value.toLowerCase().includes(q))) return false
      return true
    }).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  }, [runs, filter, deviceFilter, keyword])

  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">TEST EXECUTION CENTER</div><h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">测试任务</h1><p className="mt-1 text-sm text-ink-mute">统一管理单设备任务、多设备回归任务及执行记录。</p></div>
      <div className="flex flex-wrap gap-2"><Link to="/quick" className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-deep">＋ 创建快速任务</Link><Link to="/regressions" className="rounded-lg border border-hairline bg-white px-4 py-2.5 text-sm font-medium text-ink hover:bg-canvas-cool">创建多设备回归</Link></div>
    </header>

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="任务总数" value={runs.length} note="当前可见运行记录" />
      <Metric label="正在执行" value={activeCount} note="排队、准备或运行中" tone="blue" />
      <Metric label="执行异常" value={failedCount} note="错误或服务中断" tone={failedCount ? 'red' : 'neutral'} />
      <Metric label="历史用例通过率" value={totalCases ? passRate : 0} note={totalCases ? `基于 ${totalCases} 个已结束用例` : '暂无已结束用例'} tone="green" />
    </section>

    <section className="rounded-xl border border-hairline bg-white p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-ink">任务运行列表</h2><p className="mt-1 text-xs text-ink-mute">执行中的任务每 3 秒自动刷新</p></div><button onClick={() => void refetch()} className="rounded-lg border border-hairline px-3 py-2 text-xs font-medium text-ink-mute hover:bg-canvas-cool">↻ 刷新</button></div>
      <div className="mt-4 flex flex-col gap-3 lg:flex-row">
        <div className="flex flex-1 flex-wrap gap-1 rounded-lg bg-canvas-soft p-1">
          {([{ id: 'all', label: '全部任务' }, { id: 'active', label: '执行中' }, { id: 'done', label: '已完成' }, { id: 'problem', label: '异常中断' }] as { id: Filter; label: string }[]).map(item => <button key={item.id} onClick={() => setFilter(item.id)} className={`rounded-md px-3 py-2 text-xs font-medium transition-colors ${filter === item.id ? 'bg-white text-primary shadow-sm' : 'text-ink-mute hover:text-ink'}`}>{item.label}</button>)}
        </div>
        <input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索任务名称、模型或运行 ID" className="min-w-0 flex-1 rounded-lg border border-hairline px-3 py-2 text-sm outline-none focus:border-primary lg:max-w-xs" />
        <select value={deviceFilter} onChange={e => setDeviceFilter(e.target.value)} className="rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink"><option value="all">全部设备</option>{devices.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}</select>
      </div>
    </section>

    {isLoading && <div className="rounded-xl border border-hairline bg-white p-10 text-center text-sm text-ink-mute">正在加载任务记录…</div>}
    {isError && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">任务记录读取失败，请检查后端连接后重试。</div>}
    {!isLoading && !isError && filtered.length === 0 && <div className="rounded-xl border border-dashed border-hairline bg-white p-12 text-center"><div className="text-3xl text-ink-faint">▤</div><h3 className="mt-3 text-sm font-semibold text-ink">{runs.length ? '没有符合筛选条件的任务' : '还没有测试任务'}</h3><p className="mt-1 text-xs text-ink-mute">{runs.length ? '试试调整状态、设备或搜索条件。' : '创建快速任务，或选择多设备回归计划开始测试。'}</p>{!runs.length && <div className="mt-4 flex justify-center gap-2"><Link to="/quick" className="rounded-lg bg-primary px-4 py-2 text-xs font-medium text-white">创建快速任务</Link><Link to="/regressions" className="rounded-lg border border-hairline px-4 py-2 text-xs font-medium">多设备回归</Link></div>}</div>}
    <section className="space-y-3">{filtered.map(run => <RunCard key={run.id} run={run} deviceName={deviceNames.get(run.device_id) || run.device_id.slice(0, 12)} />)}</section>
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-mute"><span>显示 {filtered.length} / {runs.length} 条任务记录</span><span>任务执行与正式运行记录保持原有后端逻辑</span></div>
  </div>
}
