import { useMemo, useState } from 'react'
import { useState } from 'react'\nimport { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

type ExploreSession = { id: string; title: string; goal: string; status: string; platform: string; app_package: string; created_at: string }
type Validation = {
  id: string; skill_version: number; validation_type: string; status: 'passed' | 'needs_review' | 'failed'
  summary: { error_count?: number; review_count?: number; step_count?: number; evidence_count?: number }
  findings: Array<{ severity: string; code: string; message: string; step?: number }>
  device_touched: boolean; created_at: string
}
type Skill = {
  id: string; source_session_id: string; name: string; description: string; category: string
  platform: string; app_package: string; preconditions: Array<Record<string, unknown>>
  recognition: Record<string, unknown>; steps: Array<Record<string, any>>
  branches: Array<Record<string, unknown>>; postconditions: Array<Record<string, unknown>>
  safety: Record<string, any>; evidence: Array<Record<string, unknown>>
  warnings: string[]; status: string; version: number; review_note: string
  created_at: string; updated_at: string
}

const statusText: Record<string, string> = {
  draft: '待审核', pending_review: '审核中', approved: '已审核（尚未启用执行）', disabled: '已停用',
}

function SkillCard({ skill }: { skill: Skill }) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)\n  const validationsQuery = useQuery({ queryKey: ['skill-validations', skill.id], queryFn: () => api.get<Validation[]>(`/skills/${skill.id}/validations`).then(r => r.data), enabled: expanded })\n  const validate = useMutation({ mutationFn: () => api.post<Validation>(`/skills/${skill.id}/validate-structure`).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['skill-validations', skill.id] }) })
  const review = useMutation({
    mutationFn: (status: string) => api.patch(`/skills/${skill.id}`, { status }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reusable-skills'] }),
  })
  return <article className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <button className="min-w-0 flex-1 text-left" onClick={() => setExpanded(v => !v)}>
        <div className="flex items-center gap-2"><span className="text-ink-faint">{expanded ? '▾' : '▸'}</span><span className="font-semibold text-ink">{skill.name}</span></div>
        <p className="mt-1 line-clamp-2 text-sm text-ink-mute">{skill.description || '暂无描述'}</p>
      </button>
      <span className={`rounded-full px-2.5 py-1 text-xs ${skill.status === 'approved' ? 'bg-green-50 text-green-700' : skill.status === 'disabled' ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-700'}`}>{statusText[skill.status] || skill.status}</span>
    </div>
    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
      <span>{skill.platform || '平台未标注'}</span><span>{skill.app_package || '应用包名未标注'}</span>
      <span>{skill.steps.length} 个动作步骤</span><span>v{skill.version}</span>
    </div>
    {expanded && <div className="mt-4 space-y-4 border-t border-hairline pt-4">
      <section><h3 className="mb-2 text-sm font-semibold">执行步骤草稿</h3>
        {skill.steps.length ? <ol className="space-y-2">{skill.steps.map((step, index) => <li key={String(step.source_event_id || index)} className="rounded-lg bg-canvas-soft p-3">
          <div className="flex items-center justify-between gap-2 text-sm"><b>{index + 1}. {String(step.description || step.action)}</b><code className="text-xs text-primary-deep">{String(step.action)}</code></div>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs text-ink-mute">{JSON.stringify(step.args || {}, null, 2)}</pre>
          <p className="mt-1 text-xs text-ink-faint">{String(step.verification?.note || '')}</p>
        </li>)}</ol> : <p className="text-sm text-ink-faint">没有提炼出可复用动作</p>}
      </section>
      <section><h3 className="mb-1 text-sm font-semibold">前置条件与页面识别</h3><pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-canvas-soft p-3 text-xs">{JSON.stringify({ preconditions: skill.preconditions, recognition: skill.recognition }, null, 2)}</pre></section>
      <section className="rounded-lg border border-hairline p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h3 className="text-sm font-semibold">结构校验</h3><p className="mt-1 text-xs text-ink-faint">仅检查技能定义与证据引用，不连接设备、不执行手机操作。</p></div>
          <button disabled={validate.isPending} onClick={() => validate.mutate()} className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">{validate.isPending ? '校验中…' : '运行结构校验'}</button>
        </div>
        {validate.isError && <p className="mt-2 text-xs text-red-600">{String(validate.error)}</p>}
        {validationsQuery.data?.length ? <div className="mt-3 space-y-2">
          {validationsQuery.data.map(run => <div key={run.id} className="rounded-md bg-canvas-soft p-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <b>{run.status === 'passed' ? '结构完整' : run.status === 'needs_review' ? '需要人工补充' : '校验未通过'}</b>
              <span className="text-ink-faint">技能 v{run.skill_version} · {new Date(run.created_at).toLocaleString()}</span>
            </div>
            <p className="mt-1 text-xs text-ink-mute">步骤 {run.summary.step_count || 0} · 证据 {run.summary.evidence_count || 0} · 错误 {run.summary.error_count || 0} · 待检查 {run.summary.review_count || 0}</p>
            {run.findings.length > 0 && <ul className="mt-2 space-y-1 text-xs text-ink-mute">{run.findings.map((finding, i) => <li key={i}>• {finding.step ? `第 ${finding.step} 步：` : ''}{finding.message}</li>)}</ul>}
            <p className="mt-2 text-[11px] text-ink-faint">设备未触碰 · 此结果不代表真机验证通过</p>
          </div>)}
        </div> : expanded && <p className="mt-2 text-xs text-ink-faint">暂无校验历史。</p>}
      </section>
      <section><h3 className="mb-1 text-sm font-semibold">结果校验</h3><pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-canvas-soft p-3 text-xs">{JSON.stringify(skill.postconditions, null, 2)}</pre></section>
      {skill.warnings.length > 0 && <section className="rounded-lg border border-amber-200 bg-amber-50 p-3"><h3 className="mb-1 text-sm font-semibold text-amber-900">需要补充 / 人工检查</h3><ul className="list-disc space-y-1 pl-5 text-xs text-amber-800">{skill.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></section>}
      <p className="text-xs text-ink-faint">来源探索记录：{skill.source_session_id} · 证据引用 {skill.evidence.length} 条</p>
      <div className="flex flex-wrap gap-2">
        {skill.status !== 'approved' && <button disabled={review.isPending} onClick={() => review.mutate('approved')} className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">审核通过</button>}
        {skill.status !== 'disabled' && <button disabled={review.isPending} onClick={() => review.mutate('disabled')} className="rounded-md border border-hairline px-3 py-1.5 text-xs text-ink-mute disabled:opacity-50">停用</button>}
        {skill.status !== 'draft' && <button disabled={review.isPending} onClick={() => review.mutate('draft')} className="rounded-md border border-hairline px-3 py-1.5 text-xs text-ink-mute disabled:opacity-50">退回草稿</button>}
      </div>
      <p className="text-xs text-ink-faint">审核通过仅表示知识已审核；当前版本尚未接入执行器，不会自动操作手机。</p>
    </div>}
  </article>
}

export default function SkillLibrary() {
  const t = useT()
  const qc = useQueryClient()
  const [sessionId, setSessionId] = useState('')
  const [filter, setFilter] = useState('all')
  const sessionsQuery = useQuery({
    queryKey: ['skill-source-sessions'],
    queryFn: () => api.get<ExploreSession[]>('/exploration/sessions', { params: { status: 'completed', limit: 100 } }).then(r => r.data),
  })
  const skillsQuery = useQuery({
    queryKey: ['reusable-skills'],
    queryFn: () => api.get<Skill[]>('/skills', { params: { limit: 200 } }).then(r => r.data),
  })
  const create = useMutation({
    mutationFn: () => api.post<Skill>(`/skills/from-exploration/${sessionId}`).then(r => r.data),
    onSuccess: () => { setSessionId(''); qc.invalidateQueries({ queryKey: ['reusable-skills'] }) },
  })
  const sessions = sessionsQuery.data || []
  const skills = skillsQuery.data || []
  const visible = useMemo(() => filter === 'all' ? skills : skills.filter(s => s.status === filter), [skills, filter])
  return <div className="max-w-6xl space-y-6">
    <header><h1 className="text-2xl font-bold text-ink">技能库</h1><p className="mt-1 text-sm text-ink-mute">把 AI 探索过程提炼成可审核、可追溯的结构化技能。当前仅生成和管理技能草稿，不会自动执行。</p></header>
    <section className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
      <h2 className="font-semibold text-ink">从探索记录提炼技能</h2>
      <p className="mt-1 text-xs text-ink-mute">选择已完成的探索记录。系统会提取成功动作、页面特征和截图证据；无法可靠还原的步骤会标记为待补充，不会猜测。</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <select aria-label="选择探索记录" className="min-w-0 flex-1 rounded-md border border-hairline px-3 py-2 text-sm" value={sessionId} onChange={e => setSessionId(e.target.value)}>
          <option value="">请选择已完成的探索记录</option>
          {sessions.map(s => <option key={s.id} value={s.id}>{s.title || s.goal} · {s.platform} · {new Date(s.created_at).toLocaleString()}</option>)}
        </select>
        <button disabled={!sessionId || create.isPending} onClick={() => create.mutate()} className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{create.isPending ? '提炼中…' : '生成技能草稿'}</button>
      </div>
      {sessionsQuery.isLoading && <p className="mt-2 text-xs text-ink-faint">正在读取探索记录…</p>}
      {create.isError && <p className="mt-2 text-xs text-red-600">{String(create.error)}</p>}
      {create.isSuccess && <p className="mt-2 text-xs text-green-700">技能草稿已生成，可在下方展开检查。</p>}
      {!sessionsQuery.isLoading && sessions.length === 0 && <p className="mt-2 text-xs text-ink-faint">暂无已完成的探索记录。</p>}
    </section>
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold text-ink">已提炼技能 <span className="ml-1 text-xs font-normal text-ink-faint">{skills.length} 条</span></h2>
        <select aria-label="技能状态筛选" className="rounded-md border border-hairline px-3 py-1.5 text-sm" value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="all">全部状态</option><option value="draft">待审核</option><option value="pending_review">审核中</option><option value="approved">已审核</option><option value="disabled">已停用</option>
        </select>
      </div>
      {skillsQuery.isLoading ? <p className="py-8 text-center text-sm text-ink-faint">加载技能库…</p> :
        visible.length ? <div className="space-y-3">{visible.map(skill => <SkillCard key={skill.id} skill={skill} />)}</div> :
          <div className="rounded-xl border border-dashed border-hairline bg-white px-5 py-12 text-center"><p className="text-sm font-medium text-ink">还没有技能</p><p className="mt-1 text-xs text-ink-mute">从上方选择一条探索记录，生成第一份技能草稿。</p></div>}
    </section>
  </div>
}
