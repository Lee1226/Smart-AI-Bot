import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createLLMConnection, createLLMModel, deleteLLMConnection, deleteLLMModel,
  fetchLLMConnections, fetchLLMModels, importExistingLLMSettings, LLMConnection, LLMModel,
  testLLMConnection, updateLLMConnection, updateLLMModel, fetchLLMProviders,
} from '../lib/api'

const inputClass = 'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100'
const buttonClass = 'rounded-lg px-3 py-2 text-sm font-medium transition disabled:opacity-50'

export default function ModelRepository() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'connections' | 'models'>('connections')
  const [selectedId, setSelectedId] = useState('')
  const [connectionDialog, setConnectionDialog] = useState(false)
  const [modelDialog, setModelDialog] = useState(false)
  const [editingConnection, setEditingConnection] = useState<LLMConnection | null>(null)
  const [editingModel, setEditingModel] = useState<LLMModel | null>(null)
  const [form, setForm] = useState({ name: '', provider: '', base_url: '', api_key: '', aws_access_key_id: '', aws_secret_access_key: '', aws_region_name: 'us-east-1', notes: '' })
  const [modelForm, setModelForm] = useState({ model_name: '', description: '', capabilities: 'text', enabled: true })
  const [message, setMessage] = useState('')
  const { data: providers = [] } = useQuery({ queryKey: ['llm-providers'], queryFn: () => fetchLLMProviders(true) })
  const availableProviders = providers.filter(p => p.enabled || p.provider_id === form.provider)
  const providerLabel = (value: string) => providers.find(p => p.provider_id === value)?.name || value
  const connectionProviderLabel = (connection: LLMConnection) => providers.find(p => p.id === connection.provider_catalog_id)?.name || providerLabel(connection.provider)
  const selectedProvider = providers.find(p => p.provider_id === form.provider)
  const { data: connections = [], isLoading } = useQuery({ queryKey: ['llm-connections'], queryFn: fetchLLMConnections })
  const current = connections.find(c => c.id === selectedId) || connections[0]
  const activeId = current?.id || ''
  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ['llm-models', activeId], queryFn: () => fetchLLMModels(activeId), enabled: !!activeId,
  })
  const enabledModels = useMemo(() => connections.reduce((n, c) => n + (c.id === activeId ? models.filter(m => m.enabled).length : 0), 0), [connections, activeId, models])
  const refresh = () => { qc.invalidateQueries({ queryKey: ['llm-connections'] }); qc.invalidateQueries({ queryKey: ['llm-models'] }); qc.invalidateQueries({ queryKey: ['llm-providers'] }) }
  const connectionMutation = useMutation({
    mutationFn: () => editingConnection
      ? updateLLMConnection(editingConnection.id, { name: form.name, provider: form.provider, base_url: form.base_url, api_key: form.api_key || undefined, aws_access_key_id: form.aws_access_key_id || undefined, aws_secret_access_key: form.aws_secret_access_key || undefined, aws_region_name: form.aws_region_name, notes: form.notes })
      : createLLMConnection(form),
    onSuccess: result => { setSelectedId(result.id); setConnectionDialog(false); setEditingConnection(null); setMessage('连接配置已保存'); refresh() },
    onError: (e: any) => setMessage(e.response?.data?.detail || '保存连接失败'),
  })
  const modelMutation = useMutation({
    mutationFn: () => editingModel
      ? updateLLMModel(editingModel.id, { model_name: modelForm.model_name, description: modelForm.description, capabilities: modelForm.capabilities.split(',').map(x => x.trim()).filter(Boolean), enabled: modelForm.enabled })
      : createLLMModel(activeId, { model_name: modelForm.model_name, description: modelForm.description, capabilities: modelForm.capabilities.split(',').map(x => x.trim()).filter(Boolean), enabled: modelForm.enabled }),
    onSuccess: () => { setModelDialog(false); setEditingModel(null); setMessage('模型已保存'); refresh() },
    onError: (e: any) => setMessage(e.response?.data?.detail || '保存模型失败'),
  })
  const testMutation = useMutation({
    mutationFn: (id: string) => testLLMConnection(id),
    onSuccess: r => { setMessage(`连接测试成功：${r.tested_model}`); refresh() },
    onError: (e: any) => { setMessage(e.response?.data?.detail || '连接测试失败'); refresh() },
  })
  const importMutation = useMutation({
    mutationFn: importExistingLLMSettings,
    onSuccess: r => {
      setMessage(`已导入 ${r.imported_connections} 个连接、${r.imported_models} 个模型${r.skipped_providers.length ? `；未配置凭据的供应商已跳过：${r.skipped_providers.join('、')}` : ''}`)
      refresh()
    },
    onError: (e: any) => setMessage(e.response?.data?.detail || '导入现有模型配置失败'),
  })
  const openCreateConnection = () => { setEditingConnection(null); setForm({ name: '', provider: '', base_url: '', api_key: '', aws_access_key_id: '', aws_secret_access_key: '', aws_region_name: 'us-east-1', notes: '' }); setConnectionDialog(true) }
  const openEditConnection = (c: LLMConnection) => {
    setEditingConnection(c); setForm({ name: c.name, provider: providers.find(p => p.id === c.provider_catalog_id)?.provider_id || c.provider, base_url: c.base_url, api_key: '', aws_access_key_id: '', aws_secret_access_key: '', aws_region_name: c.aws_region_name, notes: c.notes }); setConnectionDialog(true)
  }
  const openModel = (m?: LLMModel) => {
    setEditingModel(m || null); setModelForm(m ? { model_name: m.model_name, description: m.description, capabilities: m.capabilities.join(', '), enabled: m.enabled } : { model_name: '', description: '', capabilities: 'text', enabled: true }); setModelDialog(true)
  }
  const removeConnection = async (c: LLMConnection) => {
    if (!confirm(`确定删除连接“${c.name}”？`)) return
    try { await deleteLLMConnection(c.id); if (selectedId === c.id) setSelectedId(''); setMessage('连接已删除'); refresh() }
    catch (e: any) { setMessage(e.response?.data?.detail || '删除失败') }
  }
  const removeModel = async (m: LLMModel) => {
    if (!confirm(`确定删除模型“${m.model_name}”？`)) return
    try { await deleteLLMModel(m.id); setMessage('模型已删除'); refresh() }
    catch (e: any) { setMessage(e.response?.data?.detail || '删除失败') }
  }

  return <div className="mx-auto max-w-6xl space-y-5">
    <header className="rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] p-6 text-white shadow-[0_16px_40px_rgba(37,99,235,0.16)]">
      <div className="text-xs font-semibold tracking-[0.16em] text-blue-100">AI TERMINAL · MODEL MANAGEMENT</div>
      <h1 className="mt-2 text-2xl font-bold">模型配置</h1>
      <p className="mt-2 max-w-2xl text-sm text-blue-100">集中管理大模型供应商连接、访问凭据和可用模型。此处配置不会自动改变当前 Agent / Verifier 的运行模型。</p>
    </header>
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs text-slate-500">连接总数</div><div className="mt-1 text-2xl font-semibold text-slate-800">{connections.length}</div></div>
      <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs text-slate-500">正常连接</div><div className="mt-1 text-2xl font-semibold text-emerald-600">{connections.filter(c => c.last_test_status === 'success').length}</div></div>
      <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs text-slate-500">当前连接启用模型</div><div className="mt-1 text-2xl font-semibold text-slate-800">{enabledModels}</div></div>
    </div>
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_28px_rgba(31,75,135,0.04)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
          <button className={`${buttonClass} ${tab === 'connections' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-600'}`} onClick={() => setTab('connections')}>连接管理</button>
          <button className={`${buttonClass} ${tab === 'models' ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-600'}`} onClick={() => setTab('models')}>模型管理</button>
        </div>
        {tab === 'connections'
          ? <div className="flex flex-wrap gap-2">
              <button className={`${buttonClass} border border-slate-200 bg-white text-slate-600 hover:bg-slate-50`} disabled={importMutation.isPending} onClick={() => importMutation.mutate()}>{importMutation.isPending ? '导入中…' : '导入现有平台配置'}</button>
              <button className={`${buttonClass} bg-blue-600 text-white hover:bg-blue-700`} onClick={openCreateConnection}>＋ 添加连接</button>
            </div>
           : <button className={`${buttonClass} bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40`} disabled={!current} onClick={() => openModel()}>＋ 添加模型</button>}
      </div>
      {message && <div className="flex items-center justify-between border-b border-blue-100 bg-blue-50 px-5 py-2 text-sm text-blue-800"><span>{message}</span><button onClick={() => setMessage('')} aria-label="关闭">×</button></div>}
      {tab === 'connections' ? <div className="p-5">
        {isLoading ? <p className="py-10 text-center text-sm text-slate-400">加载中…</p> : connections.length === 0
          ? <div className="rounded-xl border border-dashed border-slate-300 px-6 py-14 text-center"><div className="text-base font-semibold text-slate-700">还没有模型连接</div><p className="mt-2 text-sm text-slate-500">添加一个供应商连接，再在连接下维护可用模型。</p><button className={`${buttonClass} mt-4 bg-blue-600 text-white`} onClick={openCreateConnection}>添加第一个连接</button></div>
          : <div className="grid gap-3 md:grid-cols-2">{connections.map(c => <article key={c.id} className={`rounded-xl border p-4 transition ${activeId === c.id ? 'border-blue-300 bg-blue-50/40' : 'border-slate-200 hover:border-slate-300'}`}>
            <div className="flex items-start gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-lg text-slate-600">◈</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-800">{c.name}</h3><span className={`rounded-full px-2 py-0.5 text-[11px] ${c.last_test_status === 'success' ? 'bg-emerald-50 text-emerald-700' : c.last_test_status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-slate-100 text-slate-500'}`}>{c.last_test_status === 'success' ? '连接正常' : c.last_test_status === 'failed' ? '测试失败' : '尚未测试'}</span></div><p className="mt-1 text-xs text-slate-500">{connectionProviderLabel(c)} · {c.model_count} 个模型</p><p className="mt-2 truncate font-mono text-xs text-slate-400">{c.base_url || '未设置 API Base URL'}</p></div></div>
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3"><button className={`${buttonClass} bg-blue-50 text-blue-700`} onClick={() => { setSelectedId(c.id); setTab('models') }}>管理模型</button><button disabled={testMutation.isPending || c.model_count === 0} className={`${buttonClass} border border-slate-200 text-slate-600`} onClick={() => testMutation.mutate(c.id)}>{testMutation.isPending ? '测试中…' : '测试连接'}</button><button className={`${buttonClass} text-slate-500 hover:bg-slate-100`} onClick={() => openEditConnection(c)}>编辑</button><button className={`${buttonClass} text-rose-600 hover:bg-rose-50`} onClick={() => removeConnection(c)}>删除</button></div>
          </article>)}</div>}
      </div> : tab === 'models' ? <div className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3"><label className="text-sm text-slate-600">所属连接</label><select className={`${inputClass} max-w-sm`} value={activeId} onChange={e => setSelectedId(e.target.value)}>{connections.map(c => <option key={c.id} value={c.id}>{c.name} · {connectionProviderLabel(c)}</option>)}</select>{current && <span className="text-xs text-slate-400">{current.base_url || connectionProviderLabel(current)}</span>}</div>
        {!current ? <p className="py-10 text-center text-sm text-slate-400">请先创建一个模型连接。</p> : modelsLoading ? <p className="py-10 text-center text-sm text-slate-400">加载模型中…</p> : models.length === 0 ? <div className="rounded-xl border border-dashed border-slate-300 px-6 py-12 text-center"><p className="font-medium text-slate-700">这个连接还没有模型</p><p className="mt-2 text-sm text-slate-500">可以手动添加模型名称，不依赖供应商模型列表接口。</p><button className={`${buttonClass} mt-4 bg-blue-600 text-white`} onClick={() => openModel()}>添加模型</button></div> :
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead><tr className="border-b border-slate-100 text-xs text-slate-500"><th className="px-3 py-3 font-medium">模型名称</th><th className="px-3 py-3 font-medium">能力</th><th className="px-3 py-3 font-medium">状态</th><th className="px-3 py-3 text-right font-medium">操作</th></tr></thead><tbody>{models.map(m => <tr key={m.id} className="border-b border-slate-50 last:border-0"><td className="px-3 py-3"><div className="font-mono font-medium text-slate-800">{m.model_name}</div><div className="mt-1 text-xs text-slate-400">{m.description || '—'}</div></td><td className="px-3 py-3"><div className="flex flex-wrap gap-1">{m.capabilities.map(c => <span key={c} className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{c}</span>)}</div></td><td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-xs ${m.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{m.enabled ? '已启用' : '已停用'}</span></td><td className="px-3 py-3 text-right"><button className="px-2 py-1 text-xs text-blue-700 hover:underline" onClick={() => openModel(m)}>编辑</button><button className="px-2 py-1 text-xs text-rose-600 hover:underline" onClick={() => removeModel(m)}>删除</button></td></tr>)}</tbody></table></div>}
      </div>}
    </section>
    {connectionDialog && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true"><div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-slate-800">{editingConnection ? '编辑连接' : '添加模型连接'}</h2><button onClick={() => setConnectionDialog(false)} className="text-xl text-slate-400">×</button></div><div className="mt-5 grid gap-4">
      <label className="space-y-1 text-xs font-medium text-slate-600">连接名称<input className={inputClass} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="例如：百炼生产环境" /></label>
      <label className="space-y-1 text-xs font-medium text-slate-600">供应商类型<select className={inputClass} value={form.provider} onChange={e => { const p = availableProviders.find(x => x.provider_id === e.target.value); setForm({ ...form, provider: p?.provider_id || "", base_url: p?.default_base_url || "", api_key: "" }) }}><option value="">请选择供应商</option>{availableProviders.map(p => <option key={p.id} value={p.provider_id}>{p.name}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium text-slate-600">API Base URL<input className={inputClass} value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} placeholder="https://..." /></label>
      {selectedProvider?.auth_type === "api_key" && <label className="space-y-1 text-xs font-medium text-slate-600">API Key<input type="password" className={inputClass} value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} placeholder={editingConnection ? '留空保持现有密钥' : '输入 API Key'} /></label>}
      {selectedProvider?.auth_type === 'aws' && <><label className="space-y-1 text-xs font-medium text-slate-600">AWS Access Key ID<input className={inputClass} value={form.aws_access_key_id} onChange={e => setForm({ ...form, aws_access_key_id: e.target.value })} /></label><label className="space-y-1 text-xs font-medium text-slate-600">AWS Secret Access Key<input type="password" className={inputClass} value={form.aws_secret_access_key} onChange={e => setForm({ ...form, aws_secret_access_key: e.target.value })} /></label><label className="space-y-1 text-xs font-medium text-slate-600">AWS Region<input className={inputClass} value={form.aws_region_name} onChange={e => setForm({ ...form, aws_region_name: e.target.value })} /></label></>}
      <label className="space-y-1 text-xs font-medium text-slate-600">备注<textarea className={inputClass} rows={2} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
      <div className="flex justify-end gap-2 pt-2"><button className={`${buttonClass} border border-slate-200 text-slate-600`} onClick={() => setConnectionDialog(false)}>取消</button><button disabled={connectionMutation.isPending || !form.name.trim() || !form.provider} className={`${buttonClass} bg-blue-600 text-white`} onClick={() => connectionMutation.mutate()}>{connectionMutation.isPending ? '保存中…' : '保存连接'}</button></div>
    </div></div></div>}
    {modelDialog && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-slate-800">{editingModel ? '编辑模型' : '添加模型'}</h2><button onClick={() => setModelDialog(false)} className="text-xl text-slate-400">×</button></div><div className="mt-5 grid gap-4">
      <label className="space-y-1 text-xs font-medium text-slate-600">模型名称<input className={inputClass} value={modelForm.model_name} onChange={e => setModelForm({ ...modelForm, model_name: e.target.value })} placeholder="例如：qwen-plus" /></label>
      <label className="space-y-1 text-xs font-medium text-slate-600">模型备注<input className={inputClass} value={modelForm.description} onChange={e => setModelForm({ ...modelForm, description: e.target.value })} placeholder="例如：通用对话模型" /></label>
      <label className="space-y-1 text-xs font-medium text-slate-600">能力标签（逗号分隔）<input className={inputClass} value={modelForm.capabilities} onChange={e => setModelForm({ ...modelForm, capabilities: e.target.value })} placeholder="text, vision, tool_calling" /></label>
      <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={modelForm.enabled} onChange={e => setModelForm({ ...modelForm, enabled: e.target.checked })} />启用该模型</label>
      <div className="flex justify-end gap-2 pt-2"><button className={`${buttonClass} border border-slate-200 text-slate-600`} onClick={() => setModelDialog(false)}>取消</button><button disabled={modelMutation.isPending || !modelForm.model_name.trim()} className={`${buttonClass} bg-blue-600 text-white`} onClick={() => modelMutation.mutate()}>{modelMutation.isPending ? '保存中…' : '保存模型'}</button></div>
    </div></div></div>}
  </div>
}
