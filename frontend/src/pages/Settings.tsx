import { useEffect, useState, ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchSettings, saveSettings, Settings as SettingsData,
  fetchProjects, createProject, updateProject, deleteProject, Project,
} from '../lib/api'
import { useT } from '../lib/i18n'
import ProjectTestAccounts from '../components/ProjectTestAccounts'
import { useExecutionModels } from '../components/ExecutionModelSelect'

function ProjectCard({ project, onDelete }: { project: Project; onDelete: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [form, setForm] = useState<Project>(project)
  const saveMut = useMutation({
    mutationFn: () => updateProject(project.id, form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
  const field = (label: string, key: Exclude<keyof Project, 'app_ids'>, ph: string) => (
    <div>
      <label className="text-xs text-gray-500">{label}</label>
      <input
        className="w-full border rounded px-2 py-1 text-sm font-mono"
        placeholder={ph}
        value={form[key]}
        onChange={e => setForm({ ...form, [key]: e.target.value })}
      />
    </div>
  )
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] space-y-3">
      {field(t('名称 / Name', 'Name'), 'name', t('项目名', 'Project name'))}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {(['android', 'harmony', 'ios'] as const).map(platform => (
          <div key={platform}>
            <label className="text-xs text-gray-500">{platform === 'android' ? 'Android 包名' : platform === 'harmony' ? '鸿蒙 Bundle Name' : 'iOS Bundle ID'}</label>
            <input autoComplete="off" name={`project-app-${platform}`} className="w-full border rounded px-2 py-1 text-sm font-mono"
              placeholder={platform === 'harmony' ? 'com.example.harmony' : 'com.example.app'}
              value={form.app_ids?.[platform] || ''}
              onChange={e => setForm({ ...form, app_ids: { ...form.app_ids, [platform]: e.target.value } })} />
          </div>
        ))}
      </div>
      {field(t('知识检索命令 / kb_search_cmd（项目 skill 提供的检索 CLI；优先，留空则用下方关键词兜底）', 'KB search command / kb_search_cmd (search CLI provided by project skill; preferred, leave empty to fall back to keyword search below)'), 'kb_search_cmd', '如 `<cmd> "<query>" -n N`')}
      {field(t('知识库路径 / kb_path（绝对路径，支持 ~/；用于读全文 + 无检索命令时的关键词兜底）', 'KB path / kb_path (absolute path, ~/ supported; used to read full text + keyword fallback when no search command)'), 'kb_path', '/Users/you/repo/knowledge 或 ~/repo/knowledge')}
      {field(t('源码路径 / source_root（卡点时 agent 可搜/读源码）', 'Source root / source_root (agent can search/read source when stuck)'), 'source_root', '~/repo/android-src')}
      <div className="flex gap-2 pt-1">
        <button
          className="px-3 py-1 bg-primary text-white text-xs rounded hover:bg-primary-deep disabled:opacity-50"
          disabled={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending ? t('保存中…', 'Saving…') : t('保存', 'Save')}
        </button>
        <button
          className="px-3 py-1 border border-red-200 text-red-600 text-xs rounded hover:bg-red-50"
          onClick={() => { if (confirm(t('删除这个项目档案？', 'Delete this project profile?'))) onDelete() }}
        >
          {t('删除', 'Delete')}
        </button>
      </div>
      <ProjectTestAccounts projectId={project.id} />
    </div>
  )
}

function ProjectProfiles() {
  const t = useT()
  const qc = useQueryClient()
  const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: fetchProjects })
  const createMut = useMutation({
    mutationFn: () => createProject({ name: '新项目' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
  const delMut = useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-lg font-semibold">{t('项目档案（可选）', 'Project Profiles (optional)')}</h2>
        <button
          className="text-sm text-primary hover:text-primary-deep"
          onClick={() => createMut.mutate()}
        >
          {t('+ 添加项目', '+ Add project')}
        </button>
      </div>
      <p className="text-xs text-gray-400 mb-3">
        {t(
          '项目统一关联知识库和源码。测试套件选择所属项目，执行时按设备平台使用对应应用标识；未配置的平台会提示补充。',
          'A project owns its knowledge base and source. Assign suites to a project; runs select the app identifier for the device platform.'
        )}
      </p>
      {projects.length === 0 && <p className="text-sm text-gray-400">{t('还没有项目档案。', 'No project profiles yet.')}</p>}
      <div className="space-y-3">
        {projects.map(p => <ProjectCard key={p.id} project={p} onDelete={() => delMut.mutate(p.id)} />)}
      </div>
    </div>
  )
}

export default function Settings() {
  const t = useT()
  const qc = useQueryClient()
  const { data: remote, isLoading } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const { models: executionModels, isLoading: modelsLoading, isError: modelsError } = useExecutionModels()
  const [form, setForm] = useState<Partial<SettingsData>>({})
  const [saved, setSaved] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => { if (remote) setForm(remote) }, [remote])

  const saveMut = useMutation({
    mutationFn: () => saveSettings(form),
    onSuccess: (data) => {
      setForm(data)
      qc.invalidateQueries({ queryKey: ['settings'] }) // refresh useT so a language change re-renders the UI
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    },
  })

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  const set = (k: keyof SettingsData, v: string) => setForm(prev => ({ ...prev, [k]: v }))

  const Collapsible = ({ title, open, onToggle, children }:
    { title: ReactNode; open: boolean; onToggle: () => void; children: ReactNode }) => (
    <div className="border-t pt-3">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between text-sm font-medium text-gray-700 hover:text-gray-900"
      >
        <span>{title}</span>
        <span className="text-gray-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="mt-3 space-y-3">{children}</div>}
    </div>
  )

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] p-5 text-white shadow-[0_16px_40px_rgba(37,99,235,0.18)] sm:p-6">
        <div className="text-xs font-semibold tracking-[0.18em] text-blue-100">AI 终端 · 平台配置</div>
        <h1 className="mt-2 text-xl font-bold">平台设置</h1>
        <p className="mt-2 text-sm text-blue-100">配置语言、Agent 模型、Verifier 模型与通知方式。模型连接和凭据统一在模型配置中管理。</p>
      </header>

      <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-[0_8px_28px_rgba(31,75,135,0.05)] space-y-5">

        {/* ── Main language (AI output + report follow it) ── */}
        <div>
          <label className="block text-sm font-medium mb-1">{t('语言 / Language', 'Language')}</label>
          <select
            className="w-full rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2 text-sm outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-100"
            value={form.language || 'zh'}
            onChange={e => set('language', e.target.value)}
          >
            <option value="zh">简体中文</option>
            <option value="en">English</option>
          </select>
          <p className="text-xs text-gray-400 mt-1">{t('AI 的思考 / 理由 / 校验结论 / 计划 与测试报告都用这个语言。', "The AI's thinking / reasoning / verification conclusions / plans and the test report all use this language.")}</p>
        </div>

        {/* ── Agent model from the model repository ── */}
        <div className="space-y-3">
          <h3 className="text-sm font-medium">{t('Agent 模型', 'Agent Model')}</h3>
          <label className="block text-xs text-gray-500 mb-1">{t('执行模型', 'Execution model')}</label>
          <select
            className="w-full border rounded-xl px-3 py-2 text-sm"
            value={form.default_model_id || ''}
            disabled={modelsLoading || modelsError || saveMut.isPending}
            onChange={e => set('default_model_id', e.target.value)}
          >
            <option value="">{t('请选择模型仓库中的模型', 'Select a model from the model repository')}</option>
            {executionModels.map(model => (
              <option key={model.id} value={model.id}>
                {model.connection_name || '未命名连接'} · {model.provider || 'provider'} · {model.model_name}
              </option>
            ))}
          </select>
          {modelsError && <p className="text-xs text-red-600">{t('模型列表加载失败，请刷新页面重试。', 'Failed to load models. Refresh and try again.')}</p>}
          {!modelsLoading && !modelsError && executionModels.length === 0 && (
            <p className="text-xs text-amber-600">{t('模型仓库中暂无已启用模型，请先添加并启用模型。', 'No enabled models. Add and enable a model in the model repository first.')}</p>
          )}
          {form.default_model_id && !executionModels.some(model => model.id === form.default_model_id) && (
            <p className="text-xs text-amber-600">{t('当前保存的 Agent 模型不在可用列表中，请重新选择并保存。', 'The saved Agent model is unavailable. Select an available model and save.')}</p>
          )}
          <p className="text-xs text-gray-400">
            {t('Agent 的模型、API Key、Base URL 和协议参数统一从模型配置仓库读取。', 'The Agent model, API key, Base URL and protocol parameters are resolved from the model repository.')}
          </p>
        </div>

        {/* ── Advanced: Verifier + Webhook ── */}
        <Collapsible
          title={<>{t('高级：Verifier / Webhook', 'Advanced: Verifier / Webhook')}</>}
          open={showAdvanced}
          onToggle={() => setShowAdvanced(v => !v)}
        >
          <div>
            <p className="text-sm font-medium mb-1">Verification Model <span className="text-xs font-normal text-gray-400">(optional)</span></p>
            <p className="text-xs text-gray-400 mb-2">
              {t('用于 pass/fail 判断的专用模型。留空则与 Agent 使用同一模型。', 'A dedicated model for pass/fail judgement. Leave empty to use the same model as the Agent.')}
            </p>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Verifier 模型', 'Verifier model')}</label>
              <select
                className="w-full border rounded px-3 py-2 text-sm"
                value={form.verifier_model_id || ''}
                disabled={modelsLoading || modelsError || saveMut.isPending}
                onChange={e => setForm(prev => ({
                  ...prev,
                  verifier_model_id: e.target.value,
                  // Clear legacy name-based settings so an empty selection truly means Agent model.
                  verifier_provider: '',
                  verifier_model: '',
                }))}
              >
                <option value="">
                  {form.verifier_provider || form.verifier_model
                    ? t('保留旧版 Verifier 配置（选择此项会改为同 Agent）', 'Keep legacy Verifier config (selecting this changes to Agent)')
                    : t('与本次任务的 Agent 模型相同', 'Use the Agent model selected for this run')}
                </option>
                {executionModels.map(model => (
                  <option key={model.id} value={model.id}>
                    {model.connection_name || '未命名连接'} · {model.provider || 'provider'} · {model.model_name}
                  </option>
                ))}
              </select>
              {!form.verifier_model_id && (form.verifier_provider || form.verifier_model) && (
                <p className="mt-1 text-xs text-amber-600">
                  {t('当前账号仍有旧版 Verifier 配置正在生效。选择其他模型或明确选择“同 Agent”并保存后，将切换到模型仓库配置。', 'A legacy Verifier setting is still active. Choose a repository model or explicitly switch to Agent and save to migrate.')}
                </p>
              )}
              {modelsError && <p className="mt-1 text-xs text-red-600">{t('模型列表加载失败，请刷新页面重试。', 'Failed to load models. Refresh and try again.')}</p>}
              {!modelsLoading && !modelsError && executionModels.length === 0 && (
                <p className="mt-1 text-xs text-gray-400">{t('模型仓库中暂无已启用模型。', 'No enabled models in the model repository.')}</p>
              )}
            </div>
          </div>

          <div className="pt-1">
            <p className="text-sm font-medium mb-1">Webhook Notification <span className="text-xs font-normal text-gray-400">(optional)</span></p>
            <p className="text-xs text-gray-400 mb-2">{t('Run 完成后推送结果到飞书/钉钉/Slack。', 'Push results to Feishu/DingTalk/Slack after a run completes.')}</p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Type</label>
                <select
                  className="w-full border rounded px-3 py-1.5 text-sm"
                  value={form.webhook_type || ''}
                  onChange={e => set('webhook_type', e.target.value)}
                >
                  <option value="">-- disabled --</option>
                  <option value="feishu">Feishu / Lark</option>
                  <option value="dingtalk">DingTalk</option>
                  <option value="slack">Slack</option>
                  <option value="custom">Custom POST</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-gray-500 mb-1">Webhook URL</label>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                  placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                  value={form.webhook_url || ''}
                  onChange={e => set('webhook_url', e.target.value)}
                />
              </div>
            </div>
          </div>
        </Collapsible>

        <button
          className="w-full rounded-xl bg-gradient-to-r from-[#2169D5] to-[#328FE5] py-3 font-semibold text-white shadow-sm transition hover:brightness-105 disabled:opacity-50"
          disabled={saveMut.isPending}
          onClick={() => {
            saveMut.mutate()
          }}
        >
          {saved ? t('✓ 已保存', '✓ Saved') : saveMut.isPending ? t('保存中…', 'Saving…') : t('保存设置', 'Save Settings')}
        </button>
        {saveMut.isError && <p role="alert" className="text-sm text-red-600">{t('保存失败，请重试；删除标记尚未生效。', 'Save failed. Retry; deletion has not been applied.')}</p>}
      </div>

      <ProjectProfiles />
    </div>
  )
}
