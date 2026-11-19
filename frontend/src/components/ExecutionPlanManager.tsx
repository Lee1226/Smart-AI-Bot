import { useState } from 'react'
import { api, type Device } from '../lib/api'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  updateExecutionPlan, ExecutionPlanStep, ExecutionPlanIntent,
  fetchAppCatalog, fetchExecutionPlans, fetchSuiteCases, importLegacyExecutionPlan, runExecutionPlan,
} from '../lib/api'
import { useT } from '../lib/i18n'
import LifecycleActionEditor, { type LifecycleAction } from './LifecycleActionEditor'

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

function hasValidTapCoordinates(step: ExecutionPlanStep): boolean {
  const fallbackCoordinates = (step.fallback?.coordinates || {}) as Record<string, unknown>
  const x = step.args?.x ?? fallbackCoordinates.x
  const y = step.args?.y ?? fallbackCoordinates.y
  return [x, y].every(value => value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value)))
}

function hasValidSwipeCoordinates(step: ExecutionPlanStep): boolean {
  const fallbackCoordinates = (step.fallback?.coordinates || {}) as Record<string, unknown>
  return ['x1', 'y1', 'x2', 'y2'].every(key => {
    const value = step.args?.[key] ?? fallbackCoordinates[key]
    return value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value))
  })
}

