import { useEffect, useState, ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchSettings, saveSettings, Settings as SettingsData,
  fetchProjects, createProject, updateProject, deleteProject, Project,
} from '../lib/api'
import { useT } from '../lib/i18n'
import ProjectTestAccounts from '../components/ProjectTestAccounts'
import { PROVIDER_MODELS, PROVIDERS } from '../lib/providerConfig'

type FieldDef = { key: keyof SettingsData; label: string; placeholder?: string; type?: string }

// Credential fields each provider needs. A provider with no entry needs no key.
const PROVIDER_FIELDS: Record<string, FieldDef[]> = {
  openai: [{ key: 'openai_api_key', label: 'API Key', placeholder: 'sk-…', type: 'password' }],
  anthropic: [
    { key: 'anthropic_api_key', label: 'API Key', placeholder: 'sk-ant-…', type: 'password' },
    { key: 'anthropic_base_url', label: 'Base URL (LiteLLM proxy, optional)', placeholder: 'https://litellm.example.com' },
  ],
  ark: [
    { key: 'ark_api_key', label: 'Ark API Key', placeholder: 'sk-…', type: 'password' },
    { key: 'ark_base_url', label: 'Base URL', placeholder: 'https://ark.cn-beijing.volces.com/api/v3' },
  ],
  bedrock: [
    { key: 'aws_access_key_id', label: 'AWS Access Key ID', placeholder: 'AKIA…' },
    { key: 'aws_secret_access_key', label: 'AWS Secret Access Key', type: 'password' },
    { key: 'aws_region_name', label: 'AWS Region', placeholder: 'us-east-2' },
  ],
  google: [{ key: 'gemini_api_key', label: 'API Key', type: 'password' }],
  zhipuai: [{ key: 'zhipu_api_key', label: 'API Key', type: 'password' }],
  groq: [{ key: 'groq_api_key', label: 'API Key', type: 'password' }],
  ollama: [{ key: 'ollama_base_url', label: 'Base URL', placeholder: 'http://localhost:11434' }],
}

