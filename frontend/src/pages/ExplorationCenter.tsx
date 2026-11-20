import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchDevices, fetchSuites, fetchSuiteCases, fetchExecutionPlans, runExecutionPlan, fetchRun, fetchResults, type Device } from '../lib/api'
import { useT } from '../lib/i18n'

type ExploreStep = { id: string; sequence: number; event_type: string; title: string; detail: string; action: string; status: string; duration_ms: number; evidence: Record<string, string>; page_before: { ui_text?: string }; page_after: { ui_text?: string }; action_args: Record<string, unknown> }
type ExploreSession = {
  id: string; title: string; goal: string; expected_result: string; device_id: string
  platform: string; status: string; summary: string; created_at: string
  steps?: ExploreStep[]
}
type ExploreKnowledge = {
  id: string; title: string; category: string; description: string; status: string
  app_package: string; platform: string; risk_level: string; source_session_id: string
}
type Overview = {
  session_total: number; session_running: number; session_completed: number
  knowledge_total: number; knowledge_pending: number; knowledge_confirmed: number
  recent_sessions: ExploreSession[]
}

const navItems = [
  { to: '/exploration', zh: '探索概览', en: 'Overview', icon: '▦' },
  { to: '/exploration/new', zh: '新建探索', en: 'New exploration', icon: '＋' },
  { to: '/exploration/history', zh: '探索历史', en: 'History', icon: '◷' },
  { to: '/exploration/knowledge', zh: '探索经验库', en: 'Experience library', icon: '◇' },
  { to: '/exploration/plans', zh: '计划生成与验证', en: 'Plan review', icon: '✓' },
]
const statusLabel: Record<string, string> = {
  draft: '草稿', queued: '排队中', running: '探索中', paused: '已暂停', completed: '已完成',
  failed: '失败', cancelled: '已取消', discovered: '待整理', pending_review: '待审核',
  validated: '已验证', confirmed: '已确认', invalidated: '已失效', disabled: '已停用',
}
const statusClass: Record<string, string> = {
  completed: 'bg-green-50 text-green-700', confirmed: 'bg-green-50 text-green-700',
  running: 'bg-blue-50 text-blue-700', failed: 'bg-red-50 text-red-700',
  discovered: 'bg-amber-50 text-amber-700', pending_review: 'bg-amber-50 text-amber-700',
  draft: 'bg-slate-100 text-slate-600', paused: 'bg-slate-100 text-slate-600',
}


function EvidenceImage({ sessionId, filename, label }: { sessionId: string; filename?: string; label: string }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let objectUrl = ''
    let active = true
    if (!filename) { setUrl(''); return }
    api.get(`/exploration/sessions/${sessionId}/evidence/${encodeURIComponent(filename)}`, { responseType: 'blob' })
      .then(response => {
        objectUrl = URL.createObjectURL(response.data)
        if (active) setUrl(objectUrl)
      }).catch(() => { if (active) setUrl('') })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [sessionId, filename])
  if (!filename) return null
  return <div className="min-w-0"><div className="mb-1 text-[11px] text-ink-faint">{label}</div>{url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={label} className="max-h-80 w-full rounded-lg border border-hairline object-contain bg-black/5" /></a> : <div className="rounded-lg bg-canvas-soft p-3 text-xs text-ink-faint">证据读取中或暂不可用</div>}</div>
}

function Status({ value }: { value: string }) {
  return <span className={`inline-flex rounded-full px-2 py-1 text-xs ${statusClass[value] || 'bg-slate-100 text-slate-600'}`}>{statusLabel[value] || value}</span>
}
function PageTitle({ title, desc, action }: { title: string; desc: string; action?: React.ReactNode }) {
  return <div className="mb-6 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-bold text-ink">{title}</h1><p className="mt-1 text-sm text-ink-mute">{desc}</p></div>{action}</div>
}
function Empty({ title, desc, action }: { title: string; desc: string; action?: React.ReactNode }) {
  return <div className="rounded-xl border border-dashed border-hairline bg-white px-5 py-12 text-center"><div className="text-sm font-semibold text-ink">{title}</div><p className="mt-1 text-xs text-ink-mute">{desc}</p>{action && <div className="mt-4">{action}</div>}</div>
}


