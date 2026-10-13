import { useRef, useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { fetchSuites, fetchProjects, createSuite, uploadSuite, deleteSuite, setSuiteTag, importSuite, getToken, type Suite } from '../lib/api'
import AISuiteImport from '../components/AISuiteImport'

const PAGE_SIZE = 20

function TagEdit({ suite }: { suite: Suite }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(suite.tag || '')

  const tagMut = useMutation({
    mutationFn: (tag: string) => setSuiteTag(suite.id, tag),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })

  if (!editing) {
    return (
      <button
        onClick={() => { setValue(suite.tag || ''); setEditing(true) }}
        className={`text-xs px-2 py-0.5 rounded-full border ${
          suite.tag
            ? 'bg-primary-soft text-primary-deep border-transparent'
            : 'border-dashed border-gray-300 text-gray-400 hover:text-gray-600'
        }`}
      >
        {suite.tag || '+ 标签'}
      </button>
    )
  }

  return (
    <input
      autoFocus
      value={value}
      onChange={e => setValue(e.target.value)}
      onBlur={() => { setEditing(false); if (value !== (suite.tag || '')) tagMut.mutate(value) }}
      onKeyDown={e => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') { setValue(suite.tag || ''); setEditing(false) }
      }}
      placeholder="标签名，回车保存"
      className="text-xs px-2 py-0.5 rounded-full border border-primary bg-white focus:outline-none w-28"
    />
  )
}

