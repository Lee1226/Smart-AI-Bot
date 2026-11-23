import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchKnowledge, addKnowledge, deleteKnowledge, KnowledgeNote } from '../lib/api'
import { useT } from '../lib/i18n'

function Tags({ items, color }: { items: string[]; color: string }) {
  if (!items?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {items.map((x, i) => (
        <span key={i} className={`text-xs px-1.5 py-0.5 rounded ${color}`}>{x}</span>
      ))}
    </div>
  )
}

function NoteCard({ note }: { note: KnowledgeNote }) {
  const t = useT()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const del = useMutation({
    mutationFn: () => deleteKnowledge(note.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['knowledge'] }),
  })
  return (
    <article className="group rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] transition hover:border-blue-200 hover:shadow-[0_12px_32px_rgba(37,99,235,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <button className="text-left flex-1 min-w-0" onClick={() => setOpen(o => !o)}>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-blue-50 text-xs text-blue-600">{open ? '−' : '+'}</span>
            {note.title}
          </div>
        </button>
        <button
          className="shrink-0 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
          onClick={() => { if (confirm(t('删除这条知识？', 'Delete this note?'))) del.mutate() }}
        >
          {t('删除', 'Delete')}
        </button>
      </div>
      {open && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <div className="whitespace-pre-wrap text-sm leading-7 text-slate-600">{note.body}</div>
          <Tags items={note.keywords} color="rounded-full bg-blue-50 px-2.5 py-1 text-blue-700" />
          <Tags items={note.aliases} color="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600" />
          <details className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">
            <summary className="cursor-pointer font-medium text-slate-600">{t('原始输入', 'Raw input')}</summary>
            <p className="mt-2 whitespace-pre-wrap leading-6 text-slate-500">{note.raw_input}</p>
          </details>
          <p className="text-xs text-slate-400">{new Date(note.created_at).toLocaleString()}</p>
        </div>
      )}
    </article>
  )
}

export default function Knowledge() {
  const t = useT()
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')

  const { data: notes = [], isLoading } = useQuery({
    queryKey: ['knowledge', query],
    queryFn: () => fetchKnowledge(query),
  })

  const addMut = useMutation({
    mutationFn: () => addKnowledge(text.trim()),
    onSuccess: () => {
      setText('')
      qc.invalidateQueries({ queryKey: ['knowledge'] })
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`${t('记录失败', 'Failed to save')}: ${msg}`)
    },
  })

  const canSubmit = text.trim().length > 0 && !addMut.isPending

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <header className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-7 py-7 text-white shadow-[0_18px_45px_rgba(37,99,235,0.16)] sm:px-9"><div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" /><div className="relative"><div className="mb-3 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-blue-50">AI 终端 · 知识沉淀</div><h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">{t('知识库', 'Knowledge')}</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-blue-50/90">
        {t(
          '用大白话说一条关于被测 App 的知识（黑话、功能、入口、坑点都行），AI 会整理后记录下来，方便日后查询。',
          'Describe a fact about the app under test in plain words (slang, a feature, a path, a pitfall). The AI tidies it up and files it so you can look it up later.',
        )}
      </p></div></header>

      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] sm:p-6"><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-slate-800">{t('记录一条新知识', 'Capture a knowledge note')}</h2><p className="mt-1 text-xs leading-5 text-slate-500">{t('用自然语言描述，AI 将帮助整理并保存。', 'Describe it naturally; AI will organize and save it.')}</p></div><span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">AI 整理</span></div><div className="grid gap-4 xl:grid-cols-2">
        <textarea
          rows={3}
          className="w-full resize-y rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 text-sm leading-6 text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10"
          placeholder={t('例如：本App里"金币"和"钻石"是两种不同货币', 'e.g. In this app, "coins" and "gems" are two different currencies')}
          value={text}
          onChange={e => setText(e.target.value)}
        />
        <div className="flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-slate-400">{text.trim().length} {t('个字符', 'characters')}</span>
          <button
            className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45"
            disabled={!canSubmit}
            onClick={() => addMut.mutate()}
          >
            {addMut.isPending ? t('整理中…', 'Organizing…') : t('记录', 'Save')}
          </button>
        </div>
      </div></section>

      <section className="space-y-4">
        <input
          className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10"
          placeholder={t('搜索知识库…', 'Search the knowledge base…')}
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold text-slate-800">{t('已沉淀知识', 'Knowledge notes')}</h2><p className="mt-1 text-xs text-slate-500">{t('搜索、查看知识正文及原始输入。', 'Search and inspect note content and original input.')}</p></div><span className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600">{notes.length} {t('条记录', 'notes')}</span></div>
        {isLoading ? (
          <p className="rounded-2xl border border-slate-200 bg-white px-5 py-10 text-center text-sm text-slate-500">{t('加载中…', 'Loading…')}</p>
        ) : notes.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-slate-300 bg-white px-5 py-12 text-center text-sm text-slate-400">
            {query ? t('没有匹配的知识。', 'No matching notes.') : t('还没有知识，先在上面记一条吧。', 'No notes yet — jot one down above.')}
          </p>
        ) : (
          <div className="space-y-3">
            {notes.map(n => <NoteCard key={n.id} note={n} />)}
          </div>
        )}
      </section>
    </div>
  )
}
