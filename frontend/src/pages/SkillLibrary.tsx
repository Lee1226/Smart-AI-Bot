import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

type ExploreSession = {
  id: string
  title: string
  goal: string
  expected_result: string
  status: string
  platform: string
  app_package: string
  created_at: string
}

type Finding = {
  severity: string
  code: string
  message: string
  step?: number
}

type Validation = {
  id: string
  skill_version: number
  validation_type: string
  status: 'passed' | 'needs_review' | 'failed'
  summary: { error_count?: number; review_count?: number; step_count?: number; evidence_count?: number }
  findings: Finding[]
  device_touched: boolean
  created_at: string
}

type SkillStep = {
  source_event_id?: string
  sequence?: number
  action?: string
  args?: Record<string, unknown>
  description?: string
  decision_summary?: string
  page_before_excerpt?: string
  page_after_excerpt?: string
  expected_transition?: Record<string, unknown>
  wait_after_ms?: number
  verification?: { available?: boolean; type?: string; note?: string }
}

type Evidence = {
  event_id?: string
  sequence?: number
  kind?: string
  path?: string
}

type Teaching = {
  source_prompt?: string
  expected_outcome?: string
  task_intent?: string
  decision_records?: Array<{ sequence?: number; action?: string; summary?: string }>
  observations?: Array<{ sequence?: number; description?: string; visible_text?: string; screenshot?: string }>
  recovery_experiences?: Array<{ source_event_id?: string; sequence?: number; action?: string; result?: string; handling?: string }>
  execution_guidance?: string[]
  knowledge_completeness?: Record<string, boolean>
  notice?: string
}

type Skill = {
  id: string
  source_session_id: string
  name: string
  description: string
  category: string
  platform: string
  app_package: string
  preconditions: Array<Record<string, unknown>>
  recognition: Record<string, unknown> & { teaching?: Teaching }
  steps: SkillStep[]
  branches: Array<Record<string, unknown>>
  postconditions: Array<Record<string, unknown>>
  safety: Record<string, unknown>
  evidence: Evidence[]
  warnings: string[]
  status: string
  version: number
  review_note: string
  created_at: string
  updated_at: string
}

const statusText: Record<string, string> = {
  draft: '待确认',
  pending_review: '审核中',
  approved: '已审核 · 尚不可执行',
  disabled: '已停用',
}

function prettyAction(action: string): string {
  const labels: Record<string, string> = {
    start_app: '启动应用',
    launch_app: '启动应用',
    tap: '点击控件',
    click: '点击控件',
    swipe: '滑动页面',
    scroll: '滚动页面',
    input_text: '输入内容',
    wait: '等待页面状态',
    back: '返回',
    home: '回到桌面',
    press_key: '按下按键',
    recent: '打开最近任务',
    stop_app: '停止应用',
    global_action: '系统操作',
  }
  return labels[action] || action || '未识别动作'
}

