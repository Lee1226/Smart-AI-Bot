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

export default function ExecutionPlanManager({ suiteId, platform }: { suiteId: string; platform: string }) {
  const t = useT()
  const qc = useQueryClient()
  const [caseId, setCaseId] = useState('')
  const [planId, setPlanId] = useState('')
  const [editing, setEditing] = useState(false)
  const [stepsJson, setStepsJson] = useState('')
  const [jsonError, setJsonError] = useState('')

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
      alert(t(`导入失败：${msg}`, `Import failed: ${msg}`))
    },
  })
  const saveMut = useMutation({
    mutationFn: (body: { steps: ExecutionPlanStep[]; metadata: Record<string, unknown>; warnings: string[]; platform: string }) =>
      createExecutionPlanVersion(selectedPlan!.id, body),
    onSuccess: async plan => {
      setPlanId(plan.id)
      setEditing(false)
      setJsonError('')
      await invalidate()
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(t(`保存新版本失败：${msg}`, `Failed to save new version: ${msg}`))
    },
  })

  const planOptions = plans
  if (casesLoading) return <div className="mt-6 text-sm text-gray-400">{t('加载执行计划…', 'Loading execution plans…')}</div>
  if (cases.length === 0) return null

  const beginEdit = () => {
    if (!selectedPlan) return
    setStepsJson(JSON.stringify(selectedPlan.steps, null, 2))
    setJsonError('')
    setEditing(true)
  }
  const saveVersion = () => {
    try {
      const parsed = JSON.parse(stepsJson)
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error(t('步骤必须是非空数组', 'Steps must be a non-empty array'))
      setJsonError('')
      saveMut.mutate({
        steps: parsed,
        metadata: selectedPlan?.metadata || {},
        warnings: selectedPlan?.warnings || [],
        platform: selectedPlan?.platform || platform,
      })
    } catch (e) {
      setJsonError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="mt-6 rounded-lg border bg-white shadow-sm overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b bg-slate-50">
        <div>
          <h2 className="text-sm font-semibold">{t('执行计划', 'Execution plans')}</h2>
          <p className="text-xs text-gray-500 mt-0.5">{t('将录制步骤沉淀为可审查、可版本化的自动化执行方案', 'Turn recorded steps into reviewable, versioned automation plans')}</p>
        </div>
        <span className="text-[11px] rounded-full px-2 py-1 bg-amber-50 text-amber-700">{t('第一阶段：计划管理', 'Phase 1: plan management')}</span>
      </div>
      <div className="p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-gray-500">{t('录制用例', 'Recorded case')}</label>
          <select className="min-w-0 flex-1 border rounded px-2 py-1.5 text-sm"
            value={activeCaseId} onChange={e => { setCaseId(e.target.value); setPlanId(''); setEditing(false) }}>
            {cases.map(c => <option key={c.id} value={c.id}>{c.path || `#${c.order}`}</option>)}
          </select>
          <button className="px-3 py-1.5 text-xs rounded bg-primary text-white hover:bg-primary-deep disabled:opacity-50"
            disabled={!activeCaseId || importMut.isPending} onClick={() => importMut.mutate()}>
            {importMut.isPending ? t('导入中…', 'Importing…') : t('从旧版 Replay 导入', 'Import legacy Replay')}
          </button>
        </div>

        {plansLoading ? <div className="text-sm text-gray-400">{t('读取计划版本…', 'Loading plan versions…')}</div> : (
          <>
            {planOptions.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <label className="text-xs text-gray-500">{t('计划版本', 'Plan version')}</label>
                <select className="border rounded px-2 py-1.5 text-sm" value={selectedPlan?.id || ''}
                  onChange={e => { setPlanId(e.target.value); setEditing(false) }}>
                  {planOptions.map(p => <option key={p.id} value={p.id}>v{p.version} · {p.status}</option>)}
                </select>
                {selectedPlan && <span className={`text-[11px] rounded-full px-2 py-1 ${STATUS_STYLE[selectedPlan.status] || 'bg-gray-100 text-gray-600'}`}>{selectedPlan.status}</span>}
                {selectedPlan && <span className="text-xs text-gray-400">{selectedPlan.steps.length} {t('步', 'steps')} · {selectedPlan.source}</span>}
                <button className="ml-auto px-3 py-1.5 text-xs border rounded hover:bg-gray-50 disabled:opacity-50"
                  disabled={!selectedPlan || editing} onClick={beginEdit}>{t('编辑并创建新版本', 'Edit as new version')}</button>
              </div>
            )}
            {!selectedPlan && (
              <div className="rounded border border-dashed p-4 text-center text-sm text-gray-500">
                {t('该用例还没有执行计划。可从旧版 Replay 导入生成草稿。', 'No execution plan yet. Import legacy Replay to create a draft.')}
              </div>
            )}
          </>
        )}

        {selectedPlan && !editing && (
          <div className="space-y-3">
            {selectedPlan.warnings.length > 0 && (
              <div className="rounded border border-amber-200 bg-amber-50 p-3">
                <div className="text-xs font-semibold text-amber-800 mb-1">{t('导入提示', 'Import warnings')}</div>
                <ul className="list-disc pl-4 space-y-1 text-xs text-amber-800">
                  {selectedPlan.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}
            <div className="border rounded divide-y">
              {selectedPlan.steps.map((step, i) => (
                <div key={step.step_id || i} className="px-3 py-2.5 flex items-start gap-3">
                  <span className="text-xs font-mono text-gray-400 pt-0.5 w-6">{String(step.sequence ?? i + 1).padStart(2, '0')}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{step.action}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${step.executor === 'deterministic' ? 'bg-green-100 text-green-700' : step.executor === 'hybrid' ? 'bg-blue-100 text-blue-700' : 'bg-amber-100 text-amber-700'}`}>{step.executor}</span>
                      {typeof step.import_status === 'string' && <span className="text-[10px] text-gray-400">{step.import_status}</span>}
                    </div>
                    <div className="mt-1 text-[11px] text-gray-500 break-all">
                      {Object.keys(step.locator || {}).length > 0 ? `Locator: ${JSON.stringify(step.locator)}` : t('未配置语义定位', 'No semantic locator')}
                      {step.fallback ? ` · Fallback: ${JSON.stringify(step.fallback)}` : ''}
                    </div>
                    {Array.isArray(step.warnings) && step.warnings.length > 0 && <div className="mt-1 text-[11px] text-amber-700">{step.warnings.join('；')}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {editing && selectedPlan && (
          <div className="space-y-2">
            <div className="text-xs text-gray-500">{t('编辑完整步骤 JSON。保存会创建新草稿版本，不会覆盖当前版本。', 'Edit the full steps JSON. Saving creates a new draft version and never overwrites this version.')}</div>
            <textarea className="w-full min-h-[320px] border rounded p-3 font-mono text-xs leading-relaxed"
              value={stepsJson} onChange={e => setStepsJson(e.target.value)} spellCheck={false} />
            {jsonError && <div className="text-xs text-red-600">{jsonError}</div>}
            <div className="flex gap-2">
              <button className="px-3 py-1.5 rounded bg-primary text-white text-xs disabled:opacity-50"
                disabled={saveMut.isPending} onClick={saveVersion}>{saveMut.isPending ? t('保存中…', 'Saving…') : t('保存为新版本', 'Save new version')}</button>
              <button className="px-3 py-1.5 rounded border text-xs" onClick={() => { setEditing(false); setJsonError('') }}>{t('取消', 'Cancel')}</button>
            </div>
          </div>
        )}
        <div className="rounded bg-slate-50 border px-3 py-2 text-xs text-gray-500">
          <strong>{t('注意：', 'Note: ')}</strong>{t('当前仅支持执行计划导入、查看和版本编辑；计划执行引擎尚未接入，草稿不能直接运行。原有 AI 步骤树和 Replay 运行流程不受影响。', 'This stage supports plan import, inspection, and version editing only. The plan execution engine is not connected yet, so drafts cannot run. Existing AI step-tree and Replay flows are unchanged.')}
        </div>
      </div>
    </section>
  )
}
