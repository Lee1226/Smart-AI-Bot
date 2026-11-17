import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createExecutionPlanVersion, ExecutionPlanStep,
  fetchExecutionPlans, fetchSuiteCases, importLegacyExecutionPlan,
} from '../lib/api'
import { useT } from '../lib/i18n'

const STATUS_STYLE: Record<string, string> = {
  draft: 'bg-amber-100 text-amber-700',
  validated: 'bg-green-100 text-green-700',
  deprecated: 'bg-gray-100 text-gray-500',
}

const ACTIONS = [
  { value: 'tap', label: '点击控件' },
  { value: 'input_text', label: '输入文本' },
  { value: 'swipe', label: '滑动屏幕' },
  { value: 'scroll', label: '滚动页面' },
  { value: 'start_app', label: '系统启动应用' },
  { value: 'press_key', label: '按下按键' },
  { value: 'global_action', label: '系统操作' },
  { value: 'tap_element', label: '点击页面元素（旧格式）' },
]

const fieldClass = 'w-full min-w-0 rounded-md border border-gray-200 bg-white px-2.5 py-2 text-sm text-gray-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100'
const labelClass = 'mb-1 block text-[11px] font-medium text-gray-500'

function asNumber(value: unknown): string {
  return value === undefined || value === null ? '' : String(value)
}

