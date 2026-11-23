import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchRuns, compareRuns, Run, CompareOut } from '../lib/api'

const STATUS_COLOR: Record<string, string> = {
  pass: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  fail: 'bg-rose-50 text-rose-700 ring-rose-200',
  error: 'bg-amber-50 text-amber-700 ring-amber-200',
  skip: 'bg-slate-100 text-slate-600 ring-slate-200',
  pending: 'bg-sky-50 text-sky-700 ring-sky-200',
}

const statusBadge = (s: string | null) => {
  if (!s) return <span className="text-xs text-slate-300">—</span>
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${STATUS_COLOR[s] ?? 'bg-slate-100 text-slate-600 ring-slate-200'}`}>
      {{ pass: '通过', fail: '失败', error: '异常', skip: '跳过', pending: '等待中' }[s] ?? s}
    </span>
  )
}

export default function RunCompare() {
  const navigate = useNavigate()
  const [runA, setRunA] = useState('')
  const [runB, setRunB] = useState('')

  const { data: runs = [] } = useQuery({
    queryKey: ['runs'],
    queryFn: () => fetchRuns(),
  })

  const { data: comparison, isLoading } = useQuery<CompareOut>({
    queryKey: ['compare', runA, runB],
    queryFn: () => compareRuns(runA, runB),
    enabled: !!runA && !!runB && runA !== runB,
  })

  const doneRuns = runs.filter(r => r.status === 'done' || r.status === 'error' || r.status === 'cancelled')

  const runLabel = (r: Run) =>
    `${r.suite_name ?? r.suite_id.slice(0, 8)} — ${r.provider}/${r.model} — ${new Date(r.created_at).toLocaleDateString()} (${r.passed}P/${r.failed}F)`

  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <header className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] px-5 py-5 text-white shadow-[0_18px_45px_rgba(37,99,235,0.16)] sm:px-6">
        <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full border-[36px] border-white/10" />
        <div className="relative">
          <button className="mb-4 inline-flex items-center rounded-xl border border-white/25 bg-white/10 px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-white/20" onClick={() => navigate('/runs')}>
            ← 返回任务列表
          </button>
          <div className="mb-2 text-xs font-semibold text-blue-50">AI 终端 · 测试结果分析</div>
          <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">结果对比</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-50/90">对比两次测试运行的用例结果，快速识别能力提升、回归与变化。</p>
        </div>
      </header>

      {/* Selectors */}
      <section className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-700">基准运行（A）</label>
          <select
            className="w-full rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-3 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10"
            value={runA}
            onChange={e => setRunA(e.target.value)}
          >
            <option value="">选择一次测试运行…</option>
            {doneRuns.map(r => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-700">新版本运行（B）</label>
          <select
            className="w-full rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-3 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10"
            value={runB}
            onChange={e => setRunB(e.target.value)}
          >
            <option value="">选择一次测试运行…</option>
            {doneRuns.map(r => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </div>
      </section>

      {runA && runB && runA === runB && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">请选择两次不同的测试运行。</p>
      )}

      {isLoading && <div className="rounded-2xl border border-slate-200 bg-white px-5 py-10 text-center text-sm text-slate-500">正在加载对比结果…</div>}

      {comparison && (
        <div className="space-y-4">
          {/* Summary */}
          <section className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-5 shadow-[0_6px_20px_rgba(16,185,129,0.05)]">
              <div className="text-xs font-semibold text-emerald-700">改善用例</div><div className="mt-2 text-2xl font-bold tabular-nums text-emerald-700">{comparison.summary.improved}</div>
            </div>
            <div className="rounded-2xl border border-rose-200 bg-rose-50/70 p-5 shadow-[0_6px_20px_rgba(244,63,94,0.05)]">
              <div className="text-xs font-semibold text-rose-700">回归用例</div><div className="mt-2 text-2xl font-bold tabular-nums text-rose-700">{comparison.summary.regressed}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-5 shadow-[0_6px_20px_rgba(31,75,135,0.03)]">
              <div className="text-xs font-semibold text-slate-600">保持不变</div><div className="mt-2 text-2xl font-bold tabular-nums text-slate-700">{comparison.summary.unchanged}</div>
            </div>
          </section>

          {/* Run meta */}
          <section className="grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-slate-200/80 bg-white px-5 py-4 text-sm text-slate-600 shadow-[0_6px_22px_rgba(31,75,135,0.04)]">
              <span className="mr-2 inline-flex rounded-md bg-blue-50 px-2 py-1 text-xs font-bold text-blue-700">基准 A</span> {comparison.run_a.provider}/{comparison.run_a.model} — {comparison.run_a.passed}P/{comparison.run_a.failed}F/{comparison.run_a.total}T
            </div>
            <div className="rounded-2xl border border-slate-200/80 bg-white px-5 py-4 text-sm text-slate-600 shadow-[0_6px_22px_rgba(31,75,135,0.04)]">
              <span className="mr-2 inline-flex rounded-md bg-cyan-50 px-2 py-1 text-xs font-bold text-cyan-700">新版本 B</span> {comparison.run_b.provider}/{comparison.run_b.model} — {comparison.run_b.passed}P/{comparison.run_b.failed}F/{comparison.run_b.total}T
            </div>
          </section>

          {/* Comparison table */}
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-[0_10px_32px_rgba(31,75,135,0.06)]">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs font-semibold tracking-wide text-slate-500">
                  <th className="px-5 py-3">测试用例</th>
                  <th className="w-20 px-4 py-3 text-center">基准 A</th>
                  <th className="w-10 px-2 py-3 text-center"></th>
                  <th className="w-20 px-4 py-3 text-center">新版本 B</th>
                  <th className="w-20 px-4 py-3 text-center">步骤数</th>
                  <th className="px-5 py-3">变化情况</th>
                </tr>
              </thead>
              <tbody>
                {comparison.cases.map((c, i) => {
                  let change = ''
                  let changeColor = 'text-gray-400'
                  if (c.status_a && c.status_b) {
                    if (c.status_a !== 'pass' && c.status_b === 'pass') {
                      change = 'improved'
                      changeColor = 'text-ok font-medium'
                    } else if (c.status_a === 'pass' && c.status_b !== 'pass') {
                      change = 'regressed'
                      changeColor = 'text-red-600 font-medium'
                    } else if (c.status_a === c.status_b) {
                      change = 'same'
                    } else {
                      change = 'changed'
                      changeColor = 'text-yellow-600'
                    }
                  }
                  return (
                    <tr key={c.case_id} className={`border-t border-slate-100 transition hover:bg-blue-50/40 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/40'}`}>
                      <td className="px-5 py-3">
                        <div className="truncate font-mono text-[11px] text-slate-400">{c.path}</div>
                        <div className="mt-1 truncate text-xs leading-5 text-slate-600">{c.expected}</div>
                      </td>
                      <td className="px-4 py-3 text-center">{statusBadge(c.status_a)}</td>
                      <td className="px-2 py-3 text-center text-slate-300">&rarr;</td>
                      <td className="px-4 py-2 text-center">{statusBadge(c.status_b)}</td>
                      <td className="px-4 py-3 text-center text-xs tabular-nums text-slate-500">
                        {c.steps_a > 0 && <span>{c.steps_a}</span>}
                        {c.steps_a > 0 && c.steps_b > 0 && <span> / </span>}
                        {c.steps_b > 0 && <span>{c.steps_b}</span>}
                      </td>
                      <td className={`px-5 py-3 text-xs font-semibold ${changeColor}`}>{({ improved: '改善', regressed: '回归', same: '不变', changed: '状态变化' }[change] ?? '—')}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