export default function Suites() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [appPackage, setAppPackage] = useState('')
  const [projectId, setProjectId] = useState('')
  const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: fetchProjects })
  const [newTag, setNewTag] = useState('')
  const [createError, setCreateError] = useState('')
  const createMut = useMutation({
    mutationFn: () => createSuite({ name: name.trim(), app_package: projectId ? '' : appPackage.trim(), project_id: projectId, tag: newTag.trim() }),
    onSuccess: suite => {
      qc.invalidateQueries({ queryKey: ['suites'] })
      navigate(`/suites/${suite.id}`)
    },
    onError: () => setCreateError('创建失败，请检查名称后重试。'),
  })
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [tag, setTag] = useState('')
  const [page, setPage] = useState(1)

  // Debounce the search input
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedQ(q); setPage(1) }, 300)
    return () => clearTimeout(t)
  }, [q])

  const { data, isLoading } = useQuery({
    queryKey: ['suites', debouncedQ, tag, page],
    queryFn: () => fetchSuites({ q: debouncedQ, tag, page, page_size: PAGE_SIZE }),
  })

  const suites = data?.items ?? []
  const tags = data?.tags ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const uploadMut = useMutation({
    mutationFn: uploadSuite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`Upload failed: ${msg}`)
    },
  })

  const deleteMut = useMutation({
    mutationFn: deleteSuite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })

  const handleFile = (file: File) => uploadMut.mutate(file)

  const importJsonRef = useRef<HTMLInputElement>(null)
  const importMut = useMutation({
    mutationFn: importSuite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`导入失败：${msg}`)
    },
  })
  const handleImportFile = async (file: File) => {
    try {
      const data = JSON.parse(await file.text())
      importMut.mutate(data)
    } catch (e) {
      alert(`解析 JSON 失败：${e}`)
    }
  }

  const selectTag = (t: string) => { setTag(t); setPage(1) }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">用例套件</h1>
        <div className="flex gap-2 flex-wrap">
          <button className="bg-primary text-white px-4 py-1.5 rounded text-sm" onClick={() => { setCreating(true); setCreateError('') }}>+ 手动创建用例套件</button>
          <AISuiteImport />
          <input
            ref={fileRef}
            type="file"
            accept=".xmind,.md,.markdown"
            className="hidden"
            onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }}
          />
          <input
            ref={importJsonRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={e => { if (e.target.files?.[0]) handleImportFile(e.target.files[0]) }}
          />
          <button
            className="border border-gray-300 text-gray-700 px-4 py-1.5 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
            onClick={() => importJsonRef.current?.click()}
            disabled={importMut.isPending}
          >
            {importMut.isPending ? '导入中…' : '导入 JSON'}
          </button>
          <button
            className="bg-primary text-white px-4 py-1.5 rounded text-sm hover:bg-primary-deep disabled:opacity-50"
            onClick={() => fileRef.current?.click()}
            disabled={uploadMut.isPending}
          >
            {uploadMut.isPending ? 'Uploading…' : '+ 原样导入 .xmind / .md'}
          </button>
        </div>
      </div>

      {creating && <form className="bg-white border rounded-lg p-4 mb-4" onSubmit={e => { e.preventDefault(); if (name.trim()) createMut.mutate() }}>
        <h2 className="font-semibold mb-3">手动创建用例套件</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-sm">套件名称（必填）<input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} className="block w-full border rounded px-3 py-2 mt-1" /></label>
          <label className="text-sm">所属项目（可选）<select value={projectId} onChange={e => setProjectId(e.target.value)} className="block w-full border rounded px-3 py-2 mt-1">
            <option value="">未关联项目</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></label>
          <label className="text-sm">标签（可选）<input value={newTag} onChange={e => setNewTag(e.target.value)} className="block w-full border rounded px-3 py-2 mt-1" /></label>
        </div>
        {!projectId && <label className="block text-sm mt-3">旧版 Android 包名（可选）<input value={appPackage} onChange={e => setAppPackage(e.target.value)} placeholder="com.example.app" className="block border rounded px-3 py-2 mt-1" /></label>}
        <p className="text-sm text-gray-500 mt-3">创建后进入套件详情，可手动添加用例步骤和预期结果。</p>
        {createError && <p role="alert" className="text-sm text-red-600 mt-2">{createError}</p>}
        <div className="flex gap-2 mt-3">
          <button type="submit" disabled={!name.trim() || createMut.isPending} className="bg-primary text-white rounded px-4 py-2 text-sm disabled:opacity-50">{createMut.isPending ? '创建中…' : '创建并编辑'}</button>
          <button type="button" disabled={createMut.isPending} onClick={() => setCreating(false)} className="border rounded px-4 py-2 text-sm">取消</button>
        </div>
      </form>}

      {/* Search */}
      <input
        type="text"
        value={q}
        onChange={e => setQ(e.target.value)}
        placeholder="搜索套件名称…"
        className="w-full border rounded px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-primary"
      />

      {/* Tag grouping chips */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <button
          onClick={() => selectTag('')}
          className={`text-xs px-3 py-1 rounded-full border ${tag === '' ? 'bg-primary text-white border-transparent' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
        >
          全部 ({total})
        </button>
        {tags.map(t => (
          <button
            key={t}
            onClick={() => selectTag(t)}
            className={`text-xs px-3 py-1 rounded-full border ${tag === t ? 'bg-primary text-white border-transparent' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
          >
            {t}
          </button>
        ))}
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}

      {suites.length === 0 && !isLoading && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg">暂无用例套件</p>
          <p className="text-sm mt-1">手动创建用例套件、导入文件，或调整搜索与标签筛选。</p>
        </div>
      )}

      <div className="grid gap-3">
        {suites.map(s => (
          <div key={s.id} className="bg-white border rounded-lg p-4 flex items-center gap-4 shadow-sm">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <Link
                  to={`/suites/${s.id}`}
                  className="font-medium text-primary hover:underline truncate block"
                >
                  {s.name}
                </Link>
                <TagEdit suite={s} />
                {s.project_id && <span className="text-xs text-gray-500">项目：{projects.find(p => p.id === s.project_id)?.name || '不可用'}</span>}
              </div>
              <div className="text-xs text-gray-400 mt-0.5">
                {s.source_format} · {s.case_count} cases · {new Date(s.created_at).toLocaleString()}
              </div>
            </div>
            <Link
              to={`/suites/${s.id}`}
              className="text-sm px-3 py-1.5 bg-primary text-white rounded hover:bg-primary-deep"
            >
              Run
            </Link>
            <a
              href={`/api/suites/${s.id}/export?token=${encodeURIComponent(getToken() || '')}`}
              download
              className="text-sm px-3 py-1.5 border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
            >
              导出
            </a>
            <button
              className="text-sm px-3 py-1.5 border border-red-200 text-red-600 rounded hover:bg-red-50"
              onClick={() => {
                if (confirm(`Delete suite "${s.name}"?`)) deleteMut.mutate(s.id)
              }}
            >
              Delete
            </button>
          </div>
        ))}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-6">
          <button
            className="px-3 py-1 text-sm border rounded disabled:opacity-40"
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
          >
            ‹
          </button>
          <span className="text-sm text-gray-500">{page} / {totalPages}</span>
          <button
            className="px-3 py-1 text-sm border rounded disabled:opacity-40"
            disabled={page >= totalPages}
            onClick={() => setPage(p => p + 1)}
          >
            ›
          </button>
        </div>
      )}
    </div>
  )
}