function PlanReviewWorkspace() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [deviceId, setDeviceId] = useState('')
  const [activeRunId, setActiveRunId] = useState('')
  const requestedSuiteId = searchParams.get('suite') || ''
  const { data: suitesPage, isLoading: suitesLoading } = useQuery({
    queryKey: ['exploration-plan-suites'],
    queryFn: () => fetchSuites({ page: 1, page_size: 100 }),
  })
  const suites = (suitesPage?.items || []).filter(suite => suite.source_format === 'exploration')
  const selectedSuite = suites.find(suite => suite.id === requestedSuiteId) || suites[0]
  const { data: cases = [], isLoading: casesLoading } = useQuery({
    queryKey: ['exploration-plan-cases', selectedSuite?.id],
    queryFn: () => fetchSuiteCases(selectedSuite!.id),
    enabled: !!selectedSuite,
  })
  const selectedCase = cases[0]
  const { data: plans = [], isLoading: plansLoading, refetch: refetchPlans } = useQuery({
    queryKey: ['exploration-plan-items', selectedCase?.id],
    queryFn: () => fetchExecutionPlans(selectedCase!.id),
    enabled: !!selectedCase,
    refetchInterval: activeRunId ? 2000 : false,
  })
  const selectedPlan = plans[0]
  const { data: devices = [] } = useQuery({
    queryKey: ['devices'],
    queryFn: fetchDevices,
    refetchInterval: 5000,
  })
  const onlineDevices = devices.filter(device => device.status === 'online')
  const selectedDevice = onlineDevices.find(device => device.id === deviceId)
  const { data: run } = useQuery({
    queryKey: ['exploration-plan-run', activeRunId],
    queryFn: () => fetchRun(activeRunId),
    enabled: !!activeRunId,
    refetchInterval: query => ['pending', 'running'].includes(query.state.data?.status || '') ? 1500 : false,
  })
  const { data: results = [] } = useQuery({
    queryKey: ['exploration-plan-results', activeRunId],
    queryFn: () => fetchResults(activeRunId),
    enabled: !!activeRunId && !!run && !['pending', 'running'].includes(run.status),
  })
  const runMut = useMutation({
    mutationFn: async () => {
      if (!selectedPlan || !selectedDevice) throw new Error('请先选择在线测试设备')
      return runExecutionPlan(selectedPlan.id, { device_id: selectedDevice.id, max_steps: 8 })
    },
    onSuccess: result => {
      setActiveRunId(result.id)
      qc.invalidateQueries({ queryKey: ['exploration-plan-run', result.id] })
    },
  })
  useEffect(() => {
    if (run && !['pending', 'running'].includes(run.status)) {
      refetchPlans()
      qc.invalidateQueries({ queryKey: ['execution-plans'] })
    }
  }, [run?.status, refetchPlans, qc, run])
  useEffect(() => {
    if (selectedSuite && selectedSuite.id !== requestedSuiteId) {
      setSearchParams({ suite: selectedSuite.id }, { replace: true })
    }
  }, [selectedSuite?.id, requestedSuiteId, setSearchParams])

  const steps = selectedPlan?.steps || []
  const blockers = steps.flatMap((step, index) => {
    const issues: string[] = []
    if (!step.action || !step.executor) issues.push('动作类型或执行策略未配置')
    if (step.action === 'tap_element' && !Object.values(step.locator || {}).some(value => String(value || '').trim())) {
      issues.push('缺少可复用的控件定位信息，不能仅依赖探索时的控件索引')
    }
    if (['ai', 'hybrid'].includes(step.executor) &&
      (step.intent?.status !== 'confirmed' || !step.intent.description?.trim() || !step.intent.expected_result?.trim())) {
      issues.push('操作意图或预期结果尚未人工确认')
    }
    if (step.action === 'start_app' && !String(step.args?.package_name || step.args?.package || '').trim()) {
      issues.push('缺少应用 Package 标识')
    }
    if (step.action === 'swipe' && !['x1', 'y1', 'x2', 'y2'].every(key => {
      const value = step.args?.[key] ?? (step.fallback?.coordinates as Record<string, unknown> | undefined)?.[key]
      return value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value))
    })) issues.push('滑动坐标不完整')
    return issues.map(issue => ({ step: index + 1, issue }))
  })
  const runPending = !!run && ['pending', 'running'].includes(run.status)
  const result = results[0]
  const resultPassed = result?.status === 'pass'
  const planValidated = selectedPlan?.status === 'validated'

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-hairline bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-sm font-semibold">待整理的探索计划</h2><p className="mt-1 text-xs text-ink-mute">只展示 AI 探索生成的计划草稿，选择后可检查、编辑和验证。</p></div>
          <span className="rounded-full bg-primary-soft px-3 py-1 text-xs text-primary-deep">{suites.length} 个探索套件</span>
        </div>
        {suitesLoading ? <p className="py-8 text-center text-sm text-ink-faint">正在读取探索计划…</p> :
          suites.length === 0 ? <Empty title="还没有待整理的计划" desc="请先在探索历史中完成一次探索，并点击“生成执行计划草稿”。" action={<Link to="/exploration/history" className="inline-flex rounded-lg border border-hairline px-3 py-2 text-sm">查看探索历史</Link>} /> :
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-xs font-medium text-ink-secondary">探索计划套件
              <select className="mt-1.5 w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm" value={selectedSuite?.id || ''} onChange={e => { setSearchParams({ suite: e.target.value }); setActiveRunId('') }}>
                {suites.map(suite => <option key={suite.id} value={suite.id}>{suite.name} · {suite.case_count} 个用例</option>)}
              </select>
            </label>
            <div className="rounded-lg bg-canvas-soft p-3 text-xs text-ink-mute">
              <div className="font-medium text-ink">{selectedSuite?.app_package || '未记录应用标识'}</div>
              <div className="mt-1">创建时间：{selectedSuite ? new Date(selectedSuite.created_at).toLocaleString() : '—'}</div>
              <div className="mt-1">计划用例：{casesLoading ? '读取中…' : cases.length}</div>
            </div>
          </div>}
      </section>

      {selectedSuite && <section className="rounded-xl border border-hairline bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="text-sm font-semibold">计划检查与整理</h2><p className="mt-1 text-xs text-ink-mute">复用现有执行计划编辑器，编辑操作意图、定位、等待和验证条件。</p></div>
          {selectedPlan && <Link to={`/execution-plans?suite=${encodeURIComponent(selectedSuite.id)}`} className="rounded-lg border border-hairline px-3 py-2 text-xs hover:bg-canvas-soft">打开步骤编辑器</Link>}
        </div>
        {casesLoading || plansLoading ? <p className="py-6 text-center text-sm text-ink-faint">正在加载计划内容…</p> :
          !selectedCase ? <Empty title="套件中没有用例" desc="该探索套件没有关联用例，请返回探索记录重新生成草稿。" /> :
          !selectedPlan ? <Empty title="该探索用例尚无执行计划" desc="请前往执行计划编辑器生成或检查计划。" action={<Link to={`/execution-plans?suite=${encodeURIComponent(selectedSuite.id)}`} className="text-primary underline">打开执行计划编辑器</Link>} /> :
          <div className="mt-4 space-y-4">
            <div className="rounded-lg bg-canvas-soft p-3"><div className="text-xs font-medium text-ink">探索目标</div><p className="mt-1 text-sm">{selectedCase.path || '未记录目标'}</p><div className="mt-2 text-xs font-medium text-ink">预期结果</div><p className="mt-1 text-xs text-ink-mute">{selectedCase.expected || '未设置最终预期；需确认各步骤的验证条件'}</p></div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-slate-100 px-2.5 py-1">{selectedPlan.status === 'validated' ? '已验证' : '待整理'}</span>
              <span className="text-ink-faint">版本 v{selectedPlan.version} · {steps.length} 个步骤</span>
              {selectedPlan.validated_run_id && <span className="text-ink-faint">验证运行：{selectedPlan.validated_run_id.slice(0, 8)}</span>}
            </div>
            <div className="rounded-lg border border-hairline p-3">
              <div className="text-xs font-semibold">步骤预检</div>
              {blockers.length ? <div className="mt-2 space-y-1.5">{blockers.map((item, i) => <div key={i} className="text-xs text-amber-800">第 {item.step} 步：{item.issue}</div>)}</div> :
                <p className="mt-2 text-xs text-green-700">基础结构检查通过。仍需结合真实设备回放结果确认行为正确。</p>}
              {selectedPlan.warnings.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-ink-mute">{selectedPlan.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
            </div>
            <div className="space-y-2">{steps.map((step, i) => <div key={step.step_id || i} className="flex items-start gap-3 rounded-lg border border-hairline p-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-canvas-soft text-xs">{i + 1}</span>
              <div className="min-w-0 flex-1"><div className="text-sm font-medium">{step.action}</div><p className="mt-1 text-xs text-ink-mute">{step.intent?.description || '操作意图待补充'}</p><div className="mt-1 text-[11px] text-ink-faint">执行器：{step.executor} · 验证条件：{step.verification ? '已配置' : '未配置'} · 操作后等待：{step.wait_condition?.type === 'delay' ? `${step.wait_condition.duration_ms || 0} ms` : '未配置'}</div></div>
              <span className="text-[11px] text-ink-faint">{step.intent?.status === 'confirmed' ? '已确认' : '待确认'}</span>
            </div>)}</div>
          </div>}
      </section>}

      {selectedPlan && <section className="rounded-xl border border-hairline bg-white p-4">
        <h2 className="text-sm font-semibold">真机回放验证</h2>
        <p className="mt-1 text-xs text-ink-mute">在选定的在线设备上调用现有执行计划 Runner；不另建设备控制通道。</p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="min-w-[240px] flex-1 text-xs font-medium text-ink-secondary">测试设备
            <select className="mt-1.5 w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm" value={deviceId} onChange={e => setDeviceId(e.target.value)}>
              <option value="">选择在线设备</option>{onlineDevices.map(device => <option key={device.id} value={device.id}>{device.name} · {device.platform} · {device.connection_type}</option>)}
            </select>
          </label>
          <button disabled={!selectedDevice || !selectedPlan || blockers.length > 0 || runMut.isPending || runPending} onClick={() => runMut.mutate()} className="rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{runMut.isPending ? '正在启动…' : runPending ? '回放运行中…' : '开始真机回放验证'}</button>
        </div>
        {blockers.length > 0 && <p className="mt-2 text-xs text-amber-700">请先补齐上述必需项；当前计划不能启动验证。</p>}
        {runMut.isError && <p className="mt-2 text-xs text-red-600">{String((runMut.error as Error)?.message || runMut.error)}</p>}
        {run && <div className="mt-4 rounded-lg border border-hairline p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-semibold">运行状态：{run.status}</span><span className="text-[11px] text-ink-faint">Run ID：{run.id}</span></div>
          {runPending ? <p className="mt-2 text-xs text-ink-mute">正在回放，页面每隔约 1.5 秒刷新运行状态…</p> :
            <div className="mt-2 text-xs"><div>通过：{run.passed} · 失败：{run.failed} · 异常：{run.errored}</div>{result && <p className={resultPassed ? 'mt-1 text-green-700' : 'mt-1 text-red-700'}>{result.status === 'pass' ? '回放验证通过' : `回放未通过：${result.reason || result.status}`}</p>}</div>}
          {planValidated && <p className="mt-2 text-xs font-medium text-green-700">计划已标记为已验证，可继续人工审核后投入正式执行。</p>}
        </div>}
      </section>}
    </div>
  )
}

