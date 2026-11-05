import { useEffect, useMemo, useState } from 'react'
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
    if (snapshot.data?.logs?.length) setCursor(snapshot.data.next)
  }, [snapshot.data?.next])
  useEffect(() => {
    if (settings) {
      setProvider(settings.default_provider || 'openai')
      setModel(settings.default_model || DEFAULT_MODEL[settings.default_provider || 'openai'] || '')
    }
  }, [settings])
  const logs = useMemo(() => snapshot.data?.logs || [], [snapshot.data])
  const busy = !!sessionId && ['queued', 'running'].includes(snapshot.data?.status || 'queued')
  const canStart = !!deviceId && !!goal.trim() && !!provider && !!model.trim() && !start.isPending && !busy
  const changeProvider = (value: string) => {
    setProvider(value)
    setModel(value === settings?.default_provider && settings?.default_model ? settings.default_model : DEFAULT_MODEL[value] || '')
  }

  return <div className="space-y-5">
    <div>
      <h1 className="text-2xl font-bold">调试控制台</h1>
      <p className="text-sm text-ink-mute mt-1">直接驱动已连接设备执行自然语言指令。调试过程不写入正式测试运行记录，并与正式任务共用设备独占锁。</p>
    </div>
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5 items-start">
      <section className="bg-white border rounded-lg p-5 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="block text-sm font-medium">目标设备
            <select className="mt-1 w-full border rounded px-3 py-2 bg-white" value={deviceId} onChange={e => setDeviceId(e.target.value)}>
              <option value="">请选择在线设备</option>
              {devices.map(d => <option key={d.id} value={d.id} disabled={d.status !== 'online'}>{d.name} · {d.status === 'online' ? '在线' : '离线'}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium">模型服务商
            <select className="mt-1 w-full border rounded px-3 py-2 bg-white" value={provider} onChange={e => changeProvider(e.target.value)}>
              {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium md:col-span-2">模型名称
            <input className="mt-1 w-full border rounded px-3 py-2" value={model} onChange={e => setModel(e.target.value)} list="debug-models" />
            <datalist id="debug-models">{(PROVIDER_MODELS[provider] || []).map(m => <option key={m} value={m} />)}</datalist>
            <span className="text-xs text-ink-mute font-normal">严格使用所选模型，不启用 fallback。</span>
          </label>
        </div>
        <label className="block text-sm font-medium">执行指令
          <textarea rows={4} className="mt-1 w-full border rounded px-3 py-2 text-sm resize-y" placeholder="例如：打开系统设置，进入 WLAN 页面，读取当前连接状态。" value={goal} onChange={e => setGoal(e.target.value)} />
        </label>
        <label className="block text-sm font-medium">预期结果（可选）
          <input className="mt-1 w-full border rounded px-3 py-2 text-sm" placeholder="例如：成功进入 WLAN 页面并说明连接状态" value={expected} onChange={e => setExpected(e.target.value)} />
        </label>
        <label className="block text-sm font-medium">最大执行步数：{maxSteps}
          <input type="range" min={1} max={60} value={maxSteps} onChange={e => setMaxSteps(Number(e.target.value))} className="block w-full mt-2" />
        </label>
        <div className="flex gap-2">
          <button disabled={!canStart} onClick={() => { setSessionId(''); setCursor(0); start.mutate({ device_id: deviceId, goal, expected: expected || '完成用户要求的操作，并说明执行结果', provider, model, max_steps: maxSteps }) }} className="px-5 py-2 rounded bg-primary text-white text-sm font-medium disabled:opacity-40">开始调试</button>
          <button disabled={!busy || stop.isPending} onClick={() => stop.mutate(sessionId)} className="px-5 py-2 rounded border text-sm disabled:opacity-40">停止任务</button>
          <button onClick={() => { setSessionId(''); setCursor(0) }} className="px-4 py-2 rounded border text-sm">清空日志视图</button>
        </div>
        {start.isPending && <p className="text-xs text-ink-mute">正在创建调试会话…</p>}
      </section>
      <LivePanel device={selectedDevice as Device | null} defaultMode="auto" storageKey="debug-live-mode" />
    </div>
    <section className="bg-[#111827] rounded-lg overflow-hidden text-slate-100">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
        <div className="text-sm font-semibold">执行日志</div>
        <div className="text-xs text-slate-400">{sessionId ? (snapshot.data?.status || '连接中') : '尚未启动'}</div>
      </div>
      <div className="p-4 min-h-64 max-h-[520px] overflow-auto font-mono text-xs leading-6 whitespace-pre-wrap break-words">
        {!sessionId && <span className="text-slate-500">等待调试任务启动…</span>}
        {logs.map((line, i) => <div key={cursor - logs.length + i} className="text-emerald-300">{line}</div>)}
        {snapshot.isError && <div className="text-red-300">日志读取失败，请检查网络或重新进入页面。</div>}
        {snapshot.data && !busy && <div className="text-sky-300 mt-2">会话状态：{snapshot.data.status}</div>}
      </div>
    </section>
  </div>
}
