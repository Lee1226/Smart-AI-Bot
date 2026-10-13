import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { confirmSuiteImport, fetchSuiteImportMetadata, previewSuiteImport, previewSuiteOrganization, type SuiteImportDraft } from '../lib/api'

export function SuiteOrganizationInfo({ suiteId }: { suiteId: string }) {
  const { data, isError } = useQuery({ queryKey: ['suite-import-metadata', suiteId], queryFn: () => fetchSuiteImportMetadata(suiteId) })
  if (isError) return <p className="text-sm text-red-600 mb-3">整理来源说明加载失败，请刷新重试。</p>
  if (!data) return null
  return <details className="border rounded p-3 text-sm mb-3">
    <summary className="cursor-pointer">AI 整理说明 · 原始 {data.source_cases?.length || 0} 条 · {data.review_notes?.length || 0} 项待确认</summary>
    {!!data.changes?.length && <ul className="list-disc pl-5 mt-3">{data.changes.map((note, i) => <li key={i}>{note.replace('source_ids', '原用例对应关系')}</li>)}</ul>}
    {!!data.review_notes?.length && <div className="bg-amber-50 rounded p-3 mt-3"><strong>待确认事项</strong><ul className="list-disc pl-5">{data.review_notes.map((note, i) => <li key={i}>{note}</li>)}</ul></div>}
    <h3 className="font-medium mt-3">原始内容</h3>
    {data.source_cases?.map((source, i) => <div key={source.id} className="border-t mt-2 pt-2"><strong>原用例 {i + 1}</strong><p className="whitespace-pre-wrap">{source.path}</p><p className="whitespace-pre-wrap">预期：{source.expected || '执行成功'}</p>{source.steps.map((step, j) => <p key={j}>{step.action} → {step.expected}</p>)}</div>)}
  </details>
}

const errorMessage = (error: unknown) => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : '操作失败，请重试或检查模型配置'
}