function SkillCard({ skill }: { skill: Skill }) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const validationsQuery = useQuery({
    queryKey: ['skill-validations', skill.id],
    queryFn: () => api.get<Validation[]>(`/skills/${skill.id}/validations`).then(response => response.data),
    enabled: expanded,
  })
  const validate = useMutation({
    mutationFn: () => api.post<Validation>(`/skills/${skill.id}/validate-structure`).then(response => response.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skill-validations', skill.id] }),
  })
  const review = useMutation({
    mutationFn: (status: string) => api.patch(`/skills/${skill.id}`, { status }).then(response => response.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reusable-skills'] }),
  })
  const teaching = skill.recognition.teaching || {}
  const completeness = teaching.knowledge_completeness || {}
  const prompt = teaching.source_prompt || skill.description
  const steps = skill.steps || []

  return <article className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_8px_28px_rgba(31,75,135,0.05)]">
    <div className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button className="min-w-0 flex-1 text-left" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
          <div className="flex items-center gap-2">
            <span className="text-sm text-ink-faint">{expanded ? '⌄' : '›'}</span>
            <h3 className="text-base font-semibold text-ink">{skill.name || '未命名技能'}</h3>
          </div>
          <p className="mt-2 line-clamp-2 text-sm leading-6 text-ink-mute">{prompt || '暂无原始任务提示词'}</p>
        </button>
        <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${
          skill.status === 'approved' ? 'bg-emerald-50 text-emerald-700' :
          skill.status === 'disabled' ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-800'
        }`}>{statusText[skill.status] || skill.status}</span>
      </div>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-faint">
        <span>{skill.platform || '平台未限定'}</span>
        <span>{skill.app_package || '应用未限定'}</span>
        <span>{steps.length} 个已记录动作</span>
        <span>{skill.evidence.length} 条证据</span>
        <span>技能 v{skill.version}</span>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button onClick={() => setExpanded(value => !value)} className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-medium text-ink-mute">
          {expanded ? '收起技能详情' : '查看 AI 学习内容'}
        </button>
        {skill.status !== 'approved' && <button disabled={review.isPending} onClick={() => review.mutate('approved')} className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-white disabled:opacity-50">确认技能内容</button>}
        {skill.status !== 'disabled' && <button disabled={review.isPending} onClick={() => review.mutate('disabled')} className="rounded-lg border border-hairline px-3 py-2 text-xs text-ink-mute disabled:opacity-50">停用</button>}
      </div>
    </div>

    {expanded && <div className="space-y-6 border-t border-hairline bg-slate-50/60 p-5">
      <section>
        <div className="mb-2 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold text-ink">01 · 原始任务提示词</h4>
          <span className="text-xs text-ink-faint">探索时用户的原始输入</span>
        </div>
        <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-4 text-sm leading-7 text-slate-800 whitespace-pre-wrap">{prompt || '原始提示词未保存，不能从标题反向补造。'}</div>
        <div className="mt-3 rounded-xl border border-hairline bg-white p-4">
          <h5 className="text-xs font-semibold text-ink-mute">任务目标与完成标准</h5>
          <p className="mt-2 text-sm leading-6 text-ink">{teaching.task_intent || '尚未提炼任务意图'}</p>
          <p className="mt-2 text-sm leading-6 text-ink-mute">{teaching.expected_outcome || '用户尚未填写明确的预期结果'}</p>
        </div>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold text-ink">02 · AI 从探索中学到了什么</h4>
          <span className="text-xs text-ink-faint">{(teaching.observations || []).length} 次观察 · {(teaching.decision_records || []).length} 条决策记录</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-hairline bg-white p-4">
            <h5 className="text-xs font-semibold text-ink-mute">页面观察</h5>
            {(teaching.observations || []).length ? <ol className="mt-3 space-y-3">{(teaching.observations || []).map((item, index) => <li key={`${item.sequence ?? index}-observation`} className="border-l-2 border-blue-200 pl-3">
              <p className="text-xs font-medium text-ink">{item.description || `第 ${index + 1} 次页面观察`}</p>
              <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-ink-mute">{item.visible_text || '本次观察未保存可读页面文本'}</p>
              {item.screenshot && <p className="mt-1 break-all text-[11px] text-ink-faint">截图证据：{item.screenshot}</p>}
            </li>)}</ol> : <p className="mt-3 text-xs text-ink-faint">探索记录没有保存独立观察事件。</p>}
          </div>
          <div className="rounded-xl border border-hairline bg-white p-4">
            <h5 className="text-xs font-semibold text-ink-mute">AI 操作决策摘要</h5>
            {(teaching.decision_records || []).length ? <ol className="mt-3 space-y-3">{(teaching.decision_records || []).map((item, index) => <li key={`${item.sequence ?? index}-decision`} className="border-l-2 border-violet-200 pl-3">
              <p className="text-xs font-medium text-ink">{prettyAction(item.action || '')}</p>
              <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-ink-mute">{item.summary || '探索记录未保存决策摘要'}</p>
            </li>)}</ol> : <p className="mt-3 text-xs text-ink-faint">没有持久化的决策说明；系统不会推测模型的内部思考。</p>}
          </div>
        </div>
      </section>

      <section>
        <h4 className="mb-3 text-sm font-semibold text-ink">03 · 教 AI 如何重复执行</h4>
        {steps.length ? <ol className="space-y-3">{steps.map((step, index) => <li key={step.source_event_id || index} className="rounded-xl border border-hairline bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-ink">{index + 1}</span><span className="text-sm font-semibold text-ink">{step.description || prettyAction(step.action || '')}</span></div>
            <span className="rounded-md bg-slate-100 px-2 py-1 text-[11px] text-ink-mute">{prettyAction(step.action || '')}</span>
          </div>
          {step.decision_summary && <p className="mt-3 text-sm leading-6 text-ink-mute"><b>当时的决策依据：</b>{step.decision_summary}</p>}
          {step.page_before_excerpt && <div className="mt-3 rounded-lg bg-slate-50 p-3"><p className="text-[11px] font-semibold text-ink-faint">操作前页面</p><p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-ink-mute">{step.page_before_excerpt}</p></div>}
          {step.page_after_excerpt && <div className="mt-2 rounded-lg bg-emerald-50/60 p-3"><p className="text-[11px] font-semibold text-emerald-800">操作后页面</p><p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-ink-mute">{step.page_after_excerpt}</p></div>}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className={step.verification?.available ? 'text-emerald-700' : 'text-amber-700'}>{step.verification?.available ? '有操作后页面证据' : '缺少自动验证依据'}</span>
            <span className="text-ink-faint">{step.verification?.note || '需要人工确认本步骤的成功条件'}</span>
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-ink-faint">查看底层动作参数</summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{JSON.stringify(step.args || {}, null, 2)}</pre>
          </details>
        </li>)}</ol> : <p className="rounded-xl border border-dashed border-hairline bg-white p-4 text-sm text-ink-faint">没有可复用的成功动作，请检查探索时间轴。</p>}
      </section>

      <section>
        <h4 className="mb-3 text-sm font-semibold text-ink">04 · 异常与安全经验</h4>
        {(teaching.recovery_experiences || []).length ? <div className="space-y-2">{(teaching.recovery_experiences || []).map((item, index) => <div key={item.source_event_id || index} className="rounded-xl border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs font-semibold text-amber-900">{prettyAction(item.action || '')} · 第 {item.sequence || '?'} 条记录</p>
          <p className="mt-1 text-xs text-amber-900">{item.result || '失败结果未记录'}</p>
          <p className="mt-1 text-xs text-amber-800">{item.handling || '需要人工分析处理方式'}</p>
        </div>)}</div> : <p className="rounded-xl border border-hairline bg-white p-3 text-xs text-ink-faint">本次探索没有记录失败工具调用。没有失败记录不代表覆盖了所有异常。</p>}
        <ul className="mt-3 list-disc space-y-1 pl-5 text-xs leading-5 text-ink-mute">
          {(teaching.execution_guidance || []).map((item, index) => <li key={index}>{item}</li>)}
        </ul>
      </section>

      <section>
        <h4 className="mb-3 text-sm font-semibold text-ink">05 · 技能完整度与验证</h4>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {[
            ['原始提示词', completeness.has_original_prompt],
            ['预期结果', completeness.has_expected_outcome],
            ['页面观察', completeness.has_observations],
            ['决策记录', completeness.has_decision_records],
            ['异常经验', completeness.has_failure_experiences],
            ['截图证据', completeness.has_action_evidence],
          ].map(([label, ready]) => <div key={String(label)} className="flex items-center justify-between rounded-lg border border-hairline bg-white px-3 py-2 text-xs">
            <span className="text-ink-mute">{label}</span><span className={ready ? 'font-medium text-emerald-700' : 'font-medium text-amber-700'}>{ready ? '已记录' : '待补充'}</span>
          </div>)}
        </div>
        <div className="mt-3 rounded-xl border border-hairline bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h5 className="text-sm font-semibold text-ink">结构校验</h5><p className="mt-1 text-xs text-ink-faint">只检查技能定义，不连接设备、不执行手机操作。</p></div>
            <button disabled={validate.isPending} onClick={() => validate.mutate()} className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-white disabled:opacity-50">{validate.isPending ? '校验中…' : '运行结构校验'}</button>
          </div>
          {validate.isError && <p className="mt-2 text-xs text-red-600">{String(validate.error)}</p>}
          {validationsQuery.data?.map(run => <div key={run.id} className="mt-3 rounded-lg bg-slate-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs"><b>{run.status === 'passed' ? '结构完整' : run.status === 'needs_review' ? '需要人工补充' : '校验未通过'}</b><span className="text-ink-faint">技能 v{run.skill_version} · {new Date(run.created_at).toLocaleString()}</span></div>
            <p className="mt-1 text-xs text-ink-mute">步骤 {run.summary.step_count || 0} · 证据 {run.summary.evidence_count || 0} · 错误 {run.summary.error_count || 0} · 待检查 {run.summary.review_count || 0}</p>
            {run.findings.map((finding, index) => <p key={`${finding.code}-${index}`} className="mt-1 text-xs text-ink-mute">{finding.step ? `第 ${finding.step} 步：` : ''}{finding.message}</p>)}
            <p className="mt-2 text-[11px] text-ink-faint">设备未触碰 · 结构校验不等于真机复现通过</p>
          </div>)}
          {validationsQuery.data?.length === 0 && <p className="mt-2 text-xs text-ink-faint">暂无校验历史。</p>}
        </div>
      </section>
      <section className="text-xs text-ink-faint">来源探索记录：{skill.source_session_id} · 证据引用 {skill.evidence.length} 条。{teaching.notice || ''}</section>
      <p className="text-xs text-amber-800">当前技能仅完成知识提炼与审核管理，尚未接入正式执行器；审核通过不代表可以自动操作手机。</p>
    </div>}
  </article>
}

export default function SkillLibrary() {
  const qc = useQueryClient()
  const [sessionId, setSessionId] = useState('')
  const [filter, setFilter] = useState('all')
  const sessionsQuery = useQuery({
    queryKey: ['skill-source-sessions'],
    queryFn: () => api.get<ExploreSession[]>('/exploration/sessions', { params: { status: 'completed', limit: 100 } }).then(response => response.data),
  })
  const skillsQuery = useQuery({
    queryKey: ['reusable-skills'],
    queryFn: () => api.get<Skill[]>('/skills', { params: { limit: 200 } }).then(response => response.data),
  })
  const create = useMutation({
    mutationFn: () => api.post<Skill>(`/skills/from-exploration/${sessionId}`).then(response => response.data),
    onSuccess: () => { setSessionId(''); qc.invalidateQueries({ queryKey: ['reusable-skills'] }) },
  })
  const sessions = sessionsQuery.data || []
  const skills = skillsQuery.data || []
  const visible = useMemo(
    () => filter === 'all' ? skills : skills.filter(skill => skill.status === filter),
    [skills, filter],
  )

  return <div className="mx-auto max-w-7xl space-y-6">
    <header className="rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] p-5 text-white shadow-[0_16px_40px_rgba(37,99,235,0.18)] sm:p-6">
      <div className="text-xs font-semibold tracking-[0.18em] text-blue-100">AI 终端 · 技能资产管理</div><h1 className="mt-2 text-xl font-bold text-white">AI 技能库</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-blue-100">把一次成功探索沉淀为可重复使用的 AI 执行教学：保留原始任务提示词，提取页面观察、操作决策、成功步骤与异常经验。未经复现验证的技能不会被当作可执行能力。</p>
    </header>

    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-base font-semibold text-ink">从已完成探索中学习</h2><p className="mt-1 text-xs leading-5 text-ink-mute">选择一条探索记录，系统将整合用户提示词、完整事件时间轴和证据，生成待确认的技能教学稿。</p></div>
        <span className="rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-700">探索 → 提炼 → 确认 → 复现验证</span>
      </div>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <select aria-label="选择探索记录" className="min-w-0 flex-1 rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm" value={sessionId} onChange={event => setSessionId(event.target.value)}>
          <option value="">请选择已完成的探索记录</option>
          {sessions.map(session => <option key={session.id} value={session.id}>{session.title || session.goal} · {session.platform} · {new Date(session.created_at).toLocaleString()}</option>)}
        </select>
        <button disabled={!sessionId || create.isPending} onClick={() => create.mutate()} className="rounded-xl bg-gradient-to-r from-[#2169D5] to-[#328FE5] px-5 py-2.5 text-sm font-semibold text-white shadow-sm disabled:opacity-50">{create.isPending ? '正在提炼…' : '提炼 AI 执行技能'}</button>
      </div>
      {sessionsQuery.isLoading && <p className="mt-2 text-xs text-ink-faint">正在读取探索记录…</p>}
      {sessionsQuery.isError && <p className="mt-2 text-xs text-red-600">读取探索记录失败，请检查后端服务。</p>}
      {create.isError && <p className="mt-2 text-xs text-red-600">技能提炼失败，请检查探索记录及后端日志。</p>}
      {create.isSuccess && <p className="mt-2 text-xs text-emerald-700">技能教学稿已生成，请展开检查来源提示词、步骤和证据。</p>}
      {!sessionsQuery.isLoading && sessions.length === 0 && <p className="mt-3 text-xs text-ink-faint">暂无已完成的探索记录。</p>}
    </section>

    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-base font-semibold text-ink">已提炼的技能</h2><p className="mt-1 text-xs text-ink-faint">{skills.length} 条技能 · 按最近更新排序</p></div>
        <select aria-label="技能状态筛选" className="rounded-lg border border-hairline bg-white px-3 py-2 text-sm" value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="all">全部状态</option><option value="draft">待确认</option><option value="pending_review">审核中</option><option value="approved">已审核</option><option value="disabled">已停用</option>
        </select>
      </div>
      {skillsQuery.isLoading ? <p className="py-10 text-center text-sm text-ink-faint">正在加载技能库…</p> :
        skillsQuery.isError ? <p className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700">技能库读取失败，请检查后端服务。</p> :
        visible.length ? <div className="space-y-4">{visible.map(skill => <SkillCard key={skill.id} skill={skill} />)}</div> :
        <div className="rounded-2xl border border-dashed border-hairline bg-white px-5 py-14 text-center"><p className="text-sm font-semibold text-ink">还没有符合条件的技能</p><p className="mt-2 text-xs text-ink-mute">从上方选择一条已完成的探索记录，提炼第一份 AI 执行技能。</p></div>}
    </section>
  </div>
}