export default function ExecutionPlanManager({ suiteId, platform, device }: { suiteId: string; platform: string; device: Device | null }) {
  const t = useT()
  const qc = useQueryClient()
  const [caseId, setCaseId] = useState('')
  const [planId, setPlanId] = useState('')
  const [editing, setEditing] = useState(false)
  const [draftSteps, setDraftSteps] = useState<ExecutionPlanStep[]>([])
  const [draftPreActions, setDraftPreActions] = useState<LifecycleAction[]>([])
  const [draftPostActions, setDraftPostActions] = useState<LifecycleAction[]>([])
  const [editError, setEditError] = useState('')
  const [targetStepIndex, setTargetStepIndex] = useState(0)
  const [executingStep, setExecutingStep] = useState<number | null>(null)
  const [executionLogs, setExecutionLogs] = useState<Array<{ id: string; time: string; step: number; status: 'success' | 'error'; message: string }>>([])

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
  const { data: testApps = [], isLoading: appsLoading, isError: appsError } = useQuery({
    queryKey: ['app-catalog', device?.id || 'account'],
    // The catalog is account-scoped; it can be loaded even before a device is selected.
    // When a device is selected, the same endpoint also returns its installation state.
    queryFn: () => fetchAppCatalog(device?.platform === 'android' && device.connection_type === 'adb_usb' ? device.id : undefined),
    enabled: !device || (device.platform === 'android' && device.connection_type === 'adb_usb'),
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
  const selectableTestApps = testApps.filter(app => app.is_test_app)
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
      updateExecutionPlan(selectedPlan!.id, body),
    onSuccess: async plan => {
      setPlanId(plan.id)
      setEditing(false)
      setEditError('')
      await invalidate()
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(t(`保存执行计划失败：${msg}`, `Failed to save execution plan: ${msg}`))
    },
  })

  const runMut = useMutation({
    mutationFn: () => {
      if (!selectedPlan || !device) throw new Error('请先选择已连接的测试设备')
      return runExecutionPlan(selectedPlan.id, { device_id: device.id, max_steps: 8 })
    },
    onSuccess: run => {
      alert(t(`执行任务已创建：${run.id}，可在测试运行记录中查看进度和结果。`, `Run created: ${run.id}. Track progress and results in Run History.`))
      qc.invalidateQueries({ queryKey: ['runs'] })
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(t(`执行计划启动失败：${msg}`, `Failed to start execution plan: ${msg}`))
    },
  })

  const beginEdit = () => {
    if (!selectedPlan) return
    setDraftPreActions(Array.isArray(selectedPlan.metadata?.pre_actions) ? selectedPlan.metadata.pre_actions as LifecycleAction[] : [])
    setDraftPostActions(Array.isArray(selectedPlan.metadata?.post_actions) ? selectedPlan.metadata.post_actions as LifecycleAction[] : [])
    setDraftSteps(selectedPlan.steps.map(step => ({
      ...step,
      args: { ...(step.args || {}) },
      locator: { ...(step.locator || {}) },
      intent: step.intent ? { ...step.intent } : { description: '', expected_result: '', source: 'legacy_import', status: 'needs_confirmation' },
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
  const patchIntent = (index: number, patch: Partial<ExecutionPlanIntent>) => {
    const step = draftSteps[index]
    const current: ExecutionPlanIntent = step.intent || { description: '', expected_result: '', source: 'legacy_import', status: 'needs_confirmation' }
    const contentEdited = patch.description !== undefined || patch.expected_result !== undefined
    patchStep(index, { intent: { ...current, ...patch, ...(contentEdited ? { source: 'user_edited', status: 'needs_confirmation' } : {}) } })
  }
  const patchCoordinate = (index: number, key: string, value: string) => {
    const step = draftSteps[index]
    const fallback = (step.fallback || { type: 'coordinate', coordinates: {} }) as Record<string, unknown>
    const coordinates = { ...((fallback.coordinates as Record<string, unknown>) || {}), [key]: value === '' ? '' : Number(value) }
    patchStep(index, { fallback: { ...fallback, type: 'coordinate', coordinates } })
  }
  const patchSwipeCoordinate = (index: number, key: 'x1' | 'y1' | 'x2' | 'y2', value: string) => {
    const step = draftSteps[index]
    const nextValue = value === '' ? '' : Number(value)
    const fallback = (step.fallback || { type: 'coordinate', coordinates: {} }) as Record<string, unknown>
    const coordinates = { ...((fallback.coordinates as Record<string, unknown>) || {}), [key]: nextValue }
    // The replay adapter gives args precedence over fallback.coordinates.
    // Keep both representations in sync so edited coordinates always take effect.
    patchStep(index, {
      args: { ...(step.args || {}), [key]: nextValue },
      fallback: { ...fallback, type: 'coordinate', coordinates },
    })
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
      intent: { description: '', expected_result: '', source: 'manual', status: 'needs_confirmation' },
      import_status: 'manual',
    }])
  }
  const removeStep = (index: number) => {
    setDraftSteps(current => current.filter((_, i) => i !== index).map((step, i) => ({ ...step, sequence: i + 1 })))
  }
  const executeDeviceStep = async (step: ExecutionPlanStep, index: number) => {
    if (!device || device.platform !== 'android' || device.connection_type !== 'adb_usb') {
      setExecutionLogs(current => [{ id: String(Date.now()), time: new Date().toLocaleTimeString(), step: index + 1, status: 'error', message: '仅支持已连接的 Android 本机 ADB 设备' }, ...current])
      return
    }
    if (executingStep !== null) return
    setExecutingStep(index)
    const addLog = (status: 'success' | 'error', message: string) =>
      setExecutionLogs(current => [{ id: String(Date.now()) + Math.random(), time: new Date().toLocaleTimeString(), step: index + 1, status, message }, ...current].slice(0, 30))
    try {
      const args = { ...(step.args || {}) } as Record<string, any>
      const locator = (step.locator || {}) as Record<string, any>
      let action = step.action
      if (action === 'wait') {
        const ms = Number(step.wait_condition?.duration_ms || args.duration_ms || 0)
        await new Promise(resolve => window.setTimeout(resolve, Math.max(0, Math.min(ms, 60000))))
        addLog('success', `等待 ${ms} ms 完成`)
        return
      }
      if ((action === 'tap' || action === 'tap_element') && step.force_coordinate) {
        const coordinates = (step.fallback?.coordinates || {}) as Record<string, unknown>
        const x = coordinates.x ?? args.x
        const y = coordinates.y ?? args.y
        if (x === undefined || y === undefined || x === null || y === null || x === '' || y === '') {
          throw new Error('已启用强制坐标点击，但该步骤没有有效的 X/Y 坐标。')
        }
        const coordinateSpace = String(step.fallback?.coordinate_space || args.coordinate_space || 'device_px')
        const { data } = await api.post('/recorder/gesture', {
          device_id: device.id, action: 'tap',
          args: { x: Number(x), y: Number(y), coordinate_space: coordinateSpace },
        })
        addLog('success', data.description || `已直接点击坐标 (${x}, ${y})`)
        return
      }
      if (action === 'tap' || action === 'tap_element' || action === 'input_text') {
        const { data: tree } = await api.get<{ elements: Array<Record<string, any>> }>(`/recorder/elements?device_id=${encodeURIComponent(device.id)}`)
        const keys = [
          ['resourceId', locator.resource_id || locator.resourceId],
          ['contentDescription', locator.content_description || locator.contentDescription],
          ['text', locator.text],
        ].filter((entry): entry is [string, string] => typeof entry[1] === 'string' && !!entry[1].trim())
        if (!keys.length) throw new Error('该步骤没有语义定位条件。请先从实时 UI Tree 选择控件并绑定，暂不使用不可靠的旧 index/坐标自动点击。')
        let matches = tree.elements
        for (const [key, value] of keys) {
          const found = matches.filter(element => String(element[key] || '') === value)
          if (found.length) { matches = found; break }
        }
        if (matches.length !== 1) throw new Error(matches.length ? `定位到 ${matches.length} 个同名控件，存在歧义；请补充 Resource ID。` : '当前页面未找到目标控件，请刷新 UI Tree 并重新绑定。')
        const target = matches[0]
        if (target.visible === false || target.enabled === false) throw new Error('目标控件不可见或已禁用，已阻止操作。')
        if (action === 'tap' || action === 'tap_element' || action === 'input_text') {
          await api.post('/recorder/action', { device_id: device.id, action: 'tap_element', args: { index: target.index } })
        }
        if (action === 'input_text') {
          if (typeof args.text !== 'string' || !args.text.length) throw new Error('输入步骤缺少文本内容。')
          const { data } = await api.post('/recorder/action', { device_id: device.id, action: 'input_text', args: { text: args.text, clear: !!args.clear } })
          addLog('success', data.description || '控件已聚焦并输入文本')
          return
        }
        if (action === 'tap' || action === 'tap_element') {
          addLog('success', `已点击控件：${target.text || target.contentDescription || target.resourceId || target.className}`)
          return
        }
      } else if (action === 'start_app') {
        const pkg = String(args.package_name || args.package || '').trim()
        if (!pkg) throw new Error('启动应用步骤缺少 Package 名称。')
        const { data } = await api.post('/recorder/action', { device_id: device.id, action: 'start_app', args: { package: pkg, activity: args.activity || '' } })
        if (String(data.result || '').startsWith('ERROR:')) throw new Error(String(data.result))
        addLog('success', data.description || data.result || `已发送启动请求：${pkg}`)
        return
      } else if (action === 'global_action' || action === 'press_key') {
        const { data } = await api.post('/recorder/action', { device_id: device.id, action, args })
        addLog('success', data.description || data.result || '设备操作已返回')
        return
      } else if (action === 'scroll' || action === 'swipe') {
        const { data } = await api.post('/recorder/action', { device_id: device.id, action, args })
        addLog('success', data.description || data.result || '设备操作已返回')
        return
      }
      throw new Error(`暂不支持单步调试动作：${action}`)
    } catch (error: any) {
      addLog('error', error?.response?.data?.detail || error?.message || String(error))
    } finally {
      setExecutingStep(null)
    }
  }

  const saveVersion = () => {
    if (draftSteps.length === 0) {
      setEditError(t('至少保留一个执行步骤。', 'Keep at least one execution step.'))
      return
    }
    const invalid = draftSteps.findIndex(step =>
      !step.action || !step.executor ||
      (step.force_coordinate && (!['tap', 'tap_element'].includes(step.action) || !hasValidTapCoordinates(step))) ||
      (step.action === 'swipe' && !hasValidSwipeCoordinates(step))
    )
    if (invalid >= 0) {
      const invalidStep = draftSteps[invalid]
      setEditError(invalidStep.action === 'swipe'
        ? `第 ${invalid + 1} 步是坐标滑动，必须填写有效的起点 X/Y 和终点 X/Y。`
        : invalidStep.force_coordinate
          ? `第 ${invalid + 1} 步已启用强制坐标点击，但没有有效的 X/Y 坐标，或该操作不是点击。`
          : `第 ${invalid + 1} 步缺少操作类型或执行策略。`)
      return
    }
    setEditError('')
    saveMut.mutate({
      steps: draftSteps.map((step, i) => ({ ...step, sequence: i + 1 })),
      metadata: { ...(selectedPlan?.metadata || {}), pre_actions: draftPreActions, post_actions: draftPostActions },
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

        {plansLoading ? <div className="text-sm text-gray-400">{t('读取执行计划…', 'Loading execution plan…')}</div> : (
          <>
            {plans.length > 0 && selectedPlan && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-100 bg-gray-50 p-3">
                <span className="text-xs font-medium text-gray-700">{t('当前执行计划', 'Current execution plan')}</span>
                <span className={`rounded-full px-2 py-1 text-[11px] ${STATUS_STYLE[selectedPlan.status] || 'bg-gray-100 text-gray-600'}`}>{selectedPlan.status}</span>
                <span className="text-xs text-gray-400">{selectedPlan.steps.length} {t('个步骤', 'steps')}</span>
                {!editing && <div className="ml-auto flex items-center gap-2">
                  <button className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!device || runMut.isPending || selectedPlan.steps.some(step => ['ai', 'hybrid'].includes(step.executor) && (step.intent?.status !== 'confirmed' || !step.intent?.description?.trim() || !step.intent?.expected_result?.trim()))}
                    onClick={() => runMut.mutate()}>
                    {runMut.isPending ? t('启动中…', 'Starting…') : t('运行执行计划', 'Run plan')}
                  </button>
                  <button className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs hover:bg-gray-50" onClick={beginEdit}>{t('编辑步骤', 'Edit steps')}</button>
                </div>}
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

        <section className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-gray-700">单步执行日志</h3>
            <button type="button" onClick={() => setExecutionLogs([])} className="text-[11px] text-gray-400 hover:text-gray-700">清空</button>
          </div>
          {executionLogs.length === 0 ? <p className="py-3 text-center text-[11px] text-gray-400">尚无执行记录。点击步骤右侧“单步执行”后，这里会显示设备实际返回结果。</p> : (
            <div className="max-h-40 space-y-1.5 overflow-auto">
              {executionLogs.map(log => <div key={log.id} className="flex items-start gap-2 text-[11px]">
                <span className="shrink-0 text-gray-400">{log.time}</span>
                <span className="shrink-0 text-gray-500">第 {log.step} 步</span>
                <span className={log.status === 'success' ? 'text-green-700' : 'text-red-700'}>{log.status === 'success' ? '成功' : '失败'}</span>
                <span className="min-w-0 break-all text-gray-600">{log.message}</span>
              </div>)}
            </div>
          )}
        </section>

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
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {(['pre_actions', 'post_actions'] as const).map((phase) => {
                const actions = Array.isArray(selectedPlan.metadata?.[phase]) ? selectedPlan.metadata[phase] as LifecycleAction[] : []
                return <div key={phase} className="rounded-lg border border-gray-200 bg-white p-3">
                  <div className="text-xs font-semibold text-gray-700">{phase === 'pre_actions' ? '执行前处理' : '执行后处理'}</div>
                  {actions.filter(action => action.enabled !== false).length ? <div className="mt-2 space-y-1">{actions.filter(action => action.enabled !== false).map((action, index) => <div key={action.id || index} className="text-xs text-gray-600">{index + 1}. {({ stop_app: '强制停止应用', start_app: '启动应用', global_action: '系统导航', press_key: '按键操作', tap: '点击坐标', input_text: '输入文本', swipe: '滑动屏幕', scroll: '滚动页面', wait: '等待' } as Record<string, string>)[action.action] || action.action} · {String(action.args?.package_name || action.args?.package || action.args?.action || action.args?.key || action.args?.duration_ms || '')}</div>)}</div> : <p className="mt-2 text-xs text-gray-400">未配置，不执行任何处理</p>}
                </div>
              })}
            </div>
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
                    <p className="mt-1 text-xs text-gray-600">{String(step.intent?.description || t('尚未填写操作意图', 'No operation intent defined'))}</p>
                    <p className="mt-1 truncate text-[11px] text-gray-400">{String(step.locator?.text || step.locator?.resource_id || step.args?.text || step.args?.package_name || step.args?.package || t('尚未设置目标控件或参数', 'Target or arguments not configured'))}</p>
                    {step.intent?.status !== 'confirmed' && <span className="mt-1 inline-block rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">{t('操作意图待确认', 'Intent needs confirmation')}</span>}
                    <button type="button" disabled={!device || executingStep !== null || step.executor === 'ai' || step.executor === 'hybrid'} onClick={() => void executeDeviceStep(step, i)} title={step.executor === 'ai' || step.executor === 'hybrid' ? t('AI / 混合策略请通过“运行执行计划”执行，单步调试暂不支持', 'Run the full plan for AI / hybrid steps; single-step debug does not support these strategies yet') : undefined} className="mt-2 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-[11px] font-medium text-blue-700 disabled:opacity-40">{step.executor === 'ai' || step.executor === 'hybrid' ? t('请运行整份计划', 'Run full plan') : executingStep === i ? '执行中…' : '单步执行'}</button>
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
                <p className="text-xs text-gray-500">{t('修改步骤属性、调整顺序或增删步骤，保存后直接更新当前执行计划。', 'Edit step settings, reorder, add, or remove steps. Save updates this execution plan directly.')}</p>
              </div>
              <button className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-100" onClick={addStep}>＋ {t('新增步骤', 'Add step')}</button>
            </div>

            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <LifecycleActionEditor
                title="执行前处理（可选）"
                description="在正常测试步骤开始前执行；不添加操作则完全跳过。"
                actions={draftPreActions}
                onChange={setDraftPreActions}
              />
              <LifecycleActionEditor
                title="执行后处理（可选）"
                description="在正常测试步骤结束后执行；可配置清理应用或其他设备操作。"
                actions={draftPostActions}
                onChange={setDraftPostActions}
              />
            </div>
            {draftSteps.map((step, index) => {
              const fallback = (step.fallback || {}) as Record<string, unknown>
              const coords = (fallback.coordinates || {}) as Record<string, unknown>
              const coordinateX = step.args?.x ?? coords.x
              const coordinateY = step.args?.y ?? coords.y
              const swipeCoordinates = {
                x1: step.args?.x1 ?? coords.x1,
                y1: step.args?.y1 ?? coords.y1,
                x2: step.args?.x2 ?? coords.x2,
                y2: step.args?.y2 ?? coords.y2,
              }
              const swipeDuration = step.args?.duration_ms ?? coords.duration_ms ?? ''
              const swipeCoordinateSpace = fallback.coordinate_space ?? step.args?.coordinate_space
              const waitMs = step.wait_condition?.type === 'delay' ? step.wait_condition.duration_ms : ''
              return (
                <div key={step.step_id || index} className="overflow-hidden rounded-lg border border-gray-200">
                  <div className="flex items-center justify-between gap-2 border-b bg-slate-50 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <button type="button" onClick={() => setTargetStepIndex(index)} title="将真机控件绑定到此步骤" className={targetStepIndex === index ? "flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white" : "flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700"}>{index + 1}</button>
                      <span className="text-xs font-semibold text-gray-700">{ACTIONS.find(a => a.value === step.action)?.label || step.action}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button type="button" title={step.executor === 'ai' || step.executor === 'hybrid' ? 'AI / 混合计划执行器尚未接入' : '在真机上执行当前步骤'} disabled={!device || executingStep !== null || step.executor === 'ai' || step.executor === 'hybrid'} onClick={() => void executeDeviceStep(step, index)} className="rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-700 disabled:opacity-40">{step.executor === 'ai' || step.executor === 'hybrid' ? '请运行整份计划' : executingStep === index ? '执行中…' : '单步执行'}</button>
                      <button title={t('上移', 'Move up')} disabled={index === 0} onClick={() => moveStep(index, -1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↑</button>
                      <button title={t('下移', 'Move down')} disabled={index === draftSteps.length - 1} onClick={() => moveStep(index, 1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↓</button>
                      <button title={t('删除步骤', 'Delete step')} onClick={() => removeStep(index)} className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50">×</button>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-3 p-3 md:grid-cols-2">
                    <div className="md:col-span-2 rounded-lg border border-blue-100 bg-blue-50/50 p-3 space-y-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div><div className="text-xs font-semibold text-gray-800">{t('操作意图', 'Operation intent')}</div><p className="mt-0.5 text-[11px] text-gray-500">{t('描述这一步要完成什么。录制坐标和控件属性不能代替操作目的。', 'Describe what this step should accomplish. Coordinates and element attributes are not a substitute for intent.')}</p></div>
                        <span className={step.intent?.status === 'confirmed' ? 'rounded-full bg-green-100 px-2 py-1 text-[10px] text-green-700' : 'rounded-full bg-amber-100 px-2 py-1 text-[10px] text-amber-700'}>{step.intent?.status === 'confirmed' ? t('已确认', 'Confirmed') : t('待确认', 'Needs confirmation')}</span>
                      </div>
                      <div><label className={labelClass}>{t('这一步要做什么', 'What should this step do?')}</label><textarea rows={2} className={fieldClass} value={String(step.intent?.description || '')} onChange={e => patchIntent(index, { description: e.target.value })} placeholder={t('例如：点击搜索入口，进入应用搜索页面', 'e.g. Open the app search page by selecting its search entry')} /></div>
                      <div><label className={labelClass}>{t('预期结果', 'Expected result')}</label><textarea rows={2} className={fieldClass} value={String(step.intent?.expected_result || '')} onChange={e => patchIntent(index, { expected_result: e.target.value })} placeholder={t('例如：搜索页面显示，搜索输入框可见', 'e.g. Search page opens and the search field is visible')} /></div>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[10px] text-gray-500">{t('来源', 'Source')}: {String(step.intent?.source || 'legacy_import')}</span>
                        <label className="flex items-center gap-2 text-xs text-gray-600"><input type="checkbox" checked={step.intent?.status === 'confirmed'} disabled={!String(step.intent?.description || '').trim() || !String(step.intent?.expected_result || '').trim()} onChange={e => patchIntent(index, { status: e.target.checked ? 'confirmed' : 'needs_confirmation' })} />{t('我已确认操作意图和预期结果', 'I confirm this intent and expected result')}</label>
                      </div>
                    </div>
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
                        <div className="md:col-span-2 rounded-md border border-gray-200 bg-slate-50 p-3">
                          <label className="flex items-center gap-2 text-xs font-medium text-gray-700">
                            <input type="checkbox" checked={!!step.force_coordinate} onChange={e => patchStep(index, { force_coordinate: e.target.checked })} />
                            {t('强制使用坐标点击', 'Force coordinate tap')}
                          </label>
                          <p className="mt-1 text-[11px] text-gray-500">{t('开启后跳过 UI 元素查找，直接使用下方录制坐标；关闭时保持 UI 定位优先、失败后坐标兜底。', 'When enabled, skip UI lookup and tap the recorded coordinates directly. When disabled, keep UI-first lookup with coordinate fallback.')}</p>
                        </div>
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
                          <input type="number" className={fieldClass} value={asNumber(coordinateX)} onChange={e => patchCoordinate(index, 'x', e.target.value)} placeholder="X" />
                        </div>
                        <div>
                          <label className={labelClass}>{t('坐标兜底 Y', 'Fallback Y')}</label>
                          <input type="number" className={fieldClass} value={asNumber(coordinateY)} onChange={e => patchCoordinate(index, 'y', e.target.value)} placeholder="Y" />
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
                      <div className="md:col-span-2 space-y-2">
                        <label className={labelClass}>选择已标记应用</label>
                        <select className={fieldClass} value={String(step.args?.package_name || step.args?.package || '')} onChange={e => { const pkg = e.target.value; patchStep(index, { args: { ...(step.args || {}), package_name: pkg, package: pkg } }) }}>
                          <option value="">选择已加入测试应用的应用…</option>
                          {selectableTestApps.map(app => <option key={app.package_name} value={app.package_name} disabled={!app.installed}>{app.app_name || app.package_name} · {app.package_name}{app.installed ? '' : '（当前设备未安装）'}</option>)}
                        </select>
                        {appsLoading ? <p className="text-[10px] text-gray-500">正在读取账号应用库…</p> : appsError ? <p className="text-[10px] text-red-600">应用库读取失败，请检查登录状态或刷新页面。</p> : selectableTestApps.length === 0 ? <p className="text-[10px] text-amber-700">账号应用库中还没有已标记的测试应用，请先到「设备应用库」加入测试应用。</p> : !device ? <p className="text-[10px] text-amber-700">已显示账号内标记的应用；请先在右侧选择 Android ADB 设备，才能选择已安装的应用。</p> : null}
                        <div className="text-[10px] text-gray-400">执行时使用包名直接启动；未安装在当前设备的应用不可选择。</div>
                      </div>
                    )}
                    {step.action === 'scroll' && (
                      <>
                        <div>
                          <label className={labelClass}>{t('滚动方向', 'Scroll direction')}</label>
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
                    {step.action === 'swipe' && (
                      <>
                        <div className="md:col-span-2 rounded-md border border-blue-100 bg-blue-50/50 p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-xs font-semibold text-gray-700">{t('滑动坐标', 'Swipe coordinates')}</span>
                            <span className="text-[10px] text-gray-500">{t('坐标空间', 'Coordinate space')}: {String(swipeCoordinateSpace || t('未标注', 'Not specified'))}</span>
                          </div>
                          <p className="mt-1 text-[11px] text-gray-500">{t('起点和终点坐标来自录制数据。编辑时会同步保存到执行参数和坐标兜底数据，确保运行时使用修改后的值。', 'Coordinates come from the recording. Edits are saved to both execution arguments and fallback data so the updated values are used at runtime.')}</p>
                          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                            {([
                              ['x1', t('起点 X', 'Start X')],
                              ['y1', t('起点 Y', 'Start Y')],
                              ['x2', t('终点 X', 'End X')],
                              ['y2', t('终点 Y', 'End Y')],
                            ] as const).map(([key, label]) => (
                              <div key={key}>
                                <label className={labelClass}>{label}</label>
                                <input type="number" className={fieldClass} value={asNumber(swipeCoordinates[key])} onChange={e => patchSwipeCoordinate(index, key, e.target.value)} placeholder={key.toUpperCase()} />
                              </div>
                            ))}
                          </div>
                        </div>
                        <div>
                          <label className={labelClass}>{t('持续时间（毫秒）', 'Duration (ms)')}</label>
                          <input type="number" min="0" className={fieldClass} value={asNumber(swipeDuration)} onChange={e => patchArgs(index, 'duration_ms', Number(e.target.value) || 0)} />
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
              <span className="text-xs text-gray-500">{draftSteps.length} {t('个步骤', 'steps')}</span>
              <div className="flex gap-2">
                <button className="rounded-md border border-gray-200 px-3 py-2 text-xs hover:bg-gray-50" onClick={() => { setEditing(false); setEditError('') }}>{t('取消', 'Cancel')}</button>
                <button className="rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50" disabled={saveMut.isPending} onClick={saveVersion}>{saveMut.isPending ? t('保存中…', 'Saving…') : t('保存', 'Save')}</button>
              </div>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-5 text-gray-500">
          <strong className="text-gray-700">{t('说明：', 'Note: ')}</strong>{t('操作意图会随执行计划保存；AI / 混合步骤必须先确认操作意图和预期结果。运行整份计划时，系统按步骤分发确定性、AI、混合或系统执行器；混合步骤优先执行确定性操作；执行失败、验证不通过或未配置可判定的验证条件时，才调用 AI 检查并恢复。', 'Step intent is saved with the execution plan. AI / hybrid steps require a confirmed intent and expected result. Full-plan runs dispatch deterministic, AI, hybrid, and system steps through the shared runner; hybrid steps try deterministic execution first, then invoke AI to inspect and recover only on execution failure, failed verification, or missing verification conditions.')}
        </div>
      </div>
    </section>
  )
}
