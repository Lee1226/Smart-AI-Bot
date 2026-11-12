import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Device, fetchDevices, fetchSettings, startDebugSession, fetchDebugSession, stopDebugSession } from '../lib/api'
import { PROVIDERS, PROVIDER_MODELS, DEFAULT_MODEL } from '../lib/providerConfig'
import LivePanel from '../components/LivePanel'

export default function DebugConsole() {
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const [deviceId, setDeviceId] = useState('')
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o')
  const [goal, setGoal] = useState('')
  const [expected, setExpected] = useState('')
  const [maxSteps, setMaxSteps] = useState(20)
  const [sessionId, setSessionId] = useState('')
  const [cursor, setCursor] = useState(0)
  const [logLines, setLogLines] = useState<string[]>([])
  const selectedDevice = devices.find(d => d.id === deviceId) || null
  const snapshot = useQuery({
    queryKey: ['debug-session', sessionId, cursor],
    queryFn: () => fetchDebugSession(sessionId, cursor),
    enabled: !!sessionId,
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'done' || status === 'error' || status === 'cancelled' ? false : 1000
    },
  })
  const start = useMutation({
    mutationFn: startDebugSession,
    onSuccess: result => { setSessionId(result.session_id); setCursor(0) },
    onError: (e: any) => alert(e?.response?.data?.detail || String(e)),
  })
  const stop = useMutation({
    mutationFn: stopDebugSession,
  })
  useEffect(() => {
    if (snapshot.data?.logs?.length) setLogLines(previous => [...previous, ...snapshot.data!.logs])
    if (snapshot.data) setCursor(snapshot.data.next)
  }, [snapshot.data?.next])
  useEffect(() => {
    if (settings) {
      setProvider(settings.default_provider || 'openai')
      setModel(settings.default_model || DEFAULT_MODEL[settings.default_provider || 'openai'] || '')
    }
  }, [settings])
  const logs = logLines
  const busy = !!sessionId && ['queued', 'running'].includes(snapshot.data?.status || 'queued')
  const canStart = !!deviceId && !!goal.trim() && !!provider && !!model.trim() && !start.isPending && !busy
  const changeProvider = (value: string) => {
    setProvider(value)
    setModel(value === settings?.default_provider && settings?.default_model ? settings.default_model : DEFAULT_MODEL[value] || '')
  }

  const statusLabel = snapshot.data?.status === 'running' ? '正在执行' : snapshot.data?.status === 'queued' ? '等待设备' : snapshot.data?.status === 'done' ? '执行完成' : snapshot.data?.status === 'error' ? '执行异常' : snapshot.data?.status === 'cancelled' ? '已停止' : '未启动'
  const statusTone = busy ? 'bg-sky-50 text-sky-700' : snapshot.data?.status === 'error' ? 'bg-rose-50 text-rose-700' : snapshot.data?.status === 'done' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'

  return <div className="space-y-5">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">AGENT INTERACTIVE DEBUGGING</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">调试控制台</h1>
        <p className="mt-1 text-sm text-ink-mute">实时观察设备画面与 Agent 执行过程。调试会话独立于正式测试记录，并与正式任务共用设备独占锁。</p>
      </div>
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-3 py-1.5 text-xs font-medium ${statusTone}`}>{statusLabel}</span>
        <span className="text-xs text-ink-mute">{selectedDevice ? selectedDevice.name : '未选择设备'}</span>
      </div>
    </header>

    <div className="grid items-start gap-5 2xl:grid-cols-[minmax(360px,0.9fr)_minmax(520px,1.35fr)]">
      <section className="min-w-0 space-y-3">
        <div className="flex items-center justify-between px-1">
          <div><h2 className="text-sm font-semibold">设备实时画面</h2><p className="mt-0.5 text-xs text-ink-mute">支持实时视频与鼠标远程操作</p></div>
          {selectedDevice && <span className={`text-xs ${selectedDevice.status === 'online' ? 'text-emerald-700' : 'text-rose-600'}`}>{selectedDevice.status === 'online' ? '● 在线' : '● 离线'}</span>}
        </div>
        <LivePanel device={selectedDevice as Device | null} defaultMode="on" storageKey="debug-live-mode" />
        <div className="rounded-xl border border-hairline bg-white p-4">
          <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">本次调试配置</h3><span className="text-[11px] text-ink-mute">独立会话</span></div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-lg bg-canvas-soft p-3"><div className="text-ink-mute">设备类型</div><div className="mt-1 font-medium text-ink">{selectedDevice?.platform === 'harmony' ? 'HarmonyOS' : selectedDevice?.platform === 'ios' ? 'iOS' : 'Android'}</div></div>
            <div className="rounded-lg bg-canvas-soft p-3"><div className="text-ink-mute">连接方式</div><div className="mt-1 font-medium text-ink">{selectedDevice?.connection_type || '—'}</div></div>
          </div>
        </div>
      </section>

      <section className="min-w-0 space-y-4">
        <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">Agent 任务配置</h2><p className="mt-1 text-xs text-ink-mute">选择目标设备与模型，输入自然语言操作目标</p></div><span className="rounded-md bg-primary-soft px-2 py-1 text-[11px] font-medium text-primary-deep">自然语言驱动</span></div>
          <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="block text-xs font-medium text-ink-mute">目标设备
              <select className="mt-1.5 w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink" value={deviceId} onChange={e => setDeviceId(e.target.value)}>
                <option value="">请选择在线设备</option>
                {devices.map(d => <option key={d.id} value={d.id} disabled={d.status !== 'online'}>{d.name} · {d.status === 'online' ? '在线' : '离线'}</option>)}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink-mute">模型服务商
              <select className="mt-1.5 w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink" value={provider} onChange={e => changeProvider(e.target.value)}>
                {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label className="block text-xs font-medium text-ink-mute md:col-span-2">模型名称
              <input className="mt-1.5 w-full rounded-lg border border-hairline px-3 py-2.5 text-sm" value={model} onChange={e => setModel(e.target.value)} list="debug-models" />
              <datalist id="debug-models">{(PROVIDER_MODELS[provider] || []).map(m => <option key={m} value={m} />)}</datalist>
              <span className="mt-1 block text-[11px] font-normal text-ink-mute">严格使用所选模型，不启用 fallback。</span>
            </label>
          </div>
          <label className="mt-4 block text-xs font-medium text-ink-mute">执行指令
            <textarea rows={4} className="mt-1.5 w-full resize-y rounded-lg border border-hairline px-3 py-3 text-sm text-ink outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" placeholder="例如：打开系统设置，进入 WLAN 页面，读取当前连接状态。" value={goal} onChange={e => setGoal(e.target.value)} />
          </label>
          <label className="mt-3 block text-xs font-medium text-ink-mute">预期结果 <span className="font-normal">（可选）</span>
            <input className="mt-1.5 w-full rounded-lg border border-hairline px-3 py-2.5 text-sm" placeholder="例如：成功进入 WLAN 页面并说明连接状态" value={expected} onChange={e => setExpected(e.target.value)} />
          </label>
          <div className="mt-4 rounded-lg bg-canvas-soft p-3">
            <div className="flex items-center justify-between text-xs"><span className="font-medium text-ink-mute">最大执行步数</span><span className="font-semibold tabular-nums text-ink">{maxSteps} 步</span></div>
            <input type="range" min={1} max={60} value={maxSteps} onChange={e => setMaxSteps(Number(e.target.value))} className="mt-2 block w-full accent-primary" />
            <div className="mt-1 flex justify-between text-[10px] text-ink-faint"><span>1 步</span><span>60 步</span></div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2 border-t border-hairline pt-4">
            <button disabled={!canStart} onClick={() => { setSessionId(''); setCursor(0); setLogLines([]); start.mutate({ device_id: deviceId, goal, expected: expected || '完成用户要求的操作，并说明执行结果', provider, model, max_steps: maxSteps }) }} className="rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-deep disabled:cursor-not-allowed disabled:opacity-40">{start.isPending ? '正在启动…' : '▶ 开始调试'}</button>
            <button disabled={!busy || stop.isPending} onClick={() => stop.mutate(sessionId)} className="rounded-lg border border-rose-200 px-4 py-2.5 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40">■ 停止任务</button>
            <button onClick={() => { setSessionId(''); setCursor(0); setLogLines([]) }} className="rounded-lg border border-hairline px-4 py-2.5 text-sm text-ink-mute hover:bg-canvas-cool">清空日志</button>
          </div>
          {start.isPending && <p className="mt-2 text-xs text-ink-mute">正在创建调试会话…</p>}
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-700 bg-[#0b1220] shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-700 px-4 py-3">
            <div><h2 className="text-sm font-semibold text-slate-100">Agent 执行过程</h2><p className="mt-0.5 text-[11px] text-slate-400">实时日志 · 动作反馈 · 设备状态</p></div>
            <div className="flex items-center gap-2 text-xs text-slate-400"><span className={`h-2 w-2 rounded-full ${busy ? 'animate-pulse bg-sky-400' : 'bg-slate-500'}`} />{sessionId ? (snapshot.data?.status || '连接中') : '等待启动'}</div>
          </div>
          <div className="max-h-[560px] min-h-[280px] overflow-auto p-4 font-mono text-xs leading-6">
            {!sessionId && <div className="flex min-h-[240px] flex-col items-center justify-center text-center text-slate-500"><div className="text-3xl">⌘</div><div className="mt-3 text-sm text-slate-300">等待调试任务启动</div><div className="mt-1 text-[11px]">配置设备与模型后，输入指令开始执行</div></div>}
            {logs.map((line, i) => <div key={cursor - logs.length + i} className="flex gap-3 border-b border-slate-800/70 py-1.5"><span className="shrink-0 select-none text-slate-600">{String(i + 1).padStart(3, '0')}</span><span className="break-words text-emerald-300">{line}</span></div>)}
            {snapshot.isError && <div className="mt-3 text-rose-300">日志读取失败，请检查网络或重新进入页面。</div>}
            {snapshot.data && !busy && <div className="mt-4 rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sky-300">会话结束 · 状态：{snapshot.data.status}</div>}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-700 bg-slate-900/70 px-4 py-2.5 text-[11px] text-slate-400"><span>Session: {sessionId || '—'}</span><span>{logs.length} 条日志</span></div>
        </div>
      </section>
    </div>
  </div>}