function isConfigured(form: Partial<SettingsData>, provider: string): boolean {
  const fields = PROVIDER_FIELDS[provider] || []
  // ollama only needs a base URL which has a default → always "ready"
  if (provider === 'ollama') return true
  return fields.some(f =>
    !!(form[f.key] as string)?.trim() || form.configured_secrets?.includes(f.key as string)
  )
}

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
    <div className="border rounded-lg p-4 bg-white space-y-2">
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
  const [form, setForm] = useState<Partial<SettingsData>>({})
  const [saved, setSaved] = useState(false)
  const [clearSecrets, setClearSecrets] = useState<string[]>([])
  const [showOthers, setShowOthers] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [cliCopied, setCliCopied] = useState(false)

  // CLI download — address follows the current deployment origin (dynamic, not hard-coded)
  const cliDownloadUrl = `${window.location.origin}/api/app/cli/download`
  const installCommand = `curl -fsSL ${cliDownloadUrl} -o /usr/local/bin/sab && chmod +x /usr/local/bin/sab`
  const copyInstallCmd = async () => {
    try {
      await navigator.clipboard.writeText(installCommand)
      setCliCopied(true); setTimeout(() => setCliCopied(false), 2000)
    } catch { /* clipboard may be blocked on http */ }
  }

  useEffect(() => { if (remote) setForm(remote) }, [remote])

  const saveMut = useMutation({
    mutationFn: () => saveSettings({ ...form, clear_secrets: clearSecrets }),
    onSuccess: (data) => {
      setForm(data)
      setClearSecrets([])
      qc.invalidateQueries({ queryKey: ['settings'] }) // refresh useT so a language change re-renders the UI
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    },
  })

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  const provider = form.default_provider || 'openai'
  const set = (k: keyof SettingsData, v: string) => setForm(prev => ({ ...prev, [k]: v }))

  const Field = ({ f }: { f: FieldDef }) => (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{f.label}</label>
      <div className="flex items-center gap-2">
      <input
        type={f.type || 'text'}
        className="w-full border rounded px-3 py-1.5 text-sm font-mono"
        disabled={clearSecrets.includes(f.key) || saveMut.isPending}
        placeholder={clearSecrets.includes(f.key)
          ? t('待删除，保存后生效', 'Pending deletion; save to apply')
          : form.configured_secrets?.includes(f.key as string)
          ? t('已配置；留空保持不变', 'Configured; leave blank to keep')
          : (f.placeholder || '')}
        value={(form[f.key] as string) || ''}
        onChange={e => set(f.key, e.target.value)}
      />
      {form.configured_secrets?.includes(f.key) && (
        <button
          type="button"
          disabled={saveMut.isPending}
          className="shrink-0 text-xs text-red-600 hover:underline disabled:opacity-50"
          aria-label={`${clearSecrets.includes(f.key) ? t('撤销删除', 'Undo deletion') : t('删除已配置', 'Delete configured')} ${f.label}`}
          onClick={() => {
            set(f.key, '')
            setClearSecrets(prev => prev.includes(f.key) ? prev.filter(k => k !== f.key) : [...prev, f.key])
          }}
        >
          {clearSecrets.includes(f.key) ? t('撤销删除', 'Undo deletion') : t('删除已配置 Key', 'Delete saved key')}
        </button>
      )}
      </div>
      {clearSecrets.includes(f.key) && <p className="mt-1 text-xs text-red-600">{t('此凭证将在保存设置后删除，相关模型可能无法调用。', 'Saving will delete this credential; dependent models may become unavailable.')}</p>}
    </div>
  )

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
    <div className="max-w-xl">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <div className="bg-white border rounded-lg p-6 shadow-sm space-y-5">

        {/* ── Main language (AI output + report follow it) ── */}
        <div>
          <label className="block text-sm font-medium mb-1">{t('语言 / Language', 'Language')}</label>
          <select
            className="w-full border rounded px-3 py-1.5 text-sm"
            value={form.language || 'zh'}
            onChange={e => set('language', e.target.value)}
          >
            <option value="zh">简体中文</option>
            <option value="en">English</option>
          </select>
          <p className="text-xs text-gray-400 mt-1">{t('AI 的思考 / 理由 / 校验结论 / 计划 与测试报告都用这个语言。', "The AI's thinking / reasoning / verification conclusions / plans and the test report all use this language.")}</p>
        </div>

        {/* ── CLI download (sab) ── */}
        <div className="border-t pt-3">
          <h3 className="text-sm font-medium">{t('CLI 下载', 'CLI Download')}</h3>
          <p className="text-xs text-gray-400 mt-1 mb-3">
            {t('下载 sab 命令行工具，从脚本 / CI / AI 触发自动化测试。下载地址与安装命令跟随当前部署环境自动生成。', 'Download the sab CLI to trigger automated tests from scripts / CI / AI. The URL follows the current deployment.')}
          </p>
          <div className="flex items-center gap-2 mb-2">
            <a
              href={cliDownloadUrl}
              download="sab"
              className="px-3 py-1.5 bg-primary text-white text-sm rounded hover:bg-primary-deep"
            >
              {t('下载 sab', 'Download sab')}
            </a>
            <button
              onClick={copyInstallCmd}
              className="px-3 py-1.5 border border-gray-300 text-sm rounded hover:bg-gray-50"
            >
              {cliCopied ? t('✓ 已复制', '✓ Copied') : t('复制安装命令', 'Copy install command')}
            </button>
          </div>
          <code className="block bg-gray-50 border rounded px-3 py-2 text-xs font-mono text-gray-700 break-all">
            {installCommand}
          </code>
        </div>

        {/* ── Agent Model + selected provider's credentials ── */}
        <div className="space-y-3">
          <h3 className="text-sm font-medium">{t('Agent 模型', 'Agent Model')}</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Provider</label>
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={provider}
                onChange={e => {
                  const p = e.target.value
                  setForm(prev => ({ ...prev, default_provider: p, default_model: (PROVIDER_MODELS[p] || [])[0] || '' }))
                }}
              >
                {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Model</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                placeholder={PROVIDER_MODELS[provider]?.[0] || ''}
                value={form.default_model || ''}
                onChange={e => set('default_model', e.target.value)}
              />
            </div>
          </div>
          <p className="text-xs text-gray-400">
            {t('可直接输入自定义 model 名，如 us.anthropic.claude-sonnet-4-6', 'You can enter a custom model name directly, e.g. us.anthropic.claude-sonnet-4-6')}
          </p>

          {/* credentials for the currently-selected provider, inline */}
          {(PROVIDER_FIELDS[provider] || []).length > 0 && (
            <div className="bg-gray-50 border rounded p-3 space-y-3">
              <p className="text-xs font-medium text-gray-600">{provider} {t('凭证', 'credentials')}</p>
              {(PROVIDER_FIELDS[provider] || []).map(f => <Field key={f.key as string} f={f} />)}
            </div>
          )}
        </div>

        {/* ── Other providers (for fallback / switching) ── */}
        <Collapsible
          title={<>{t('其它 Provider 凭证', 'Other provider credentials')} <span className="text-xs font-normal text-gray-400">{t('（用于切换 / fallback）', '(for switching / fallback)')}</span></>}
          open={showOthers}
          onToggle={() => setShowOthers(v => !v)}
        >
          {PROVIDERS.filter(p => p !== provider).map(p => (
            <div key={p} className="border rounded p-3 space-y-3">
              <p className="text-xs font-medium text-gray-600 flex items-center gap-2">
                {p}
                {isConfigured(form, p)
                  ? <span className="text-ok">{t('✓ 已配置', '✓ Configured')}</span>
                  : <span className="text-gray-400">{t('○ 未配置', '○ Not configured')}</span>}
              </p>
              {(PROVIDER_FIELDS[p] || []).map(f => <Field key={f.key as string} f={f} />)}
            </div>
          ))}
        </Collapsible>

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
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Provider</label>
                <select
                  className="w-full border rounded px-3 py-1.5 text-sm"
                  value={form.verifier_provider || ''}
                  onChange={e => set('verifier_provider', e.target.value)}
                >
                  <option value="">{t('— 与 Agent 相同 —', '— Same as Agent —')}</option>
                  {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Model</label>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                  placeholder={t('留空 = 同 Agent', 'Empty = same as Agent')}
                  value={form.verifier_model || ''}
                  onChange={e => set('verifier_model', e.target.value)}
                />
              </div>
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
          className="w-full bg-primary text-white py-2 rounded font-medium hover:bg-primary-deep disabled:opacity-50"
          disabled={saveMut.isPending}
          onClick={() => {
            if (clearSecrets.length && !confirm(t('确认删除已标记的凭证？保存后无法恢复原 Key，需要重新填写。', 'Delete the marked credentials? Saved keys cannot be recovered; you will need to enter them again.'))) return
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
