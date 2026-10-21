import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

type Host = { id: string; name: string; online: boolean; prepared: boolean; forwarding: boolean; error?: string;
  devices: { serial: string; platform?: 'ios' | 'android' | 'harmony'; name: string; ready: boolean; online: boolean; status: string }[] }

export default function MacHostConnect() {
  const qc = useQueryClient()
  const [pairing, setPairing] = useState<{code: string; expires: number; hostIds: string[]} | null>(null)
  const [now, setNow] = useState(Date.now())
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const query = useQuery({ queryKey: ['ios-hosts'], queryFn: async () => (await api.get<Host[]>('/ios-host')).data,
    refetchInterval: 5000, retry: false })
  const desktop = useQuery({ queryKey: ['ios-host-desktop'], queryFn: async () =>
    (await api.get<{downloads: {architecture: string; path: string}[]}>('/ios-host/desktop')).data, retry: false })
  useEffect(() => {
    if (!pairing) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [pairing])
  useEffect(() => {
    if (pairing && query.data?.some(host => !pairing.hostIds.includes(host.id))) {
      setPairing(null); setMessage('Mac 已配对。请在下方连接设备。')
    }
  }, [query.data, pairing])
  const action = async (key: string, fn: () => Promise<void>) => {
    setPending(key); setError(''); setMessage('')
    try { await fn() } catch (e: any) { setError(e.response?.data?.detail || '操作失败，请检查网络后重试。') }
    finally { setPending('') }
  }
  const pair = () => action('pair', async () => {
    const { data } = await api.post('/ios-host/pairings')
    setNow(Date.now())
    setPairing({code: data.code, expires: Date.now() + data.expires_in * 1000, hostIds: query.data?.map(host => host.id) || []})
  })
  const command = pairing ? `.venv/bin/python ios_host.py --server '${window.location.origin}' --pair-code '${pairing.code}'` : ''
  const download = (path: string, filename: string) => action('download', async () => {
    const { data } = await api.get(path, { responseType: 'blob' })
    const url = URL.createObjectURL(data)
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
  return <section className="mt-3" aria-label="Mac 设备助手">
    <p className="text-sm">安装 SAB 设备助手，一次配对即可连接 Android、鸿蒙和 iOS 设备。无需打开终端或安装 Python。</p>
    <p className="text-xs text-ink-mute mt-1">需要 macOS 14 或更新版本。Android 使用 ADB，鸿蒙使用 HDC；iOS 额外需要 Xcode 和已签名 WDA。</p>
    <div className="flex flex-wrap gap-2 mt-3">
      {desktop.data?.downloads.map(item => <button key={item.architecture} className="border rounded px-3 py-2 text-sm" disabled={!!pending}
        onClick={() => download(item.path, `SAB-Device-Helper-macOS-${item.architecture}.zip`)}>下载 Mac 应用 · {item.architecture === 'arm64' ? 'Apple 芯片' : 'Intel'}</button>)}
      <button className="bg-primary text-white rounded px-3 py-2 text-sm disabled:opacity-50" disabled={!!pending} onClick={pair}>配对一台 Mac</button>
      <button className="border rounded px-3 py-2 text-sm" disabled={query.isFetching} onClick={() => query.refetch()}>刷新助手</button>
    </div>
    {desktop.isLoading && <p className="text-xs text-ink-mute mt-2">正在检查 Mac 应用安装包…</p>}
    {(desktop.isError || desktop.data?.downloads.length === 0) && <p className="text-xs text-ink-mute mt-2">当前服务器尚未提供 Mac 应用安装包，请联系管理员发布安装包。已安装应用可继续配对。</p>}
    {pairing && <div className="bg-canvas-cool rounded p-3 mt-3 text-sm">
      <p>在连接设备的 Mac 打开本地助手，核对平台地址后点击「配对并连接」。</p>
      {now < pairing.expires ? <>
        <a className="inline-block bg-primary text-white rounded px-3 py-2 mt-3" href={`sab-helper://pair?server=${encodeURIComponent(window.location.origin)}&code=${encodeURIComponent(pairing.code)}`}>打开本地助手配对</a>
        <button className="border rounded px-3 py-2 ml-2" disabled={!!pending} onClick={() => action('copy', async () => {
          await navigator.clipboard.writeText(pairing.code); setMessage('配对码已复制，请粘贴到本地助手')
        })}>复制配对码</button>
        <p className="text-xs text-ink-mute mt-2">一次有效，5 分钟过期，请勿分享。若应用没有打开，请先下载并启动应用。</p>
      </> : <p className="mt-2">配对码已过期，请重新生成。</p>}
      <details className="mt-3 text-xs text-ink-mute"><summary className="cursor-pointer">高级：使用命令行助手</summary>
        <p className="mt-2">下载源码后，在助手目录安装依赖，再执行配对命令：</p>
        <pre className="overflow-x-auto my-2">{'python3 -m venv .venv\n.venv/bin/pip install -r adb-bridge-requirements.txt'}</pre>
        <pre className="overflow-x-auto my-2">{now >= pairing.expires ? '配对命令已过期' : command}</pre>
      </details>
    </div>}
    <details className="mt-3 text-xs text-ink-mute"><summary className="cursor-pointer">命令行助手下载与说明</summary>
      <button className="border rounded px-3 py-1 mt-2" disabled={!!pending} onClick={() => download('/ios-host/bundle', 'sab-mac-helper.zip')}>下载命令行助手源码</button>
      <p className="mt-2">仅手动方式需要 Python 和 USB 转发工具；WDA 路径通过 --wda-derived-data 指定。</p>
    </details>
    {query.isLoading && <p className="text-sm mt-3">正在读取已配对助手…</p>}
    {query.isError && <p role="alert" className="text-sm text-red-600 mt-3">无法读取助手列表，请检查平台是否已部署新版服务。</p>}
    {query.data?.length === 0 && <p className="text-sm mt-3">尚未配对 Mac。远程服务器无法直接发现插在你电脑上的 USB 设备，请先配对助手。</p>}
    {query.data?.map(host => <div key={host.id} className="border rounded p-3 mt-3">
      <div className="flex items-center justify-between gap-3"><strong className="text-sm">{host.name} · {host.online ? '助手在线' : '助手离线'}</strong>
        <button className="text-xs text-red-600" disabled={!!pending} onClick={() => {
          if (confirm('解除该 Mac 的配对并断开其设备？以后需要重新配对。')) action(host.id, async () => {
            await api.delete(`/ios-host/${host.id}`); await query.refetch(); qc.invalidateQueries({queryKey: ['devices']})
          })
        }}>解除配对</button></div>
      {!host.online && <p className="text-sm mt-2">请在这台 Mac 上启动助手并保持运行。</p>}
      {host.online && (!host.prepared || !host.forwarding) && <p className="text-sm mt-2">首次准备未完成：{!host.prepared && 'iOS 尚需在本地助手中选择已签名 WDA；不影响 Android 和鸿蒙连接。'}{!host.forwarding && '请使用新版 Mac 应用，或为命令行助手安装 USB 转发工具。'}</p>}
      {host.error && <p role="alert" className="text-sm mt-2">{host.error}</p>}
      {host.online && !host.devices.length && !host.error && <p className="text-sm mt-2">未发现 USB 设备，请检查数据线、解锁设备并信任此 Mac。</p>}
      {host.devices.map(d => <div key={`${d.platform || 'ios'}:${d.serial}`} className="flex justify-between gap-3 items-center border-t mt-3 pt-3 text-sm">
        <div><strong>{(d.platform || 'ios').toUpperCase()} · {d.name}</strong><p className="text-xs text-ink-mute mt-1">{d.online ? '已连接' : d.status || (!d.ready ? '请解锁设备并确认调试授权' : '待连接')}</p></div>
        <button className="bg-primary text-white rounded px-4 py-2 disabled:opacity-50" disabled={!!pending || !d.ready || ((!d.platform || d.platform === 'ios') && (!host.prepared || !host.forwarding)) || d.online}
          onClick={() => action(`${d.platform || 'ios'}:${d.serial}`, async () => {
            await api.post(`/ios-host/${host.id}/connect`, {serial: d.serial, platform: d.platform || 'ios'})
            setMessage('已请求 Mac 启动连接，请保持设备解锁；画面上线后会显示已连接。')
            await query.refetch(); qc.invalidateQueries({queryKey: ['devices']})
          })}>{d.online ? '已连接' : pending === `${d.platform || 'ios'}:${d.serial}` ? '连接中…' : '连接设备'}</button>
      </div>)}
    </div>)}
    {message && <p role="status" className="text-sm mt-3">{message}</p>}
    {error && <p role="alert" className="text-sm text-red-600 mt-3">{error}</p>}
  </section>
}
