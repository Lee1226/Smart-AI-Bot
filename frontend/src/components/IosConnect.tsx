import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, Device } from '../lib/api'
import MacHostConnect from './MacHostConnect'

type UsbDevice = { serial: string; name: string; ready: boolean; status: string; device_id?: string }
export default function IosConnect({ devices }: { devices: Device[] }) {
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
  const [helper, setHelper] = useState(true)
  return <>
    {local && <div className="flex gap-2 mt-3 text-sm">
      <button className="border rounded px-3 py-1" aria-pressed={!helper} onClick={() => setHelper(false)}>iOS 本机直连（旧方式）</button>
      <button className="border rounded px-3 py-1" aria-pressed={helper} onClick={() => setHelper(true)}>通过已配对 Mac 连接</button>
    </div>}
    {helper ? <MacHostConnect /> : <LocalIosConnect devices={devices} />}
  </>
}

function LocalIosConnect({ devices }: { devices: Device[] }) {
  const qc = useQueryClient()
  const [error, setError] = useState('')
  const [pending, setPending] = useState('')
  const { data, isFetching, refetch, error: discoveryError } = useQuery({
    queryKey: ['ios-local'], retry: false, refetchInterval: 5000,
    queryFn: async () => (await api.get<{ devices: UsbDevice[]; prepared: boolean; forwarding: boolean }>('/ios-local')).data,
  })
  const connect = async (d: UsbDevice) => {
    setPending(d.serial); setError('')
    try {
      let stored: Record<string,string> = {}
      try { stored = JSON.parse(localStorage.getItem('ios-bridge-serials') || '{}') } catch { /* manual mapping unavailable */ }
      const existing = devices.find(x => x.connection_type === 'ios_bridge' && stored[x.id] === d.serial)
      const result = await api.post('/ios-local/connect', { serial: d.serial, device_id: existing?.id })
      localStorage.setItem('ios-bridge-serials', JSON.stringify({ ...stored, [result.data.device_id]: d.serial }))
      await refetch(); qc.invalidateQueries({ queryKey: ['devices'] })
    } catch (e: any) { setError(e.response?.data?.detail || '连接失败，请检查本机助手。') }
    finally { setPending('') }
  }
  return <section className="mt-3">
    <p className="text-sm text-ink-mute">插上 USB 并保持设备解锁，点击连接即可启动 WDA 和截图流。</p>
    <button className="border rounded px-3 py-1 text-sm mt-2" disabled={isFetching} onClick={() => refetch()}>{isFetching ? '正在发现设备…' : '刷新设备'}</button>
    {!data && <p className="text-sm mt-3">{(discoveryError as any)?.response?.data?.detail || '正在检查本机助手…'} 请查看下方「首次准备与手动连接」。</p>}
    {data && !data.devices.length && <p className="text-sm mt-3">未发现 USB 设备。请解锁设备、检查数据线，并确认「信任此电脑」。</p>}
    {data && (!data.prepared || !data.forwarding) && <p className="text-sm mt-3">首次准备尚未完成：{!data.prepared ? '需要签名编译 WDA。' : ''}{!data.forwarding ? '需要安装 USB 转发工具。' : ''}完成一次后即可在这里连接。</p>}
    {data?.devices.map(d => <div key={d.serial} className="flex items-center justify-between gap-3 border rounded p-3 mt-3">
      <div><strong className="text-sm">{d.name}</strong><p className="text-xs text-ink-mute mt-1">{!d.ready ? '请开启开发者模式并信任电脑' : d.status}</p></div>
      <button className="bg-primary text-white rounded px-4 py-2 text-sm disabled:opacity-50" disabled={!d.ready || !data.prepared || !data.forwarding || !!pending || d.status === '已连接' || d.status === '连接中'} onClick={() => connect(d)}>{pending === d.serial || d.status === '连接中' ? '连接中…' : d.status === '已连接' ? '已连接' : '连接'}</button>
    </div>)}
    {error && <p role="alert" className="text-red-600 text-sm mt-2">{error}</p>}
    <p className="text-xs text-ink-mute mt-3">首次仍需在设备确认开发者模式和开发者信任；支持截图、点击、滑动和输入，暂不支持视频流。</p>
  </section>
}
