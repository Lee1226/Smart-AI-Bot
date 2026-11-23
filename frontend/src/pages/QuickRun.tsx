import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { fetchDevices, fetchSettings, fetchProjects, quickRun } from '../lib/api'
import { useT } from '../lib/i18n'
import TestAccountSelect from '../components/TestAccountSelect'
import { PROVIDERS, DEFAULT_MODEL } from '../lib/providerConfig'

export default function QuickRun() {
  const navigate = useNavigate()
  const t = useT()

  const [goal, setGoal] = useState('')
  const [expected, setExpected] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o')
  const [maxSteps, setMaxSteps] = useState(20)
  const [projectId, setProjectId] = useState('')
  const [testAccountId, setTestAccountId] = useState('')
  const settingsInitialized = useRef(false)

  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: fetchProjects })

  const onlineDevices = devices.filter(d => d.status === 'online')

  // Apply saved defaults only once on first load
  useEffect(() => {
    if (settings && !settingsInitialized.current) {
      settingsInitialized.current = true
      if (settings.default_provider) setProvider(settings.default_provider)
      if (settings.default_model) setModel(settings.default_model)
    }
  }, [settings])

  function handleProviderChange(p: string) {
    setProvider(p)
    // Use saved default only if this is the saved default provider; otherwise use per-provider default
    if (settings && p === settings.default_provider && settings.default_model) {
      setModel(settings.default_model)
    } else {
      setModel(DEFAULT_MODEL[p] || '')
    }
  }

  const runMut = useMutation({
    mutationFn: () => quickRun({
      goal,
      expected: expected || '任务完成',
      device_id: deviceId,
      provider,
      model,
      max_steps: maxSteps,
      project_id: projectId,
      test_account_id: testAccountId,
    }),
    onSuccess: run => navigate(`/runs/${run.id}`),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`${t('启动失败', 'Failed to start')}: ${msg}`)
    },
  })

  const canSubmit = goal.trim().length > 0 && deviceId.length > 0 && !runMut.isPending

  return (
    <div className="mx-auto max-w-5xl space-y-7">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-5 py-6 text-white shadow-[0_18px_45px_rgba(37,99,235,0.18)] sm:px-6">
        <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" />
        <div className="pointer-events-none absolute -bottom-28 right-40 h-56 w-56 rounded-full bg-white/10 blur-2xl" />
        <div className="relative max-w-2xl">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold tracking-wide text-blue-50">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-200" />
            {t('AI 终端 · 任务执行', 'AI Device · Task execution')}
          </div>
          <h1 className="text-xl font-bold tracking-tight sm:text-xl">{t('快速任务', 'Quick Task')}</h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-blue-50/90">{t('描述目标，选择设备与执行模型，让 Agent 在真实终端上完成操作并验证结果。', 'Describe the goal, choose a device and model, and let the agent execute and verify it on a real device.')}</p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="min-w-0 rounded-2xl border border-slate-200/80 bg-white p-6 shadow-[0_8px_28px_rgba(31,75,135,0.05)] sm:p-7">
          <div className="mb-6 flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{t('配置执行任务', 'Configure task')}</h2>
              <p className="mt-1 text-sm text-slate-500">{t('填写任务目标并配置执行环境', 'Set the task goal and execution environment')}</p>
            </div>
            <span className="rounded-xl bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700">{t('单设备执行', 'Single device')}</span>
          </div>

          <div className="space-y-5">
            <div>
              <label className="mb-2 block text-sm font-semibold text-slate-700">
                {t('任务描述', 'Task')} <span className="text-rose-500">*</span>
              </label>
              <textarea
                rows={4}
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10"
                placeholder={t('描述你想让 Agent 完成的任务，例如：打开设置页面，检查 Wi-Fi 是否已开启', 'Describe the task for the Agent, e.g. open Settings and check whether Wi-Fi is on')}
                value={goal}
                onChange={e => setGoal(e.target.value)}
              />
            </div>

            <div>
              <label className="mb-2 block text-sm font-semibold text-slate-700">
                {t('预期结果', 'Expected result')} <span className="font-normal text-slate-400">{t('（可选，不填则默认"任务完成"）', '(optional, defaults to "task complete")')}</span>
              </label>
              <input
                type="text"
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10"
                placeholder={t('例如：Wi-Fi 开关显示为开启状态', 'e.g. the Wi-Fi toggle shows as on')}
                value={expected}
                onChange={e => setExpected(e.target.value)}
              />
            </div>

            {projects.length > 0 && (
              <div>
                <label className="mb-2 block text-sm font-semibold text-slate-700">
                  {t('所属项目', 'Project')} <span className="font-normal text-slate-400">{t('（按设备平台选择应用，共用项目知识库）', '(app selected by device platform, shared project knowledge)')}</span>
                </label>
                <select className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={projectId} onChange={e => setProjectId(e.target.value)}>
                  <option value="">— {t('不使用', 'None')} —</option>
                  {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
            )}

            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <div className="mb-3 text-sm font-semibold text-slate-700">{t('测试账号', 'Test account')}</div>
              <TestAccountSelect projectId={projectId} value={testAccountId} onChange={setTestAccountId} platform={devices.find(d => d.id === deviceId)?.platform} />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-semibold text-slate-700">{t('执行设备', 'Device')}</label>
                {onlineDevices.length === 0 ? (
                  <p className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600">{t('无在线设备', 'No online devices')}</p>
                ) : (
                  <select className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={deviceId} onChange={e => setDeviceId(e.target.value)}>
                    <option value="">— {t('选择设备', 'Select device')} —</option>
                    {onlineDevices.map(d => <option key={d.id} value={d.id}>{d.name}{d.connection_type === 'hdc_bridge' ? ' · 鸿蒙' : d.connection_type === 'ios_bridge' ? ' · iOS' : ''}</option>)}
                  </select>
                )}
              </div>
              <div>
                <label className="mb-2 block text-sm font-semibold text-slate-700">{t('最大步数', 'Max steps')}</label>
                <input type="number" className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={maxSteps} onChange={e => setMaxSteps(parseInt(e.target.value) || 20)} min={5} max={100} />
                <p className="mt-1.5 text-xs text-slate-400">{t('范围：5–100 步', 'Range: 5–100 steps')}</p>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-semibold text-slate-700">Provider</label>
                <select className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={provider} onChange={e => handleProviderChange(e.target.value)}>
                  {PROVIDERS.map(p => <option key={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-2 block text-sm font-semibold text-slate-700">Model</label>
                <input type="text" className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 font-mono text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={model} onChange={e => setModel(e.target.value)} />
              </div>
            </div>

            <div className="flex flex-col-reverse gap-3 border-t border-slate-100 pt-5 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-slate-400">{t('启动后将进入任务详情页查看执行过程', 'You can monitor execution in the task detail page')}</p>
              <button className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-blue-600 to-blue-500 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-500/20 transition hover:-translate-y-0.5 hover:shadow-blue-500/30 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:translate-y-0" disabled={!canSubmit} onClick={() => runMut.mutate()}>
                {runMut.isPending ? t('启动中…', 'Starting…') : t('▶ 开始任务', '▶ Start task')}
              </button>
            </div>
          </div>
        </section>

        <aside className="space-y-4">
          <div className="rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 to-cyan-50 p-5">
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-white text-blue-600 shadow-sm">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/><circle cx="12" cy="12" r="4"/></svg>
            </div>
            <h3 className="text-sm font-semibold text-slate-800">{t('执行前检查', 'Before you start')}</h3>
            <ul className="mt-3 space-y-2 text-xs leading-5 text-slate-600">
              <li>• {t('确认目标设备处于在线状态', 'Make sure the target device is online')}</li>
              <li>• {t('任务描述尽量明确、可验证', 'Use a clear and verifiable task goal')}</li>
              <li>• {t('预期结果可用于辅助判断任务完成情况', 'Expected results help verify completion')}</li>
            </ul>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_6px_22px_rgba(31,75,135,0.04)]">
            <h3 className="text-sm font-semibold text-slate-800">{t('当前配置', 'Current configuration')}</h3>
            <div className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between gap-3"><span className="text-slate-400">{t('在线设备', 'Online devices')}</span><strong className="text-slate-700">{onlineDevices.length}</strong></div>
              <div className="flex items-center justify-between gap-3"><span className="text-slate-400">Provider</span><strong className="max-w-[150px] truncate text-slate-700">{provider}</strong></div>
              <div className="flex items-center justify-between gap-3"><span className="text-slate-400">Model</span><strong className="max-w-[150px] truncate font-mono text-xs text-slate-700">{model || '—'}</strong></div>
              <div className="flex items-center justify-between gap-3"><span className="text-slate-400">{t('最大步数', 'Max steps')}</span><strong className="text-slate-700">{maxSteps}</strong></div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
