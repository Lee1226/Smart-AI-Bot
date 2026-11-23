import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { fetchSuites, fetchAllNodes, fetchNodeContext } from '../lib/api'
import { useT } from '../lib/i18n'

const PAGE = 50

// Right-hand detail: the selected node's parent, children, and referrers.
function NodeContextPanel({ nodeId }: { nodeId: string }) {
  const navigate = useNavigate()
  const t = useT()
  const { data: ctx, isLoading } = useQuery({
    queryKey: ['node-context', nodeId],
    queryFn: () => fetchNodeContext(nodeId),
  })
  if (isLoading || !ctx) return <div className="p-4 text-sm text-gray-400">{t('加载中…', 'Loading…')}</div>
  return (
    <div className="p-4 space-y-4 text-sm">
      <div>
        <div className="text-xs text-gray-500 mb-1">{t('完整路径', 'Full path')} · {ctx.suite_name}</div>
        <div className="font-mono text-xs break-words">{ctx.path}</div>
      </div>

      <div>
        <div className="text-xs text-gray-500 mb-1">{t('上一级（父步骤）', 'Parent step')}</div>
        {ctx.parent
          ? <div className="px-2 py-1 rounded bg-gray-50">{ctx.parent.action}
              {ctx.parent.expected && <span className="text-blue-600 text-xs"> · {t('期望', 'Expected')}:{ctx.parent.expected}</span>}</div>
          : <div className="text-gray-400 text-xs">{t('（根步骤，无父）', '(Root step, no parent)')}</div>}
      </div>

      <div>
        <div className="text-xs text-gray-500 mb-1">{t('子步骤', 'Child steps')}（{ctx.children.length}）</div>
        {ctx.children.length === 0 && <div className="text-gray-400 text-xs">{t('（叶子，无子步骤）', '(Leaf, no child steps)')}</div>}
        {ctx.children.map(c => (
          <div key={c.node_id} className="px-2 py-1 rounded hover:bg-gray-50 border-t">
            {c.action}{c.expected && <span className="text-blue-600 text-xs"> · {t('期望', 'Expected')}:{c.expected}</span>}
          </div>
        ))}
      </div>

      {ctx.reuses && (
        <div>
          <div className="text-xs text-gray-500 mb-1">{t('复用自 / 源', 'Reused from / source')}</div>
          <button className="w-full text-left px-2 py-1 rounded hover:bg-gray-50 border text-xs"
                  onClick={() => navigate(`/suites/${ctx.reuses!.suite_id}`)}>
            <span className={`px-1.5 py-0.5 rounded mr-2 ${ctx.reuses.kind === 'link' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>
              {ctx.reuses.kind === 'link' ? t('活链接（改源会同步）', 'Live link (edits sync from source)') : t('快照拷贝（改源不影响）', 'Snapshot copy (source edits ignored)')}
            </span>
            <span className="font-mono">{ctx.reuses.path}</span>
            <span className="text-gray-400"> · {ctx.reuses.suite_name}</span>
          </button>
        </div>
      )}

      <div>
        <div className="text-xs text-gray-500 mb-1">{t('被复用 / 引用方', 'Reused by / referrers')}（{ctx.referrers.length}）</div>
        {ctx.referrers.length === 0 && <div className="text-gray-400 text-xs">{t('（还没有被复用）', '(Not reused yet)')}</div>}
        {ctx.referrers.map(r => (
          <button key={r.node_id} className="w-full text-left px-2 py-1 rounded hover:bg-gray-50 border-t text-xs"
                  onClick={() => navigate(`/suites/${r.suite_id}`)}>
            <span className={`px-1.5 py-0.5 rounded mr-2 ${r.kind === 'link' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>
              {r.kind === 'link' ? t('活链接', 'Live link') : t('快照拷贝', 'Snapshot copy')}
            </span>
            <span className="font-mono">{r.path}</span>
            <span className="text-gray-400"> · {r.suite_name}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Library() {
  const navigate = useNavigate()
  const t = useT()
  const [q, setQ] = useState('')
  const [suiteId, setSuiteId] = useState('')
  const [showDerived, setShowDerived] = useState(false)
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)

  const { data: suitesPage } = useQuery({ queryKey: ['suites'], queryFn: () => fetchSuites() })
  const suites = suitesPage?.items ?? []
  const { data, isFetching } = useQuery({
    queryKey: ['nodes-all', q, suiteId, showDerived, page],
    queryFn: () => fetchAllNodes({ q: q.trim(), suite_id: suiteId, include_derived: showDerived, offset: page * PAGE, limit: PAGE }),
    placeholderData: keepPreviousData,
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE))
  const reset = () => setPage(0)

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-7 py-8 text-white shadow-[0_18px_45px_rgba(37,99,235,0.18)] sm:px-9"><div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10"/><div className="relative"><div className="mb-3 inline-flex rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-blue-50">AI 终端 · 用例管理</div><h1 className="text-2xl font-bold tracking-tight sm:text-3xl">步骤复用库</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-blue-50/90">检索全库步骤节点，查看上下游关系、复用来源与引用情况，快速定位可复用操作。</p></div></div>
      <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_6px_22px_rgba(31,75,135,0.04)]">{t('用例库', 'Case Library')}</h1>
      <p className="text-sm leading-6 text-slate-500">{t('全库步骤节点 · 选中看上级/子级/引用方 · 编辑流程会同步到所有「活链接」引用方', 'All step nodes · select to see parent/children/referrers · editing a flow syncs to all "live link" referrers')}</p>

      <div className="flex flex-wrap items-center gap-3">
        <input
          className="min-w-[220px] flex-1 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10"
          placeholder={t('搜索行为或期望，如「登录」「语音」「点赞」', 'Search action or expected, e.g. "login", "voice", "like"')}
          value={q}
          onChange={e => { setQ(e.target.value); reset() }}
        />
        <select className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10" value={suiteId}
                onChange={e => { setSuiteId(e.target.value); reset() }}>
          <option value="">{t('全部套件', 'All suites')}</option>
          {suites.map(s => <option key={s.id} value={s.id}>{(s.name || '').replace(/\.xmind$/i, '')}</option>)}
        </select>
        <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-3 text-xs font-medium text-slate-600 whitespace-nowrap cursor-pointer">
          <input type="checkbox" checked={showDerived} onChange={e => { setShowDerived(e.target.checked); reset() }} />
          {t('显示拷贝/链接副本', 'Show copies/links')}
        </label>
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        {/* Left: flat paginated node list */}
        <div className="flex-1 min-w-0">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-500">
            <span>{total} {t('个节点', 'nodes')}{isFetching ? t(' · 加载中…', ' · Loading…') : ''}</span>
            <span className="flex items-center gap-2">
              <button disabled={page <= 0} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-600 transition hover:bg-blue-50 hover:text-blue-700 disabled:opacity-40"
                      onClick={() => setPage(p => Math.max(0, p - 1))}>‹ {t('上一页', 'Prev')}</button>
              <span>{page + 1} / {pages}</span>
              <button disabled={page >= pages - 1} className="px-2 py-0.5 border rounded disabled:opacity-40"
                      onClick={() => setPage(p => Math.min(pages - 1, p + 1))}>{t('下一页', 'Next')} ›</button>
            </span>
          </div>
          <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_8px_28px_rgba(31,75,135,0.05)]">
            {items.length === 0 && <div className="px-4 py-6 text-sm text-gray-400 text-center">{t('没有匹配的节点。', 'No matching nodes.')}</div>}
            {items.map(it => (
              <button key={it.node_id}
                      className={`w-full border-t border-slate-100 px-4 py-3 text-left transition hover:bg-blue-50/60 ${selected === it.node_id ? 'bg-blue-50 ring-1 ring-inset ring-blue-200' : ''}`}
                      onClick={() => setSelected(it.node_id)}>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">
                    {it.is_link && <span className="text-blue-600">🔗 </span>}{it.action || t('（空步骤）', '(Empty step)')}
                  </span>
                  {it.expected && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 flex-shrink-0">{t('期望', 'Expected')}:{it.expected}</span>}
                  {it.reuse_count > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 flex-shrink-0">🔁 {it.reuse_count}</span>}
                </div>
                <div className="text-[11px] text-gray-400 font-mono truncate">{it.path} · {it.suite_name}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Right: selected node context */}
        <div className="min-w-0 overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_8px_28px_rgba(31,75,135,0.05)] xl:sticky xl:top-5">
          {selected ? (
            <>
              <div className="flex items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/70 px-5 py-4">
                <span className="text-sm font-semibold">{t('节点详情', 'Node details')}</span>
                <button className="text-xs text-primary" onClick={() => navigate(`/suites/${items.find(i => i.node_id === selected)?.suite_id}`)}>
                  {t('去所在套件编辑', 'Edit in its suite')} →
                </button>
              </div>
              <NodeContextPanel nodeId={selected} />
            </>
          ) : (
            <div className="p-8 text-center text-sm leading-6 text-slate-400">{t('从左侧选一个节点查看它的上级 / 子级 / 引用方。', 'Select a node on the left to see its parent / children / referrers.')}</div>
          )}
        </div>
      </div>
    </div>
  )
}
