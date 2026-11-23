import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchAudioCorpus, fetchAudioOutputDevices, uploadAudioCorpus,
  type AudioCorpusItem, type AudioPlaybackTurn,
} from '../lib/api'

const fieldClass = 'w-full min-w-0 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-500/10'

export default function AudioCorpusPicker({
  mode,
  turns,
  volumePercent,
  outputDeviceIndex,
  onModeChange,
  onTurnsChange,
  onVolumeChange,
  onOutputDeviceChange,
}: {
  mode: 'single' | 'conversation'
  turns: AudioPlaybackTurn[]
  volumePercent: number
  outputDeviceIndex: number | null
  onModeChange: (mode: 'single' | 'conversation') => void
  onTurnsChange: (turns: AudioPlaybackTurn[]) => void
  onVolumeChange: (value: number) => void
  onOutputDeviceChange: (value: number | null) => void
}) {
  const qc = useQueryClient()
  const [transcript, setTranscript] = useState('')
  const [expectedResult, setExpectedResult] = useState('')
  const corpusQuery = useQuery({ queryKey: ['audio-corpus'], queryFn: () => fetchAudioCorpus() })
  const devicesQuery = useQuery({ queryKey: ['audio-output-devices'], queryFn: fetchAudioOutputDevices })
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadAudioCorpus(file, { transcript, expected_result: expectedResult }),
    onSuccess: async item => {
      await qc.invalidateQueries({ queryKey: ['audio-corpus'] })
      setTranscript('')
      setExpectedResult('')
      const next = [...turns, { corpus_id: item.id, wait_after_ms: 12000 }]
      onTurnsChange(mode === 'single' ? next.slice(0, 1) : next)
    },
    onError: (error: unknown) => {
      const message = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(error)
      alert(`音频上传失败：${message}`)
    },
  })
  const corpus = corpusQuery.data || []
  const devices = devicesQuery.data || []

  const visibleTurns = turns.length ? turns : [{ corpus_id: '', wait_after_ms: 12000 }]
  const updateTurn = (index: number, patch: Partial<AudioPlaybackTurn>) => {
    onTurnsChange(visibleTurns.map((turn, i) => i === index ? { ...turn, ...patch } : turn))
  }
  const addTurn = () => onTurnsChange([...visibleTurns, { corpus_id: '', wait_after_ms: 12000 }])
  const removeTurn = (index: number) => onTurnsChange(visibleTurns.filter((_, i) => i !== index))
  const uploadFile = (file?: File) => {
    if (file) uploadMutation.mutate(file)
  }

  return (
    <div className="md:col-span-2 space-y-3 rounded-xl border border-violet-100 bg-violet-50/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-gray-800">音频语料播放</div>
          <p className="mt-0.5 text-[11px] text-gray-500">从账号音频语料库选择素材；多轮模式按顺序播放，每轮播放后按配置等待眼镜响应。</p>
        </div>
        <select className={fieldClass + ' max-w-[160px]'} value={mode} onChange={e => {
          const nextMode = e.target.value as 'single' | 'conversation'
          onModeChange(nextMode)
          if (nextMode === 'single' && visibleTurns.length > 1) onTurnsChange(visibleTurns.slice(0, 1))
        }}>
          <option value="single">单条语料</option>
          <option value="conversation">多轮对话</option>
        </select>
      </div>

      {visibleTurns.map((turn, index) => {
        const selected = corpus.find(item => item.id === turn.corpus_id)
        return (
          <div key={index} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-semibold text-gray-700">{mode === 'single' ? '播放语料' : `第 ${index + 1} 轮`}</span>
              {mode === 'conversation' && visibleTurns.length > 1 && <button type="button" className="text-xs text-red-600" onClick={() => removeTurn(index)}>移除</button>}
            </div>
            <select className={fieldClass} value={turn.corpus_id} onChange={e => updateTurn(index, { corpus_id: e.target.value })}>
              <option value="">选择音频语料…</option>
              {corpus.map(item => <option key={item.id} value={item.id}>{item.name} · {item.format.toUpperCase()} · {item.duration_seconds.toFixed(1)} 秒</option>)}
            </select>
            {selected?.transcript && <p className="mt-2 text-[11px] text-gray-500">语音文本：{selected.transcript}</p>}
            {selected?.expected_result && <p className="mt-1 text-[11px] text-gray-500">预期结果：{selected.expected_result}</p>}
            <div className="mt-2">
              <label className="mb-1 block text-[11px] text-gray-500">本轮播放后响应等待（毫秒）</label>
              <input type="number" min="0" max="120000" className={fieldClass} value={turn.wait_after_ms} onChange={e => updateTurn(index, { wait_after_ms: Math.max(0, Math.min(120000, Number(e.target.value) || 0)) })} />
            </div>
          </div>
        )
      })}

      {mode === 'conversation' && <button type="button" className="rounded-lg border border-dashed border-violet-300 px-3 py-2 text-xs text-violet-700 hover:bg-violet-50" onClick={addTurn}>＋ 添加下一轮语料</button>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[11px] font-semibold text-gray-600">播放设备</label>
          <select className={fieldClass} value={outputDeviceIndex === null ? '' : String(outputDeviceIndex)} onChange={e => onOutputDeviceChange(e.target.value === '' ? null : Number(e.target.value))}>
            <option value="">系统默认扬声器</option>
            {devices.map(device => <option key={device.index} value={device.index}>{device.name}（{device.channels} 声道）</option>)}
          </select>
          {devicesQuery.isError && <p className="mt-1 text-[10px] text-amber-700">暂时无法读取播放设备，将尝试使用系统默认扬声器。</p>}
        </div>
        <div>
          <label className="mb-1 block text-[11px] font-semibold text-gray-600">播放音量（{volumePercent}%）</label>
          <input type="range" min="0" max="100" step="5" className="w-full accent-violet-600" value={volumePercent} onChange={e => onVolumeChange(Number(e.target.value))} />
        </div>
      </div>
      <div className="space-y-2 rounded-lg border border-dashed border-slate-200 bg-white p-3">
        <div className="text-[11px] font-semibold text-gray-700">上传素材时可补充语料信息（可选）</div>
        <textarea rows={2} className={fieldClass} value={transcript} onChange={e => setTranscript(e.target.value)} placeholder="音频语音文本，例如：明天东京天气怎么样？" />
        <textarea rows={2} className={fieldClass} value={expectedResult} onChange={e => setExpectedResult(e.target.value)} placeholder="预期响应，例如：助手应返回明天的天气信息" />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-violet-100 pt-3">
        <span className="text-[10px] text-gray-500">{corpusQuery.isLoading ? '正在加载音频语料…' : `语料库共 ${corpus.length} 条`}</span>
        <label className="cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-gray-700 hover:bg-slate-50">
          {uploadMutation.isPending ? '上传中…' : '＋ 上传音频语料'}
          <input type="file" accept=".wav,.mp3,.flac,.ogg,audio/*" className="hidden" disabled={uploadMutation.isPending} onChange={e => { uploadFile(e.target.files?.[0]); e.currentTarget.value = '' }} />
        </label>
      </div>
      {corpusQuery.isError && <p className="text-[11px] text-red-600">音频语料库加载失败，请刷新后重试。</p>}
      {corpus.length === 0 && !corpusQuery.isLoading && <p className="text-[11px] text-amber-700">当前语料库为空，请先上传音频文件。</p>}
    </div>
  )
}
