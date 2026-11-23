import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteRecording, fetchRecordings, getToken, RecordingItem } from '../lib/api'

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatDate(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function RecordingCard({ item, onDeleted }: { item: RecordingItem; onDeleted: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: () => deleteRecording(item.run_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recordings'] })
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      onDeleted()
    },
  })
  const token = getToken()
  const videoUrl = `${item.download_url}${token ? `?token=${encodeURIComponent(token)}` : ''}`

  return <article className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] transition-shadow hover:shadow-[0_12px_32px_rgba(31,75,135,0.09)]">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-ink">{item.task_name}</div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-mute">
          <span>设备：{item.device_name}</span>
          <span>录制开始：{formatDate(item.created_at)}</span>
          <span>文件：{item.file_name}</span>
          <span>{formatBytes(item.size_bytes)}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Link to={`/runs/${item.run_id}`} className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700">任务详情</Link>
        <a href={videoUrl} download={item.file_name} className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 transition hover:bg-blue-100">下载</a>
        <button disabled={remove.isPending} onClick={() => { if (confirm('只删除这段视频文件，不会删除任务和测试结果。确定继续吗？')) remove.mutate() }} className="rounded-xl border border-rose-200 px-3 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50 disabled:opacity-50">{remove.isPending ? '删除中…' : '删除视频'}</button>
      </div>
    </div>
    {remove.isError && <p role="alert" className="mt-2 text-xs text-rose-600">删除失败，请刷新后重试。</p>}
    <button onClick={() => setExpanded(v => !v)} className="mt-4 text-xs font-semibold text-blue-700 hover:text-blue-800 hover:underline">{expanded ? '收起预览' : '展开视频预览'}</button>
    {expanded && <div className="mt-3 overflow-hidden rounded-lg bg-black">
      <video key={videoUrl} src={videoUrl} controls playsInline preload="metadata" className="mx-auto max-h-[560px] w-full object-contain" />
    </div>}
  </article>
}

export default function Recordings() {
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['recordings', keyword.trim(), page, pageSize],
    queryFn: () => fetchRecordings({ q: keyword.trim() || undefined, page, page_size: pageSize }),
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  return <div className="space-y-6">
    <header className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] p-5 text-white shadow-[0_16px_40px_rgba(37,99,235,0.20)] sm:p-6">
      <div>
        <div className="text-xs font-semibold tracking-[0.18em] text-blue-100">AI 终端 · 视频资产管理</div>
        <h1 className="mt-2 text-xl font-bold tracking-tight text-white">视频记录</h1>
        <p className="mt-2 text-sm text-blue-100">集中查看、预览和下载测试任务生成的屏幕视频，管理视频记录与存储空间。</p>
      </div>
      <button onClick={() => void refetch()} className="rounded-xl border border-white/30 bg-white/15 px-4 py-2.5 text-sm font-medium text-white backdrop-blur transition-colors hover:bg-white/25">↻ 刷新列表</button>
    </header>

    <section className="grid gap-4 sm:grid-cols-3">
      <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)]"><div className="text-xs text-ink-mute">视频数量</div><div className="mt-2 text-xl font-semibold tabular-nums text-ink">{data?.total ?? 0}</div></div>
      <div className="rounded-xl border border-hairline bg-white p-4"><div className="text-xs text-ink-mute">占用空间</div><div className="mt-2 text-xl font-semibold tabular-nums text-ink">{formatBytes(data?.total_bytes ?? 0)}</div></div>
      <div className="rounded-xl border border-hairline bg-white p-4"><div className="text-xs text-ink-mute">存储目录</div><div className="mt-2 break-all text-xs font-mono text-ink">{data?.storage_dir ?? '加载中…'}</div></div>
    </section>

    <section className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_28px_rgba(31,75,135,0.05)]">
      <input value={keyword} onChange={e => { setKeyword(e.target.value); setPage(1) }} placeholder="搜索全部视频：任务名称、设备或运行 ID" className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-2.5 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-100" />
      <span className="text-xs text-ink-mute">当前页 {items.length} 条 / 共 {total} 条</span>
    </section>

    {isLoading && <div className="rounded-xl border border-hairline bg-white p-10 text-center text-sm text-ink-mute">正在读取视频记录…</div>}
    {isError && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">视频列表读取失败，请检查后端连接。</div>}
    {!isLoading && !isError && items.length === 0 && <div className="rounded-xl border border-dashed border-hairline bg-white p-12 text-center">
      <div className="text-3xl text-ink-faint">▣</div>
      <h2 className="mt-3 text-sm font-semibold text-ink">{keyword.trim() ? '没有匹配的视频' : '暂无视频记录'}</h2>
      <p className="mt-1 text-xs text-ink-mute">{keyword.trim() ? '请调整搜索关键词。' : '完成支持本机 ADB 录制的测试任务后，视频会显示在这里。'}</p>
      <Link to="/runs" className="mt-4 inline-block text-xs font-medium text-primary hover:underline">查看测试任务</Link>
    </div>}
    <section className="space-y-4">{items.map(item => <RecordingCard key={item.run_id} item={item} onDeleted={() => { void refetch() }} />)}</section>
    {total > 0 && <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-ink-mute">
      <label className="flex items-center gap-2">每页显示
        <select value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-700"><option value={10}>10 条</option><option value={20}>20 条</option><option value={50}>50 条</option><option value={100}>100 条</option></select>
      </label>
      <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600 transition hover:bg-slate-50 disabled:opacity-40">上一页</button>
      <span>第 {page} / {totalPages} 页</span>
      <button disabled={page >= Math.ceil((data?.total ?? 0) / pageSize)} onClick={() => setPage(p => p + 1)} className="rounded border border-hairline px-2 py-1 disabled:opacity-40">下一页</button>
    </div>}
    <p className="text-xs text-ink-mute">当前版本只列出后端本地 ADB 录制成功生成的 MP4 文件；未生成视频的任务不会显示。删除视频不会删除任务结果。</p>
  </div>
}