export default function ExplorationCenter() {
  const t = useT()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const qc = useQueryClient()
  const current = navItems.find(item => item.to === pathname)?.to || '/exploration'
  const isNew = current === '/exploration/new'
  const isHistory = current === '/exploration/history'
  const isKnowledge = current === '/exploration/knowledge'
  const isPlans = current === '/exploration/plans'
  const [goal, setGoal] = useState('')
  const [expected, setExpected] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [platform, setPlatform] = useState('android')
  const [appPackage, setAppPackage] = useState('')
  const [maxSteps, setMaxSteps] = useState(20)
  const [timeoutSeconds, setTimeoutSeconds] = useState(300)
  const [generatedPlanId, setGeneratedPlanId] = useState('')
  const { data: overview } = useQuery({ queryKey: ['exploration-overview'], queryFn: async () => (await api.get<Overview>('/exploration/overview')).data })
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, enabled: isNew, refetchInterval: 5000 })
  const selectedSessionId = isHistory ? searchParams.get('session') || '' : ''
  const { data: selectedSession } = useQuery({
    queryKey: ['exploration-session', selectedSessionId],
    queryFn: async () => (await api.get<ExploreSession>(`/exploration/sessions/${selectedSessionId}`)).data,
    enabled: !!selectedSessionId,
    refetchInterval: query => ['queued', 'running'].includes(query.state.data?.status || '') ? 1500 : false,
  })
  const { data: sessions = [], isLoading: sessionsLoading } = useQuery({
    queryKey: ['exploration-sessions'], queryFn: async () => (await api.get<ExploreSession[]>('/exploration/sessions')).data,
    enabled: isHistory,
    refetchInterval: 3000,
  })
  const { data: knowledge = [], isLoading: knowledgeLoading } = useQuery({
    queryKey: ['exploration-knowledge'], queryFn: async () => (await api.get<ExploreKnowledge[]>('/exploration/knowledge')).data,
    enabled: isKnowledge,
  })
  const createSession = useMutation({
    mutationFn: async () => (await api.post<ExploreSession>('/exploration/sessions', {
      title: goal.trim().slice(0, 80), goal, expected_result: expected, device_id: deviceId,
      platform, app_package: appPackage, max_steps: maxSteps, timeout_seconds: timeoutSeconds,
    })).data,
    onSuccess: async row => {
      try {
        await api.post(`/exploration/sessions/${row.id}/start`)
        await qc.invalidateQueries({ queryKey: ['exploration-overview'] })
        await qc.invalidateQueries({ queryKey: ['exploration-sessions'] })
        navigate(`/exploration/history?session=${row.id}`)
      } catch (error) {
        await qc.invalidateQueries({ queryKey: ['exploration-sessions'] })
        navigate(`/exploration/history?session=${row.id}`)
        throw error
      }
    },
  })
  const stopSession = useMutation({
    mutationFn: async (id: string) => api.post(`/exploration/sessions/${id}/stop`),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['exploration-session', selectedSessionId] })
      await qc.invalidateQueries({ queryKey: ['exploration-sessions'] })
      await qc.invalidateQueries({ queryKey: ['exploration-overview'] })
    },
  })

  const generatePlan = useMutation({
    mutationFn: async (id: string) => (await api.post<{ plan_id: string; suite_id: string; warnings: string[]; message: string }>(`/exploration/sessions/${id}/generate-plan`)).data,
    onSuccess: result => { setGeneratedPlanId(result.plan_id); window.location.assign(`/execution-plans?suite=${encodeURIComponent(result.suite_id)}`) },
  })
  const reviewKnowledge = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: string }) => api.patch(`/exploration/knowledge/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['exploration-knowledge'] }),
  })
  const updateKnowledge = useMutation({
    mutationFn: async ({ id, description }: { id: string; description: string }) => api.patch(`/exploration/knowledge/${id}`, { description }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['exploration-knowledge'] }),
  })
  const selectedDevice = devices.find((device: Device) => device.id === deviceId)
  const onlineDevices = useMemo(() => devices.filter((device: Device) => device.status === 'online'), [devices])

  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="text-xs text-ink-faint">AI WORKSPACE / EXPLORATION</div><h1 className="mt-1 text-xl font-bold text-ink">AI 探索中心</h1><p className="mt-1 text-xs text-ink-mute">从自然语言目标出发，沉淀可复用的操作经验与确定性执行计划</p></div>
      <Link to="/exploration/new" className="rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-white hover:opacity-90">＋ 新建探索</Link>
    </div>
    <div className="flex flex-wrap gap-1 border-b border-hairline">
      {navItems.map(item => <Link key={item.to} to={item.to} className={`border-b-2 px-3 py-2.5 text-sm ${current === item.to ? 'border-primary font-semibold text-primary-deep' : 'border-transparent text-ink-mute hover:text-ink'}`}><span className="mr-1.5">{item.icon}</span>{t(item.zh, item.en)}</Link>)}
    </div>

    {!isNew && !isHistory && !isKnowledge && !isPlans && <>
      <PageTitle title="探索概览" desc="查看探索进度、待审核经验和近期探索活动。" action={<Link to="/exploration/new" className="rounded-lg border border-hairline bg-white px-3 py-2 text-sm">开始一次探索</Link>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ['探索会话', overview?.session_total ?? 0, '累计创建'],
          ['正在探索', overview?.session_running ?? 0, '当前运行会话'],
          ['经验条目', overview?.knowledge_total ?? 0, '已沉淀的场景经验'],
          ['待审核经验', overview?.knowledge_pending ?? 0, '需要人工确认'],
        ].map(([label, value, hint]) => <div key={String(label)} className="rounded-xl border border-hairline bg-white p-4"><div className="text-xs text-ink-faint">{label}</div><div className="mt-2 text-2xl font-semibold tabular-nums text-ink">{value}</div><div className="mt-1 text-xs text-ink-mute">{hint}</div></div>)}
      </div>
      <section className="rounded-xl border border-hairline bg-white p-4">
        <div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">最近探索</h2><Link to="/exploration/history" className="text-xs text-primary">查看全部 →</Link></div>
        {(overview?.recent_sessions || []).length === 0 ? <Empty title="还没有探索记录" desc="创建一次探索任务，开始记录设备操作轨迹。" action={<Link to="/exploration/new" className="text-sm text-primary">新建探索</Link>} /> : <div className="divide-y divide-hairline">{overview?.recent_sessions.map(row => <Link key={row.id} to={`/exploration/history?session=${row.id}`} className="flex flex-wrap items-center justify-between gap-3 py-3"><div className="min-w-0"><div className="truncate text-sm font-medium">{row.title}</div><div className="mt-1 truncate text-xs text-ink-mute">{row.goal}</div></div><Status value={row.status} /></Link>)}</div>}
      </section>
      <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-4 text-xs leading-5 text-blue-900"><strong>当前阶段说明：</strong>AI 探索执行已接入现有 Agent 和设备控制通道，任务与普通测试共用设备独占锁；当前已支持启动、排队、停止、步骤与截图证据记录。经验自动识别、暂停/人工接管和计划生成验证仍在后续阶段。</div>
    </>}

    {isNew && <>
      <PageTitle title="新建探索" desc="描述希望 AI 在设备上完成的目标。创建会话后会立即排队执行；设备忙碌时会等待设备空闲。" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="space-y-4 rounded-xl border border-hairline bg-white p-5">
          <div><label className="mb-1.5 block text-sm font-medium">探索目标 <span className="text-red-500">*</span></label><textarea value={goal} onChange={e => setGoal(e.target.value)} rows={4} maxLength={10000} placeholder="例如：打开 YOYO 助手，询问今天的天气，并确认助手返回了天气信息。" className="w-full rounded-lg border border-hairline px-3 py-2.5 text-sm outline-none focus:border-primary" /><div className="mt-1 text-right text-[11px] text-ink-faint">{goal.length}/10000</div></div>
          <div><label className="mb-1.5 block text-sm font-medium">预期结果</label><textarea value={expected} onChange={e => setExpected(e.target.value)} rows={3} placeholder="描述完成标准，例如：页面出现天气回复，包含温度和天气状况。" className="w-full rounded-lg border border-hairline px-3 py-2.5 text-sm outline-none focus:border-primary" /></div>
          <div className="grid gap-3 md:grid-cols-2">
            <div><label className="mb-1.5 block text-sm font-medium">探索设备 <span className="text-red-500">*</span></label><select value={deviceId} onChange={e => { setDeviceId(e.target.value); const d = onlineDevices.find((x: Device) => x.id === e.target.value); if (d?.platform) setPlatform(d.platform) }} className="w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm"><option value="">选择已连接设备</option>{onlineDevices.map((d: Device) => <option key={d.id} value={d.id}>{d.name} · {d.platform || d.connection_type}</option>)}</select><p className="mt-1 text-[11px] text-ink-faint">{selectedDevice ? `已选择：${selectedDevice.name}` : '仅显示当前账号下在线设备'}</p></div>
            <div><label className="mb-1.5 block text-sm font-medium">目标平台</label><select value={platform} onChange={e => setPlatform(e.target.value)} className="w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm"><option value="android">Android</option><option value="harmony">HarmonyOS</option><option value="ios">iOS</option></select></div>
          </div>
          <div><label className="mb-1.5 block text-sm font-medium">目标应用包名（可选）</label><input value={appPackage} onChange={e => setAppPackage(e.target.value)} placeholder="com.example.app" className="w-full rounded-lg border border-hairline px-3 py-2.5 text-sm" /></div>
          <div className="grid gap-3 md:grid-cols-2">
            <div><label className="mb-1.5 block text-sm font-medium">最大探索步骤</label><input type="number" min={1} max={100} value={maxSteps} onChange={e => setMaxSteps(Math.max(1, Math.min(100, Number(e.target.value) || 1)))} className="w-full rounded-lg border border-hairline px-3 py-2.5 text-sm" /></div>
            <div><label className="mb-1.5 block text-sm font-medium">超时时间（秒）</label><input type="number" min={10} max={3600} value={timeoutSeconds} onChange={e => setTimeoutSeconds(Math.max(10, Math.min(3600, Number(e.target.value) || 10)))} className="w-full rounded-lg border border-hairline px-3 py-2.5 text-sm" /></div>
          </div>
          {createSession.isError && <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{String((createSession.error as Error)?.message || createSession.error)}</div>}
          <div className="flex justify-end gap-2 border-t border-hairline pt-4"><Link to="/exploration" className="rounded-lg border border-hairline px-4 py-2 text-sm">取消</Link><button disabled={!goal.trim() || !deviceId || createSession.isPending} onClick={() => createSession.mutate()} className="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white disabled:opacity-50">{createSession.isPending ? '保存中…' : '创建探索会话'}</button></div>
        </section>
        <aside className="space-y-3">
          <div className="rounded-xl border border-hairline bg-white p-4"><h3 className="text-sm font-semibold">探索流程</h3><ol className="mt-3 space-y-3 text-xs text-ink-mute"><li>01 · 观察设备页面与 UI 层级</li><li>02 · AI 规划并执行下一步操作</li><li>03 · 记录操作前后状态与证据</li><li>04 · 识别可复用的弹窗和异常处理经验</li><li>05 · 生成计划并进行确定性回放验证</li></ol></div>
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 text-xs leading-5 text-amber-900"><strong>注意：</strong>探索会通过现有设备控制通道执行，并与普通测试任务共用设备独占锁。请确保手机处于可操作状态。</div>
        </aside>
      </div>
    </>}

    {isHistory && <>
      <PageTitle title="探索历史" desc="查看已创建的探索会话及其状态。" />
      {sessionsLoading ? <div className="py-10 text-center text-sm text-ink-faint">正在读取探索记录…</div> : sessions.length === 0 ? <Empty title="暂无探索历史" desc="创建探索会话后，记录会显示在这里。" action={<Link to="/exploration/new" className="text-sm text-primary">新建探索</Link>} /> : <div className="overflow-hidden rounded-xl border border-hairline bg-white"><div className="grid grid-cols-[minmax(0,1fr)_120px_160px] gap-3 border-b border-hairline bg-canvas-soft px-4 py-3 text-xs font-medium text-ink-mute"><span>探索目标</span><span>状态</span><span>创建时间</span></div>{sessions.map(row => <button key={row.id} onClick={() => setSearchParams({ session: row.id })} className={`grid w-full grid-cols-[minmax(0,1fr)_120px_160px] items-center gap-3 border-b border-hairline px-4 py-4 text-left last:border-0 hover:bg-canvas-soft ${selectedSessionId === row.id ? 'bg-primary-soft/40' : ''}`}><div className="min-w-0"><div className="truncate text-sm font-medium">{row.title}</div><div className="mt-1 truncate text-xs text-ink-mute">{row.goal}</div></div><Status value={row.status} /><span className="text-xs text-ink-mute">{new Date(row.created_at).toLocaleString()}</span></button>)}</div>}
      {selectedSession && <section className="rounded-xl border border-hairline bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-sm font-semibold">{selectedSession.title}</div><p className="mt-1 text-xs text-ink-mute">{selectedSession.goal}</p></div><div className="flex items-center gap-2"><Status value={selectedSession.status} />{['queued', 'running'].includes(selectedSession.status) && <button onClick={() => stopSession.mutate(selectedSession.id)} disabled={stopSession.isPending} className="rounded-md border border-red-200 px-3 py-1.5 text-xs text-red-600 disabled:opacity-50">停止探索</button>}</div></div>
        {selectedSession.expected_result && <div className="mt-3 rounded-lg bg-canvas-soft p-3 text-xs"><strong>预期结果：</strong>{selectedSession.expected_result}</div>}{selectedSession.summary && <div className="mt-3 rounded-lg bg-blue-50 p-3 text-xs text-blue-900">{selectedSession.summary}</div>}{selectedSession.error_message && <div className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-700">{selectedSession.error_message}</div>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">探索轨迹</h3><span className="text-xs text-ink-faint">{selectedSession.steps?.length || 0} 个动作事件 · 前后截图与 UI 状态</span></div>{['completed', 'failed'].includes(selectedSession.status) && !!selectedSession.steps?.length && <button onClick={() => generatePlan.mutate(selectedSession.id)} disabled={generatePlan.isPending} className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-white disabled:opacity-50">{generatePlan.isPending ? '生成中…' : '生成执行计划草稿'}</button>}</div>
        {generatePlan.isError && <p className="mt-2 text-xs text-red-600">{String((generatePlan.error as Error)?.message || generatePlan.error)}</p>}
        {generatedPlanId && <div className="mt-2 rounded-lg bg-green-50 p-3 text-xs text-green-800">执行计划草稿已创建：{generatedPlanId}。步骤仍需人工确认操作意图和验证条件。<Link className="ml-2 underline" to="/execution-plans">打开执行计划管理</Link></div>}
        {!selectedSession.steps?.length ? <p className="mt-3 text-xs text-ink-faint">正在等待探索步骤，页面会自动刷新。</p> : <ol className="mt-3 space-y-4">{selectedSession.steps.map(step => <li key={step.id} className="rounded-xl border border-hairline p-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-sm font-semibold">{step.sequence}. {step.title || step.action || step.event_type}</div>{step.detail && <p className="mt-1 text-xs text-ink-mute">{step.detail}</p>}<div className="mt-1 text-[11px] text-ink-faint">动作：{step.action || '—'} · 状态：{step.status} · {step.duration_ms} ms</div></div></div><div className="mt-3 grid gap-3 md:grid-cols-2"><EvidenceImage sessionId={selectedSession.id} filename={step.evidence?.before_screenshot?.split('/').pop()} label="操作前截图" /><EvidenceImage sessionId={selectedSession.id} filename={step.evidence?.after_screenshot?.split('/').pop()} label="操作后截图" /></div>{(step.page_before?.ui_text || step.page_after?.ui_text) && <details className="mt-3 rounded-lg bg-canvas-soft p-3"><summary className="cursor-pointer text-xs font-medium">查看前后 UI 层级信息</summary><div className="mt-2 grid gap-3 md:grid-cols-2"><div><div className="mb-1 text-[11px] font-semibold">操作前</div><pre className="max-h-56 overflow-auto whitespace-pre-wrap text-[11px] text-ink-mute">{step.page_before?.ui_text || '无'}</pre></div><div><div className="mb-1 text-[11px] font-semibold">操作后</div><pre className="max-h-56 overflow-auto whitespace-pre-wrap text-[11px] text-ink-mute">{step.page_after?.ui_text || '无'}</pre></div></div></details>}</li>)}</ol>}
      </section>}
    </>}

    {isKnowledge && <>
      <PageTitle title="探索经验库" desc="沉淀权限弹窗、广告弹窗、登录过期、网络异常等可复用处理经验。" />
      {knowledgeLoading ? <div className="py-10 text-center text-sm text-ink-faint">正在读取经验条目…</div> : knowledge.length === 0 ? <Empty title="经验库还是空的" desc="探索过程中发现的复杂场景将在这里形成经验候选，审核确认后才能供执行计划复用。" /> : <div className="grid gap-3 md:grid-cols-2">{knowledge.map(item => <article key={item.id} className="rounded-xl border border-hairline bg-white p-4"><div className="flex items-start justify-between gap-2"><h3 className="text-sm font-semibold">{item.title}</h3><Status value={item.status} /></div><textarea key={item.id + item.description} defaultValue={item.description} aria-label="经验说明" onBlur={e => { if (e.target.value !== item.description) updateKnowledge.mutate({ id: item.id, description: e.target.value }) }} rows={3} className="mt-2 w-full resize-y rounded-lg border border-hairline bg-white px-3 py-2 text-xs leading-5 text-ink-mute" />{item.source_session_id && item.evidence?.after_screenshot && <div className="mt-3 max-w-sm"><EvidenceImage sessionId={item.source_session_id} filename={item.evidence.after_screenshot.split('/').pop()} label="场景证据" /></div>}<details className="mt-3 rounded-lg bg-canvas-soft p-3"><summary className="cursor-pointer text-xs font-medium">识别依据与处理建议</summary><pre className="mt-2 whitespace-pre-wrap text-[11px] text-ink-mute">{JSON.stringify({ trigger: item.trigger_conditions, recognition: item.recognition_rules, handling: item.handling_rules }, null, 2)}</pre></details><div className="mt-3 flex flex-wrap gap-2 text-[11px] text-ink-faint"><span>{item.category}</span>{item.app_package && <span>· {item.app_package}</span>}<span>· 风险：{item.risk_level}</span></div><div className="mt-4 flex flex-wrap gap-2">{item.status !== 'confirmed' && item.status !== 'disabled' && <><button onClick={() => reviewKnowledge.mutate({ id: item.id, status: 'confirmed' })} className="rounded-md bg-primary px-3 py-1.5 text-xs text-white">确认并允许作为参考</button><button onClick={() => reviewKnowledge.mutate({ id: item.id, status: 'invalidated' })} className="rounded-md border border-hairline px-3 py-1.5 text-xs">标记无效</button></>}{item.status !== 'disabled' && <button onClick={() => reviewKnowledge.mutate({ id: item.id, status: 'disabled' })} className="rounded-md border border-hairline px-3 py-1.5 text-xs">停用经验</button>}</div></article>)}</div>}
    </>}

    {isPlans && <PlanReviewWorkspace />}
  </div>
}