export default function AISuiteImport({ suiteId }: { suiteId?: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File>()
  const [platform, setPlatform] = useState('android')
  const [draft, setDraft] = useState<SuiteImportDraft>()
  const [error, setError] = useState('')
  const organize = useMutation({
    mutationFn: () => suiteId ? previewSuiteOrganization(suiteId, platform) : previewSuiteImport(file!, platform),
    onSuccess: result => { setDraft(result); setError('') },
    onError: e => setError(errorMessage(e)),
  })
  const save = useMutation({
    mutationFn: () => confirmSuiteImport(draft!),
    onSuccess: result => {
      qc.invalidateQueries({ queryKey: ['suites'] })
      setOpen(false); setDraft(undefined); setFile(undefined)
      navigate(`/suites/${result.id}`)
    },
    onError: e => setError(errorMessage(e)),
  })
  const update = (edit: (next: SuiteImportDraft) => void) => setDraft(previous => {
    if (!previous) return previous
    const next = structuredClone(previous)
    edit(next)
    return next
  })
  const busy = organize.isPending || save.isPending
  const inputClass = 'w-full border rounded px-3 py-1.5 text-sm bg-white'
  return <>
    <button type="button" className="border border-primary text-primary px-3 py-1.5 rounded text-sm hover:bg-primary-soft"
      onClick={() => { setError(''); setDraft(undefined); setOpen(true) }}>
      {suiteId ? 'AI 整理用例' : 'AI 整理后导入'}
    </button>
    {open && <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <section role="dialog" aria-modal="true" aria-label="AI 整理测试用例" className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] overflow-y-auto p-6">
        <div className="flex justify-between gap-4 mb-3">
          <h2 className="text-xl font-bold">AI 整理测试用例</h2>
          <button disabled={busy} onClick={() => setOpen(false)} className="text-gray-500 disabled:opacity-40">关闭</button>
        </div>
        <p className="text-sm text-gray-500 mb-4">区分前置条件、操作和预期，合并重复操作与同页检查项，保留不同条件的分支。使用当前账号设置的 AI 模型，先生成草稿，确认后另存为新套件。</p>
        <fieldset disabled={busy} className="space-y-4 disabled:opacity-70">
          <div className="flex gap-3 items-end">
            <label className="text-sm">目标平台（用于整理）
              <select aria-label="整理目标平台" className={`${inputClass} mt-1`} value={platform} onChange={e => { setPlatform(e.target.value); setDraft(undefined) }}>
                <option value="android">Android</option><option value="ios">iOS</option><option value="harmony">鸿蒙</option>
              </select>
            </label>
            {!suiteId && <div className="flex-1">
              <input ref={fileRef} aria-label="选择待整理文件" type="file" accept=".xmind,.md,.markdown" className="block text-sm"
                onChange={e => { setFile(e.target.files?.[0]); setDraft(undefined) }} />
              {file && <p className="text-xs text-gray-500 mt-1">{file.name}</p>}
            </div>}
            <button disabled={!suiteId && !file} className="bg-primary text-white rounded px-4 py-2 text-sm disabled:opacity-50"
              onClick={() => { setError(''); organize.mutate() }}>
              {organize.isPending ? 'AI 整理中…' : draft ? '重新整理' : '生成整理草稿'}
            </button>
          </div>
          {draft && <>
            <p className="text-sm text-primary">原始 {draft.sources.length} 条 → 整理后 {draft.cases.length} 条用例 · {draft.cases.reduce((n, c) => n + c.steps.length, 0)} 个操作步骤 · {draft.cases.reduce((n, c) => n + c.steps.reduce((s, step) => s + step.assertions.length, 0), 0)} 个检查项</p>
            <p className="text-xs text-gray-500">原要求已建立对应关系，仍需核对语义、分支和检查项是否正确。可直接修改下方草稿。</p>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">新套件名称<input aria-label="整理后的套件名称" className={inputClass} value={draft.name} onChange={e => update(d => { d.name = e.target.value })} /></label>
              <label className="text-sm">应用包名（可选）<input aria-label="整理后的应用包名" className={inputClass} placeholder="com.example.app" value={draft.app_package} onChange={e => update(d => { d.app_package = e.target.value })} /></label>
            </div>
            {!!draft.changes.length && <div className="bg-blue-50 rounded p-3 text-sm"><strong>整理说明</strong><ul className="list-disc pl-5 mt-1">{draft.changes.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
            {!!draft.review_notes.length && <div className="bg-amber-50 rounded p-3 text-sm"><strong>需要确认</strong><ul className="list-disc pl-5 mt-1">{draft.review_notes.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
            {draft.cases.map((test, ci) => <article className="border rounded-lg p-4 space-y-3" key={ci}>
              <label className="block text-sm font-medium">用例 {ci + 1}<input aria-label={`用例 ${ci + 1} 名称`} className={inputClass} value={test.title} onChange={e => update(d => { d.cases[ci].title = e.target.value })} /></label>
              <label className="block text-xs text-gray-500">前置条件（每行一条）<textarea aria-label={`用例 ${ci + 1} 前置条件`} className={inputClass} value={test.preconditions.join('\n')} onChange={e => update(d => { d.cases[ci].preconditions = e.target.value.split('\n') })} /></label>
              {test.steps.map((step, si) => <div key={si} className="border-t pt-3 space-y-2">
                <label className="block text-sm">操作 {si + 1}<textarea aria-label={`用例 ${ci + 1} 操作 ${si + 1}`} className={inputClass} value={step.action} onChange={e => update(d => { d.cases[ci].steps[si].action = e.target.value })} /></label>
                {step.assertions.map((assertion, ai) => <div key={ai} className="pl-4 border-l-2 border-primary-soft">
                  <label className="block text-xs text-gray-500">预期结果 {ai + 1}<textarea aria-label={`用例 ${ci + 1} 操作 ${si + 1} 预期 ${ai + 1}`} className={inputClass} value={assertion.text} onChange={e => update(d => { d.cases[ci].steps[si].assertions[ai].text = e.target.value })} /></label>
                  <p className="text-xs text-gray-400">对应原用例：{assertion.source_ids.map(id => id.replace('source-', '')).join('、')}</p>
                </div>)}
              </div>)}
            </article>)}
            <details className="border rounded p-3 text-sm"><summary className="cursor-pointer">对照原始内容（{draft.sources.length} 条）</summary>
              {draft.sources.map((source, i) => <div className="mt-3 border-t pt-2" key={source.id}><strong>原用例 {i + 1}</strong><p className="whitespace-pre-wrap">{source.path}</p><p className="whitespace-pre-wrap">预期：{source.expected || '执行成功'}</p>
                {source.steps.map((step, si) => <p key={si}>{step.action} → {step.expected}</p>)}
              </div>)}
            </details>
            <button className="w-full bg-primary text-white rounded py-2" onClick={() => { setError(''); save.mutate() }}>
              {save.isPending ? '保存中…' : '确认并另存为新套件'}
            </button>
          </>}
        </fieldset>
        {error && <p role="alert" className="text-red-600 text-sm mt-3">{error}</p>}
      </section>
    </div>}
  </>
}
