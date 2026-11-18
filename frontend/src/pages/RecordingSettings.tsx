import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchSettings, saveSettings, Settings as SettingsData,
  fetchRecordingStorageStats, RecordingStorageStats,
} from '../lib/api'
import { useT } from '../lib/i18n'

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = value
  let i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function UsageCard({ title, value, detail }: { title: string; value: string; detail?: string }) {
  return <div className="rounded-xl border border-hairline bg-white p-4">
    <div className="text-xs text-ink-mute">{title}</div>
    <div className="mt-2 text-xl font-semibold text-ink">{value}</div>
    {detail && <div className="mt-1 text-xs text-ink-mute">{detail}</div>}
  </div>
}

export default function RecordingSettings() {
  const t = useT()
  const qc = useQueryClient()
  const { data: remote, isLoading } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const { data: storage, isLoading: storageLoading } = useQuery<RecordingStorageStats>({
    queryKey: ['recording-storage'], queryFn: fetchRecordingStorageStats,
  })
  const [form, setForm] = useState<Partial<SettingsData>>({})
  const [saved, setSaved] = useState(false)

  useEffect(() => { if (remote) setForm(remote) }, [remote])

  const saveMut = useMutation({
    mutationFn: () => saveSettings({
      recording_auto_enabled: form.recording_auto_enabled !== false,
      recording_storage_dir: (form.recording_storage_dir || '').trim(),
      recording_retention_days: Number(form.recording_retention_days || 30),
    }),
    onSuccess: data => {
      setForm(prev => ({ ...prev, ...data }))
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['recording-storage'] })
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2200)
    },
  })

  if (isLoading) return <p className="text-sm text-ink-mute">Loading…</p>

  const usedPercent = storage && storage.disk_total_bytes > 0
    ? Math.min(100, Math.round(storage.disk_used_bytes / storage.disk_total_bytes * 100)) : 0

  return <div className="max-w-4xl space-y-6">
    <div>
      <h1 className="text-2xl font-bold text-ink">{t('录制与存储', 'Recording & storage')}</h1>
      <p className="mt-1 text-sm text-ink-mute">{t('统一管理测试任务的视频录制、文件保存位置和存储空间。', 'Manage task video recording, file location and storage usage in one place.')}</p>
    </div>

    <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm space-y-5">
      <div>
        <h2 className="text-base font-semibold text-ink">{t('录制设置', 'Recording')}</h2>
        <p className="mt-1 text-xs text-ink-mute">
          {t('测试任务开始时自动录制，任务结束后自动保存带声音的测试视频。', 'Automatically record a test video with audio when a run starts, and save it when the run ends.')}
        </p>
      </div>

      <label className="flex items-center justify-between gap-4 rounded-lg border border-hairline bg-canvas-soft px-4 py-4">
        <span>
          <span className="block text-sm font-medium text-ink">{t('任务视频录制', 'Task video recording')}</span>
          <span className="mt-1 block text-xs text-ink-mute">
            {t('开启后，测试任务执行期间自动录制屏幕并采集声音。', 'When enabled, the screen and audio are recorded automatically while the test runs.')}
          </span>
        </span>
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={form.recording_auto_enabled !== false}
          onChange={e => setForm(prev => ({ ...prev, recording_auto_enabled: e.target.checked }))} />
      </label>

      <div>
        <label className="mb-1 block text-sm font-medium text-ink">{t('录像保留时间', 'Retention period')}</label>
        <select value={String(form.recording_retention_days ?? 30)}
          onChange={e => setForm(prev => ({ ...prev, recording_retention_days: Number(e.target.value) }))}
          className="w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm">
          {[7, 15, 30, 60, 90, 365].map(days => <option key={days} value={days}>{days} {t('天', 'days')}</option>)}
        </select>
      </div>

      <div className="flex items-center justify-end gap-3 border-t border-hairline pt-4">
        {saveMut.isError && <span className="text-xs text-red-600">{t('保存失败，请检查设置。', 'Save failed. Please check the settings.')}</span>}
        {saved && <span className="text-xs text-green-700">{t('已保存', 'Saved')}</span>}
        <button disabled={saveMut.isPending} onClick={() => saveMut.mutate()}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-deep disabled:opacity-50">
          {saveMut.isPending ? t('保存中…', 'Saving…') : t('保存设置', 'Save settings')}
        </button>
      </div>
    </div>

    <div className="rounded-xl border border-hairline bg-white p-5 shadow-sm space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink">{t('存储空间', 'Storage usage')}</h2>
          <p className="mt-1 text-xs text-ink-mute">{t('统计当前账号录像目录所在磁盘的实际占用。', 'Actual disk usage for the drive containing this account’s recording directory.')}</p>
        </div>
        <button onClick={() => qc.invalidateQueries({ queryKey: ['recording-storage'] })}
          className="rounded-md border border-hairline px-3 py-1.5 text-xs text-ink hover:bg-canvas-soft">{t('刷新', 'Refresh')}</button>
      </div>
      {storageLoading && <p className="text-sm text-ink-mute">Loading…</p>}
      {storage && <>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <UsageCard title={t('录像文件数', 'Recording files')} value={String(storage.recording_count)} />
          <UsageCard title={t('录像占用空间', 'Recording size')} value={formatBytes(storage.recording_bytes)} />
          <UsageCard title={t('磁盘剩余空间', 'Disk free space')} value={formatBytes(storage.disk_free_bytes)} detail={t('总容量', 'Total') + ': ' + formatBytes(storage.disk_total_bytes)} />
        </div>
        <div>
          <div className="mb-2 flex justify-between text-xs text-ink-mute">
            <span>{t('磁盘已用', 'Disk used')} · {formatBytes(storage.disk_used_bytes)}</span><span>{usedPercent}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-gray-100"><div className="h-full rounded-full bg-primary" style={{ width: usedPercent + '%' }} /></div>
        </div>
        <div className="rounded-lg bg-canvas-soft px-3 py-2 text-xs text-ink-mute break-all">
          {t('当前保存目录', 'Current directory')}: {storage.directory}
        </div>
      </>}
    </div>
  </div>
}
