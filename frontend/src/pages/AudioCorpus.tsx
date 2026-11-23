import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchAudioCorpus, uploadAudioCorpus, updateAudioCorpus, deleteAudioCorpus,
  fetchAudioCorpusFile, type AudioCorpusItem,
} from '../lib/api'

const inputClass = 'w-full rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10'
const formatDuration = (seconds: number) => {
  if (!Number.isFinite(seconds)) return '—'
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return mins ? `${mins}分${String(secs).padStart(2, '0')}秒` : `${secs}秒`
}
const formatSize = (bytes: number) => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`

export default function AudioCorpus() {
  const qc = useQueryClient()
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('all')
  const [editing, setEditing] = useState<AudioCorpusItem | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadName, setUploadName] = useState('')
  const [uploadTranscript, setUploadTranscript] = useState('')
  const [uploadExpected, setUploadExpected] = useState('')
  const [uploadCategory, setUploadCategory] = useState('general')
  const [uploadLanguage, setUploadLanguage] = useState('zh-CN')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  const { data: corpus = [], isLoading, isError } = useQuery({
    queryKey: ['audio-corpus'],
    queryFn: () => fetchAudioCorpus(),
  })
  const categories = useMemo(() => [...new Set(corpus.map(item => item.category || 'general'))].sort(), [corpus])
  const filtered = corpus.filter(item => {
    const q = keyword.trim().toLowerCase()
    const matchesText = !q || [item.name, item.original_filename, item.transcript, item.expected_result].some(value => value.toLowerCase().includes(q))
    return matchesText && (category === 'all' || item.category === category)
  })
  const totalDuration = corpus.reduce((sum, item) => sum + item.duration_seconds, 0)

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error('请先选择音频文件')
      return uploadAudioCorpus(selectedFile, {
        name: uploadName.trim() || selectedFile.name,
        transcript: uploadTranscript,
        expected_result: uploadExpected,
        category: uploadCategory,
        language: uploadLanguage,
      })
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['audio-corpus'] })
      setSelectedFile(null); setUploadName(''); setUploadTranscript(''); setUploadExpected('')
      setUploadCategory('general'); setUploadLanguage('zh-CN')
      setUploading(false)
    },
    onError: (error: any) => alert(error?.response?.data?.detail || error?.message || '上传失败'),
  })
  const updateMutation = useMutation({
    mutationFn: (body: Pick<AudioCorpusItem, 'name' | 'transcript' | 'expected_result' | 'category' | 'language'>) => {
      if (!editing) throw new Error('未选择语料')
      return updateAudioCorpus(editing.id, body)
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['audio-corpus'] })
      setEditing(null)
    },
    onError: (error: any) => alert(error?.response?.data?.detail || error?.message || '保存失败'),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteAudioCorpus,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['audio-corpus'] })
      if (previewId) stopPreview()
    },
    onError: (error: any) => alert(error?.response?.data?.detail || error?.message || '删除失败'),
  })

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])
  function stopPreview() {
    setPreviewId(null)
    setPreviewUrl(current => { if (current) URL.revokeObjectURL(current); return '' })
  }
  async function togglePreview(item: AudioCorpusItem) {
    if (previewId === item.id) { stopPreview(); return }
    try {
      const blob = await fetchAudioCorpusFile(item.id)
      setPreviewUrl(current => { if (current) URL.revokeObjectURL(current); return URL.createObjectURL(blob) })
      setPreviewId(item.id)
    } catch (error: any) {
      alert(error?.response?.data?.detail || '音频预览加载失败')
    }
  }
  function onFileChange(file?: File) {
    if (!file) return
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['wav', 'mp3', 'flac', 'ogg'].includes(ext || '')) { alert('仅支持 WAV、MP3、FLAC、OGG 格式'); return }
    if (file.size > 50 * 1024 * 1024) { alert('单个音频文件不能超过 50 MB'); return }
    setSelectedFile(file)
    if (!uploadName) setUploadName(file.name.replace(/\.[^.]+$/, ''))
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-6 py-6 text-white shadow-[0_18px_45px_rgba(37,99,235,0.16)]">
        <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div><div className="mb-2 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-blue-50">测试素材管理</div>
            <h1 className="text-xl font-bold tracking-tight">音频语料库</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-50/90">统一管理语音测试素材，维护语音文本与预期响应，并在执行计划中重复引用。</p>
          </div>
          <button onClick={() => setUploading(value => !value)} className="rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-blue-700 shadow-sm hover:bg-blue-50">{uploading ? '收起上传' : '＋ 上传音频语料'}</button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4"><div className="text-xs text-slate-500">语料总数</div><div className="mt-2 text-2xl font-semibold text-slate-800">{corpus.length}<span className="ml-1 text-xs font-normal text-slate-400">条</span></div></div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4"><div className="text-xs text-slate-500">音频总时长</div><div className="mt-2 text-2xl font-semibold text-slate-800">{formatDuration(totalDuration)}</div></div>
        <div className="rounded-2xl border border-slate-200/80 bg-white p-4"><div className="text-xs text-slate-500">已被执行计划引用</div><div className="mt-2 text-2xl font-semibold text-slate-800">{corpus.filter(item => item.usage_count > 0).length}<span className="ml-1 text-xs font-normal text-slate-400">条</span></div></div>
      </div>

      {uploading && <section className="rounded-2xl border border-blue-100 bg-white p-5 shadow-sm">
        <div className="mb-4"><h2 className="text-sm font-semibold text-slate-800">添加音频语料</h2><p className="mt-1 text-xs text-slate-500">支持 WAV / MP3 / FLAC / OGG，单文件最大 50 MB。</p></div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2"><label className="mb-1.5 block text-xs font-medium text-slate-600">音频文件</label><label className="flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-blue-300 bg-blue-50/40 px-4 py-3 text-sm text-blue-700 hover:bg-blue-50"><span className="truncate">{selectedFile ? selectedFile.name : '点击选择音频文件'}</span><span className="ml-3 shrink-0 rounded-lg bg-white px-3 py-1.5 text-xs">选择文件</span><input type="file" accept=".wav,.mp3,.flac,.ogg,audio/*" className="hidden" onChange={e => { onFileChange(e.target.files?.[0]); e.currentTarget.value = '' }} /></label></div>
          <label className="text-xs font-medium text-slate-600">语料名称<input className={inputClass + ' mt-1.5'} value={uploadName} onChange={e => setUploadName(e.target.value)} placeholder="例如：东京天气查询" /></label>
          <label className="text-xs font-medium text-slate-600">分类<input className={inputClass + ' mt-1.5'} value={uploadCategory} onChange={e => setUploadCategory(e.target.value)} placeholder="general / AI生活 / AI学习" /></label>
          <label className="text-xs font-medium text-slate-600">语言<select className={inputClass + ' mt-1.5'} value={uploadLanguage} onChange={e => setUploadLanguage(e.target.value)}><option value="zh-CN">中文（普通话）</option><option value="en-US">英语</option><option value="ja-JP">日语</option><option value="yue-HK">粤语</option><option value="other">其他</option></select></label>
          <label className="text-xs font-medium text-slate-600 md:col-span-2">语音文本<textarea className={inputClass + ' mt-1.5'} rows={2} value={uploadTranscript} onChange={e => setUploadTranscript(e.target.value)} placeholder="填写音频实际说出的内容，便于检索和结果核对" /></label>
          <label className="text-xs font-medium text-slate-600 md:col-span-2">预期响应<textarea className={inputClass + ' mt-1.5'} rows={2} value={uploadExpected} onChange={e => setUploadExpected(e.target.value)} placeholder="填写助手应有的响应或行为" /></label>
        </div>
        <div className="mt-4 flex justify-end gap-2"><button onClick={() => setUploading(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button><button disabled={!selectedFile || uploadMutation.isPending} onClick={() => uploadMutation.mutate()} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{uploadMutation.isPending ? '上传并解析中…' : '确认上传'}</button></div>
      </section>}

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3"><input className="min-w-[220px] flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索语料名称、语音文本或预期响应…" />
          <select className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-700" value={category} onChange={e => setCategory(e.target.value)}><option value="all">全部分类</option>{categories.map(value => <option key={value} value={value}>{value}</option>)}</select>
          <button onClick={() => qc.invalidateQueries({ queryKey: ['audio-corpus'] })} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs text-slate-600 hover:bg-slate-50">刷新</button>
        </div>
        <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_8px_28px_rgba(31,75,135,0.04)]">
          <div className="hidden grid-cols-[minmax(180px,1.2fr)_minmax(150px,1fr)_100px_90px_90px_150px] gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-3 text-[11px] font-semibold text-slate-500 lg:grid"><span>语料信息</span><span>语音文本 / 预期响应</span><span>分类</span><span>时长 / 大小</span><span>引用次数</span><span className="text-right">操作</span></div>
          {isLoading && <div className="p-8 text-center text-sm text-slate-400">正在加载音频语料…</div>}
          {isError && <div className="p-8 text-center text-sm text-rose-600">音频语料加载失败，请刷新重试。</div>}
          {!isLoading && !isError && filtered.length === 0 && <div className="p-10 text-center"><div className="text-sm font-medium text-slate-600">{corpus.length ? '没有找到匹配的音频语料' : '音频语料库还是空的'}</div><p className="mt-1 text-xs text-slate-400">{corpus.length ? '调整搜索条件试试' : '上传第一条音频语料，之后即可在执行计划中重复使用'}</p></div>}
          {filtered.map(item => <div key={item.id} className="grid gap-3 border-b border-slate-100 px-4 py-4 last:border-0 lg:grid-cols-[minmax(180px,1.2fr)_minmax(150px,1fr)_100px_90px_90px_150px] lg:items-center">
            <div className="min-w-0"><div className="truncate text-sm font-semibold text-slate-800">{item.name}</div><div className="mt-1 truncate text-[11px] text-slate-400">{item.original_filename} · {item.format.toUpperCase()}</div></div>
            <div className="min-w-0 space-y-1 text-xs"><div className="truncate text-slate-600" title={item.transcript}>语音：{item.transcript || '未填写'}</div><div className="truncate text-slate-400" title={item.expected_result}>预期：{item.expected_result || '未填写'}</div></div>
            <div><span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600">{item.category}</span><div className="mt-1 text-[10px] text-slate-400">{item.language}</div></div>
            <div className="text-xs text-slate-600">{formatDuration(item.duration_seconds)}<div className="mt-1 text-[10px] text-slate-400">{formatSize(item.size_bytes)}</div></div>
            <div className="text-xs text-slate-600">{item.usage_count} 次</div>
            <div className="flex flex-wrap items-center gap-2 lg:justify-end"><button onClick={() => togglePreview(item)} className="rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-1.5 text-[11px] font-medium text-blue-700">{previewId === item.id ? '停止' : '试听'}</button><button onClick={() => setEditing(item)} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50">编辑</button><button disabled={item.usage_count > 0 || deleteMutation.isPending} onClick={() => { if (window.confirm(`确定删除「${item.name}」吗？删除后无法恢复。`)) deleteMutation.mutate(item.id) }} title={item.usage_count > 0 ? '该语料仍被执行计划引用，暂不可删除' : '删除语料'} className="rounded-lg border border-rose-100 px-2.5 py-1.5 text-[11px] text-rose-600 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40">删除</button></div>
            {previewId === item.id && previewUrl && <div className="lg:col-span-6"><audio controls autoPlay src={previewUrl} className="h-9 w-full" /></div>}
          </div>)}
        </div>
      </section>

      {editing && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true" aria-label="编辑音频语料"><div className="w-full max-w-2xl rounded-2xl bg-white p-5 shadow-2xl"><div className="mb-4 flex items-center justify-between"><div><h2 className="text-base font-semibold text-slate-800">编辑音频语料</h2><p className="mt-1 text-xs text-slate-400">{editing.original_filename}</p></div><button onClick={() => setEditing(null)} className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button></div>
        <form onSubmit={e => { e.preventDefault(); const form = new FormData(e.currentTarget); updateMutation.mutate({ name: String(form.get('name') || ''), transcript: String(form.get('transcript') || ''), expected_result: String(form.get('expected_result') || ''), category: String(form.get('category') || 'general'), language: String(form.get('language') || 'zh-CN') }) }} className="space-y-3">
          <label className="block text-xs font-medium text-slate-600">语料名称<input name="name" required defaultValue={editing.name} className={inputClass + ' mt-1.5'} /></label>
          <div className="grid gap-3 sm:grid-cols-2"><label className="block text-xs font-medium text-slate-600">分类<input name="category" defaultValue={editing.category} className={inputClass + ' mt-1.5'} /></label><label className="block text-xs font-medium text-slate-600">语言<select name="language" defaultValue={editing.language} className={inputClass + ' mt-1.5'}><option value="zh-CN">中文（普通话）</option><option value="en-US">英语</option><option value="ja-JP">日语</option><option value="yue-HK">粤语</option><option value="other">其他</option></select></label></div>
          <label className="block text-xs font-medium text-slate-600">语音文本<textarea name="transcript" rows={3} defaultValue={editing.transcript} className={inputClass + ' mt-1.5'} /></label>
          <label className="block text-xs font-medium text-slate-600">预期响应<textarea name="expected_result" rows={3} defaultValue={editing.expected_result} className={inputClass + ' mt-1.5'} /></label>
          <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={() => setEditing(null)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button><button disabled={updateMutation.isPending} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{updateMutation.isPending ? '保存中…' : '保存修改'}</button></div>
        </form></div></div>}
    </div>
  )
}
