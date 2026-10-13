import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createDevice, Device } from '../lib/api'
import { useT } from '../lib/i18n'

export default function AdbBridgeSetup({ devices, server, transport = 'adb' }: { devices: Device[]; server: string; transport?: 'adb' | 'hdc' }) {
  const harmony = transport === 'hdc'
  const tool = harmony ? 'HDC' : 'ADB'
  const kind = harmony ? 'hdc_bridge' : 'adb_bridge'
  const storageKey = `${transport}-bridge-serials`
  const t = useT()
  const qc = useQueryClient()
  const [serial, setSerial] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [serials, setSerials] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') } catch { return {} }
  })
  const bridges = devices.filter(d => d.connection_type === kind)
  const saveSerial = (id: string, value: string) => {
    setSerials(previous => {
      const next = { ...previous, [id]: value.trim() }
      localStorage.setItem(storageKey, JSON.stringify(next))
      return next
    })
  }
  const add = useMutation({
    mutationFn: () => createDevice(name.trim() || `${serial.trim()} · ${tool}`, kind),
    onSuccess: device => {
      saveSerial(device.id, serial)
      setSerial(''); setName(''); setError('')
      qc.invalidateQueries({ queryKey: ['devices'] })
    },
    onError: () => setError(t('创建设备失败，请重试。', 'Failed to create device. Please retry.')),
  })
  const download = () => {
    const configured = bridges.filter(d => serials[d.id])
    if (!configured.length) { setError(t('请先添加设备并填写序列号。', 'Add a device and enter its serial first.')); return }
    if (new Set(configured.map(d => serials[d.id])).size !== configured.length) {
      setError(t(`每台设备需要不同的 ${tool} 序列号。`, `Each device needs a unique ${tool} serial.`)); return
    }
    const blob = new Blob([JSON.stringify({ server, devices: configured.map(d => ({ serial: serials[d.id], token: d.token })) }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `sab-${transport}-bridge.json`; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    setError('')
  }
  return <details className="mb-6 border rounded-lg p-4 bg-white">
    <summary className="font-semibold cursor-pointer">{harmony ? t('鸿蒙本地模式 · HDC · 手机和模拟器', 'Harmony local mode · HDC · phones and emulators') : t('本机 ADB 桥接 · 多设备 · 与 Android Studio 共存', 'Local ADB bridge · multiple devices · shares Android Studio ADB')}</summary>
    <p className="text-sm text-ink-mute mt-3">{harmony ? t('在连接鸿蒙手机或模拟器的电脑上运行代理，无需安装辅助 App。支持截图刷新、UI 树、点击滑动、返回、主页和启动应用；AI 仍由 SAB 服务端执行。', 'Run the bridge on the Harmony device computer, without a helper app. Supports screenshots, UI tree, tap/swipe, back/home and app launch. SAB server runs AI.') : t('在连接手机的电脑上运行代理，设备可并发执行不同任务，并提供视频流和截图。无需停止本机 ADB。', 'Run the bridge on the computer connected to the phones. Devices can run different tasks concurrently and provide video or screenshots, sharing the existing ADB server.')}</p>
    <p className="text-sm mt-2">{harmony ? t('先用 hdc list targets 查看设备或模拟器标识，再逐台添加：', 'Run hdc list targets to find phones or emulators, then add each target:') : t('先用 adb devices -l 查看序列号，再逐台添加：', 'Run adb devices -l to find serials, then add each phone:')}</p>
    <div className="flex flex-wrap gap-2 mt-2">
      <input aria-label={`${tool} serial`} className="border rounded px-3 py-2 text-sm" placeholder={t(`${tool} 序列号`, `${tool} serial`)} value={serial} onChange={e => setSerial(e.target.value)} />
      <input aria-label={`${tool} bridge device name`} className="border rounded px-3 py-2 text-sm" placeholder={t('设备名称（可选）', 'Device name (optional)')} value={name} onChange={e => setName(e.target.value)} />
      <button className="bg-primary text-white rounded px-3 py-2 text-sm disabled:opacity-50" disabled={!serial.trim() || add.isPending || bridges.some(d => serials[d.id] === serial.trim())} onClick={() => add.mutate()}>{t('添加桥接设备', 'Add bridge device')}</button>
    </div>
    {bridges.map(d => <label key={d.id} className="flex flex-wrap items-center gap-2 text-sm mt-2"><span>{d.name}</span><input aria-label={`${tool} serial ${d.name}`} className="border rounded px-2 py-1" placeholder={`${tool} serial`} value={serials[d.id] || ''} onChange={e => saveSerial(d.id, e.target.value)} /></label>)}
    <button className="border border-primary text-primary rounded px-3 py-2 text-sm mt-3" onClick={download}>{t('下载桥接配置', 'Download bridge config')}</button>
    <p className="text-xs text-ink-mute mt-2">{t('配置含设备控制凭证，请仅保存在运行代理的电脑上。以下命令在项目根目录执行：', 'The config contains device credentials. Keep it on the bridge computer. Run these commands from the project root:')}</p>
    <pre className="text-xs overflow-x-auto bg-canvas-cool rounded p-3 mt-2">{`python3 -m venv .venv-bridge\n.venv-bridge/bin/pip install -r tools/adb-bridge-requirements.txt\n.venv-bridge/bin/python tools/${transport}_bridge.py --config ~/Downloads/sab-${transport}-bridge.json`}</pre>
    {harmony && <p className="text-xs text-ink-mute mt-2">{t('HDC 随 DevEco Studio / 鸿蒙 SDK 安装；找不到时用 --hdc 指定完整路径。暂不支持视频流、中文或清空输入、安装包和录制。', 'HDC ships with DevEco Studio / Harmony SDK. Use --hdc to specify its path. Video, Unicode/clear input, installation and recording are not supported yet.')}</p>}
    {error && <p role="alert" className="text-sm text-red-600 mt-2">{error}</p>}
  </details>
}