export default function ExecutionPlanManager({ suiteId, platform }: { suiteId: string; platform: string }) {
  const t = useT()
  const qc = useQueryClient()
  const [caseId, setCaseId] = useState('')
  const [planId, setPlanId] = useState('')
  const [editing, setEditing] = useState(false)
  const [draftSteps, setDraftSteps] = useState<ExecutionPlanStep[]>([])
  const [editError, setEditError] = useState('')

  const { data: cases = [], isLoading: casesLoading } = useQuery({
    queryKey: ['suite-cases', suiteId],
    queryFn: () => fetchSuiteCases(suiteId),
  })
  const selectedCase = cases.find(c => c.id === caseId) || cases[0]
  const activeCaseId = selectedCase?.id || ''
  const { data: plans = [], isLoading: plansLoading } = useQuery({
    queryKey: ['execution-plans', activeCaseId],
    queryFn: () => fetchExecutionPlans(activeCaseId),
    enabled: !!activeCaseId,
  })
  const selectedPlan = plans.find(p => p.id === planId) || plans[0]

  const invalidate = async () => {
    await qc.invalidateQueries({ queryKey: ['execution-plans', activeCaseId] })
  }
  const importMut = useMutation({
    mutationFn: () => importLegacyExecutionPlan(activeCaseId, platform),
    onSuccess: async plan => {
      setPlanId(plan.id)
      setEditing(false)
      await invalidate()
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(t(`生成失败：${msg}`, `Generation failed: ${msg}`))
    },
  })
  const saveMut = useMutation({
    mutationFn: (body: { steps: ExecutionPlanStep[]; metadata: Record<string, unknown>; warnings: string[]; platform: string }) =>
      createExecutionPlanVersion(selectedPlan!.id, body),
    onSuccess: async plan => {
      setPlanId(plan.id)
      setEditing(false)
      setEditError('')
      await invalidate()
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(t(`保存新版本失败：${msg}`, `Failed to save new version: ${msg}`))
    },
  })

  const beginEdit = () => {
    if (!selectedPlan) return
    setDraftSteps(selectedPlan.steps.map(step => ({
      ...step,
      args: { ...(step.args || {}) },
      locator: { ...(step.locator || {}) },
      fallback: step.fallback ? { ...step.fallback } : null,
      wait_condition: step.wait_condition ? { ...step.wait_condition } : null,
      verification: step.verification ? { ...step.verification } : null,
    })))
    setEditError('')
    setEditing(true)
  }

  const patchStep = (index: number, patch: Partial<ExecutionPlanStep>) => {
    setDraftSteps(current => current.map((step, i) => i === index ? { ...step, ...patch } : step))
  }
  const patchArgs = (index: number, key: string, value: unknown) => {
    const step = draftSteps[index]
    patchStep(index, { args: { ...(step.args || {}), [key]: value } })
  }
  const patchLocator = (index: number, key: string, value: unknown) => {
    const step = draftSteps[index]
    patchStep(index, { locator: { ...(step.locator || {}), [key]: value } })
  }
  const patchCoordinate = (index: number, key: string, value: string) => {
    const step = draftSteps[index]
    const fallback = (step.fallback || { type: 'coordinate', coordinates: {} }) as Record<string, unknown>
    const coordinates = { ...((fallback.coordinates as Record<string, unknown>) || {}), [key]: value === '' ? '' : Number(value) }
    patchStep(index, { fallback: { ...fallback, type: 'coordinate', coordinates } })
  }
  const patchWait = (index: number, value: string) => {
    patchStep(index, { wait_condition: value === '' ? null : { type: 'delay', duration_ms: Math.max(0, Number(value) || 0) } })
  }
  const moveStep = (index: number, delta: number) => {
    setDraftSteps(current => {
      const next = [...current]
      const target = index + delta
      if (target < 0 || target >= next.length) return current
      ;[next[index], next[target]] = [next[target], next[index]]
      return next.map((step, i) => ({ ...step, sequence: i + 1 }))
    })
  }
  const addStep = () => {
    setDraftSteps(current => [...current, {
      step_id: `manual_${Date.now()}`, sequence: current.length + 1,
      action: 'tap', args: {}, executor: 'deterministic',
      locator: {}, fallback: null, precondition: null,
      wait_condition: null, verification: null, on_failure: 'fail',
      import_status: 'manual',
    }])
  }
  const removeStep = (index: number) => {
    setDraftSteps(current => current.filter((_, i) => i !== index).map((step, i) => ({ ...step, sequence: i + 1 })))
  }
  const saveVersion = () => {
    if (draftSteps.length === 0) {
      setEditError(t('至少保留一个执行步骤。', 'Keep at least one execution step.'))
      return
    }
    const invalid = draftSteps.findIndex(step => !step.action || !step.executor)
    if (invalid >= 0) {
      setEditError(t(`第 ${invalid + 1} 步缺少操作类型或执行策略。第 ${invalid + 1} step needs an action and execution strategy.`, `Step ${invalid + 1} needs an action and execution strategy.`))
      return
    }
    setEditError('')
    saveMut.mutate({
      steps: draftSteps.map((step, i) => ({ ...step, sequence: i + 1 })),
      metadata: selectedPlan?.metadata || {},
      warnings: selectedPlan?.warnings || [],
      platform: selectedPlan?.platform || platform,
    })
  }

  if (casesLoading) return <div className="mt-6 text-sm text-gray-400">{t('加载执行计划…', 'Loading execution plans…')}</div>
  if (cases.length === 0) return null

  return (
    <section className="mt-6 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-slate-50 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-800">{t('执行计划', 'Execution plans')}</h2>
          <p className="mt-0.5 text-xs text-gray-500">{t('检查和调整自动化步骤，无需编辑 JSON。', 'Review and adjust automation steps without editing JSON.')}</p>
        </div>
        <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[11px] text-blue-700">{t('可视化编辑', 'Visual editor')}</span>
      </div>
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs font-medium text-gray-500">{t('录制用例', 'Recorded case')}</label>
          <select className="min-w-[220px] flex-1 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm"
            value={activeCaseId} onChange={e => { setCaseId(e.target.value); setPlanId(''); setEditing(false) }}>
            {cases.map(c => <option key={c.id} value={c.id}>{c.path || `#${c.order}`}</option>)}
          </select>
          {!selectedPlan && (
            <button className="rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              disabled={!activeCaseId || importMut.isPending} onClick={() => importMut.mutate()}>
              {importMut.isPending ? t('生成中…', 'Generating…') : t('生成执行计划', 'Generate plan')}
            </button>
          )}
        </div>

        {plansLoading ? <div className="text-sm text-gray-400">{t('读取计划版本…', 'Loading plan versions…')}</div> : (
          <>
            {plans.length > 0 && selectedPlan && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-100 bg-gray-50 p-3">
                <span className="text-xs text-gray-500">{t('当前版本', 'Current version')}</span>
                <select className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-sm" value={selectedPlan.id}
                  onChange={e => { setPlanId(e.target.value); setEditing(false) }}>
                  {plans.map(p => <option key={p.id} value={p.id}>v{p.version} · {p.status}</option>)}
                </select>
                <span className={`rounded-full px-2 py-1 text-[11px] ${STATUS_STYLE[selectedPlan.status] || 'bg-gray-100 text-gray-600'}`}>{selectedPlan.status}</span>
                <span className="text-xs text-gray-400">{selectedPlan.steps.length} {t('个步骤', 'steps')}</span>
                {!editing && <button className="ml-auto rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs hover:bg-gray-50" onClick={beginEdit}>{t('编辑步骤', 'Edit steps')}</button>}
              </div>
            )}

            {!selectedPlan && !plansLoading && (
              <div className="rounded-lg border border-dashed border-gray-300 px-4 py-6 text-center">
                <div className="text-sm font-medium text-gray-700">{t('还没有执行计划', 'No execution plan yet')}</div>
                <p className="mt-1 text-xs text-gray-500">{t('可以从该用例已有的 Replay 操作记录生成一份草稿计划。', 'Generate a draft plan from this case’s existing Replay actions.')}</p>
                {!importMut.isPending && <button className="mt-3 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700" onClick={() => importMut.mutate()}>{t('生成执行计划', 'Generate plan')}</button>}
              </div>
            )}
          </>
        )}

        {selectedPlan && !editing && (
          <div className="space-y-3">
            {selectedPlan.warnings.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                <div className="mb-1 text-xs font-semibold text-amber-800">{t('需要关注', 'Needs attention')}</div>
                <ul className="list-disc space-y-1 pl-4 text-xs text-amber-800">
                  {selectedPlan.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}
            <div className="space-y-2">
              {selectedPlan.steps.map((step, i) => (
                <div key={step.step_id || i} className="flex items-start gap-3 rounded-lg border border-gray-100 bg-white px-3 py-3">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-gray-800">{ACTIONS.find(a => a.value === step.action)?.label || step.action}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${step.executor === 'system' ? 'bg-sky-50 text-sky-700' : step.executor === 'deterministic' ? 'bg-green-50 text-green-700' : step.executor === 'hybrid' ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'}`}>{step.executor === 'system' ? t('系统 API', 'System API') : step.executor === 'deterministic' ? t('UI 自动化', 'UI automation') : step.executor === 'hybrid' ? t('混合执行', 'Hybrid') : 'AI'}</span>
                      {typeof step.import_status === 'string' && step.import_status !== 'ready' && <span className="text-[10px] text-amber-600">{step.import_status === 'needs_locator' ? t('需要补充定位', 'Locator needed') : t('待检查', 'Review')}</span>}
                    </div>
                    <p className="mt-1 truncate text-xs text-gray-500">
                      {String(step.locator?.text || step.locator?.resource_id || step.args?.text || step.args?.package_name || step.args?.package || t('尚未设置目标控件或参数', 'Target or arguments not configured'))}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {editing && selectedPlan && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-gray-800">{t('步骤编辑器', 'Step editor')}</div>
                <p className="text-xs text-gray-500">{t('修改步骤属性、调整顺序或增删步骤。保存后生成新的草稿版本。', 'Edit step settings, reorder, add, or remove steps. Saving creates a new draft version.')}</p>
              </div>
              <button className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-100" onClick={addStep}>＋ {t('新增步骤', 'Add step')}</button>
            </div>

            {draftSteps.map((step, index) => {
              const coords = ((step.fallback as Record<string, unknown> | null)?.coordinates || {}) as Record<string, unknown>
              const waitMs = step.wait_condition?.type === 'delay' ? step.wait_condition.duration_ms : ''
              return (
                <div key={step.step_id || index} className="overflow-hidden rounded-lg border border-gray-200">
                  <div className="flex items-center justify-between gap-2 border-b bg-slate-50 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700">{index + 1}</span>
                      <span className="text-xs font-semibold text-gray-700">{ACTIONS.find(a => a.value === step.action)?.label || step.action}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button title={t('上移', 'Move up')} disabled={index === 0} onClick={() => moveStep(index, -1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↑</button>
                      <button title={t('下移', 'Move down')} disabled={index === draftSteps.length - 1} onClick={() => moveStep(index, 1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↓</button>
                      <button title={t('删除步骤', 'Delete step')} onClick={() => removeStep(index)} className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50">×</button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-3 p-3 md:grid-cols-2">
                    <div>
                      <label className={labelClass}>{t('操作类型', 'Action')}</label>
                      <select className={fieldClass} value={step.action} onChange={e => { const action = e.target.value; patchStep(index, { action, ...( ['start_app', 'global_action', 'press_key'].includes(action) ? { executor: 'system' } : step.executor === 'system' ? { executor: 'deterministic' } : {}) }) }}>
                        {ACTIONS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className={labelClass}>{t('执行方式', 'Execution strategy')}</label>
                      <select className={fieldClass} value={step.executor} onChange={e => patchStep(index, { executor: e.target.value as ExecutionPlanStep['executor'] })}>
                        <option value="system" disabled={!["start_app", "global_action", "press_key"].includes(step.action)}>{t('系统 API / Package 执行', 'System API / Package')}</option>
                        <option value="deterministic">{t('UI 确定性自动执行（优先）', 'Deterministic UI automation (preferred)')}</option>
                        <option value="hybrid">{t('混合执行（必要时调用 AI）', 'Hybrid (AI fallback)')}</option>
                        <option value="ai">{t('AI 执行', 'AI execution')}</option>
                      </select>
                    </div>

                    {(step.action === 'tap' || step.action === 'tap_element') && (
                      <>
                        <div>
                          <label className={labelClass}>{t('控件文本', 'Visible text')}</label>
                          <input className={fieldClass} value={String(step.locator?.text || '')} onChange={e => patchLocator(index, 'text', e.target.value)} placeholder={t('例如：搜索、确定、登录', 'e.g. Search, Confirm, Login')} />
                        </div>
                        <div>
                          <label className={labelClass}>Resource ID</label>
                          <input className={fieldClass} value={String(step.locator?.resource_id || '')} onChange={e => patchLocator(index, 'resource_id', e.target.value)} placeholder="com.example:id/search" />
                        </div>
                        <div>
                          <label className={labelClass}>{t('坐标兜底 X', 'Fallback X')}</label>
                          <input type="number" className={fieldClass} value={asNumber(coords.x ?? step.args?.x)} onChange={e => patchCoordinate(index, 'x', e.target.value)} placeholder="X" />
                        </div>
                        <div>
                          <label className={labelClass}>{t('坐标兜底 Y', 'Fallback Y')}</label>
                          <input type="number" className={fieldClass} value={asNumber(coords.y ?? step.args?.y)} onChange={e => patchCoordinate(index, 'y', e.target.value)} placeholder="Y" />
                        </div>
                      </>
                    )}

                    {step.action === 'input_text' && (
                      <div className="md:col-span-2">
                        <label className={labelClass}>{t('输入内容', 'Text to enter')}</label>
                        <input className={fieldClass} value={String(step.args?.text || '')} onChange={e => patchArgs(index, 'text', e.target.value)} placeholder={t('输入要填写的文本', 'Enter text to type')} />
                      </div>
                    )}
                    {step.action === 'start_app' && (
                      <div className="md:col-span-2">
                        <label className={labelClass}>{t('应用包名', 'App package')}</label>
                        <input className={fieldClass} value={String(step.args?.package_name || step.args?.package || '')} onChange={e => patchStep(index, { args: { ...(step.args || {}), package_name: e.target.value, package: e.target.value } })} placeholder="com.example.app" />
                      </div>
                    )}
                    {(step.action === 'swipe' || step.action === 'scroll') && (
                      <>
                        <div>
                          <label className={labelClass}>{t('滑动方向', 'Direction')}</label>
                          <select className={fieldClass} value={String(step.args?.direction || 'up')} onChange={e => patchArgs(index, 'direction', e.target.value)}>
                            <option value="up">{t('向上', 'Up')}</option><option value="down">{t('向下', 'Down')}</option><option value="left">{t('向左', 'Left')}</option><option value="right">{t('向右', 'Right')}</option>
                          </select>
                        </div>
                        <div>
                          <label className={labelClass}>{t('持续时间（毫秒）', 'Duration (ms)')}</label>
                          <input type="number" min="0" className={fieldClass} value={asNumber(step.args?.duration_ms)} onChange={e => patchArgs(index, 'duration_ms', Number(e.target.value) || 0)} />
                        </div>
                      </>
                    )}
                    {step.action === 'press_key' && (
                      <div>
                        <label className={labelClass}>{t('按键名称', 'Key name')}</label>
                        <select className={fieldClass} value={String(step.args?.key || 'ENTER')} onChange={e => patchArgs(index, 'key', e.target.value)}>
                          {['ENTER', 'BACK', 'HOME', 'TAB', 'BACKSPACE'].map(key => <option key={key} value={key}>{key}</option>)}
                        </select>
                      </div>
                    )}
                    {step.action === 'global_action' && (
                      <div>
                        <label className={labelClass}>{t('系统操作', 'System action')}</label>
                        <select className={fieldClass} value={String(step.args?.action || 'back')} onChange={e => patchArgs(index, 'action', e.target.value)}>
                          {['back', 'home', 'recent', '通知栏', '快捷设置'].map(key => <option key={key} value={key}>{key}</option>)}
                        </select>
                      </div>
                    )}
                    <div>
                      <label className={labelClass}>{t('操作后等待（毫秒）', 'Wait after action (ms)')}</label>
                      <input type="number" min="0" className={fieldClass} value={asNumber(waitMs)} onChange={e => patchWait(index, e.target.value)} placeholder="不额外等待" />
                    </div>
                    <div>
                      <label className={labelClass}>{t('失败时处理', 'On failure')}</label>
                      <select className={fieldClass} value={step.on_failure || 'fail'} onChange={e => patchStep(index, { on_failure: e.target.value })}>
                        <option value="fail">{t('终止并报告失败', 'Stop and report failure')}</option>
                        <option value="ai_recovery">{t('调用 AI 尝试恢复', 'Ask AI to recover')}</option>
                        <option value="reexplore">{t('重新探索当前页面', 'Re-explore current page')}</option>
                      </select>
                    </div>
                    <div className="md:col-span-2">
                      <label className={labelClass}>{t('结果验证文本（可选）', 'Expected visible text (optional)')}</label>
                      <input className={fieldClass} value={String(step.verification?.text || '')} onChange={e => patchStep(index, { verification: e.target.value ? { ...(step.verification || {}), type: 'text_present', text: e.target.value } : null })} placeholder={t('例如：搜索结果、登录成功', 'e.g. Search results, Login successful')} />
                    </div>
                  </div>
                </div>
              )
            })}

            {editError && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{editError}</div>}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <span className="text-xs text-gray-500">{draftSteps.length} {t('个步骤 · 保存会创建新版本', 'steps · saving creates a new version')}</span>
              <div className="flex gap-2">
                <button className="rounded-md border border-gray-200 px-3 py-2 text-xs hover:bg-gray-50" onClick={() => { setEditing(false); setEditError('') }}>{t('取消', 'Cancel')}</button>
                <button className="rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50" disabled={saveMut.isPending} onClick={saveVersion}>{saveMut.isPending ? t('保存中…', 'Saving…') : t('保存为新版本', 'Save new version')}</button>
              </div>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-5 text-gray-500">
          <strong className="text-gray-700">{t('说明：', 'Note: ')}</strong>{t('此处可以可视化维护执行步骤。当前版本仍是计划管理阶段，尚未接入计划执行引擎；保存的草稿不能直接运行，原有 AI 步骤树和 Replay 流程不受影响。', 'Execution steps can be maintained visually here. The plan runner is not connected yet, so drafts cannot run directly. Existing AI step-tree and Replay flows are unchanged.')}
        </div>
      </div>
    </section>
  )
}
