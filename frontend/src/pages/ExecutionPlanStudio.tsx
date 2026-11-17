import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchDevices, fetchSuites } from '../lib/api'
import ExecutionPlanManager from '../components/ExecutionPlanManager'
import { useT } from '../lib/i18n'

export default function ExecutionPlanStudio() {
  const t = useT()
  const [searchParams, setSearchParams] = useSearchParams()
  const [platform, setPlatform] = useState('android')
  const [deviceId, setDeviceId] = useState('')
  const requestedSuiteId = searchParams.get('suite') || ''
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const onlineDevices = devices.filter(device => device.status === 'online')
  const selectedDevice = onlineDevices.find(device => device.id === deviceId)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['execution-plan-studio-suites'],
    queryFn: () => fetchSuites({ page: 1, page_size: 100 }),
  })
  const recordedSuites = useMemo(
    () => (data?.items || []).filter(suite => suite.source_format === 'recorded'),
    [data],
  )
  const selectedSuite = recordedSuites.find(suite => suite.id === requestedSuiteId) || recordedSuites[0]

  useEffect(() => {
    if (selectedSuite && selectedSuite.id !== requestedSuiteId) {
      setSearchParams({ suite: selectedSuite.id }, { replace: true })
    }
  }, [selectedSuite, requestedSuiteId, setSearchParams])

  const selectSuite = (id: string) => setSearchParams(id ? { suite: id } : {})

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs text-ink-faint">
            <Link to="/suites" className="hover:text-primary">{t('用例中心', 'Cases')}</Link>
            <span>/</span>
            <span>{t('执行计划调试', 'Execution plan studio')}</span>
          </div>
          <h1 className="text-2xl font-bold text-ink">{t('执行计划调试工作台', 'Execution Plan Studio')}</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink-mute">
            {t('集中管理录制用例的执行计划，检查每一步的执行器、定位条件和验证规则。保存会创建新版本，不覆盖历史计划。', 'Manage recorded-case plans, inspect executors, locators and verification rules. Saving creates a new version without overwriting history.')}
          </p>
        </div>
        <Link to="/recorder" className="inline-flex items-center gap-2 rounded-lg border border-hairline bg-white px-3 py-2 text-sm font-medium text-ink-secondary shadow-sm hover:bg-canvas-cool">
          <span aria-hidden="true">●</span>{t('打开录制工作台', 'Open recorder')}
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
          <div className="text-xs text-ink-faint">{t('计划管理', 'Plan management')}</div>
          <div className="mt-1 text-lg font-semibold text-ink">{t('版本化编辑', 'Versioned editing')}</div>
          <p className="mt-1 text-xs leading-5 text-ink-mute">{t('草稿独立保存，历史版本可回看。', 'Drafts are saved as new versions; history remains available.')}</p>
        </div>
        <div className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
          <div className="text-xs text-ink-faint">{t('执行策略', 'Execution strategy')}</div>
          <div className="mt-1 text-lg font-semibold text-ink">System · UI · AI · Hybrid</div>
          <p className="mt-1 text-xs leading-5 text-ink-mute">{t('按步骤选择系统 API、确定性 UI 或 AI 策略。', 'Choose system API, deterministic UI or AI per step.')}</p>
        </div>
        <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 shadow-sm">
          <div className="text-xs text-amber-700">{t('当前阶段', 'Current stage')}</div>
          <div className="mt-1 text-lg font-semibold text-amber-900">{t('计划编辑与校验', 'Editing and validation')}</div>
          <p className="mt-1 text-xs leading-5 text-amber-800">{t('真实设备单步执行和 Plan Runner 尚未接入。', 'Live device stepping and the Plan Runner are not connected yet.')}</p>
        </div>
      </div>

      <section className="rounded-xl border border-hairline bg-white p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{t('选择调试对象', 'Select a plan source')}</h2>
            <p className="mt-1 text-xs text-ink-mute">{t('目前从已录制的用例套件中选择用例并生成执行计划。', 'Select a recorded suite to import and edit its execution plans.')}</p>
          </div>
          <span className="rounded-full bg-primary-soft px-2.5 py-1 text-xs font-medium text-primary-deep">
            {recordedSuites.length} {t('个录制套件', 'recorded suites')}
          </span>
        </div>

        {isLoading ? (
          <div className="py-8 text-center text-sm text-ink-faint">{t('正在读取用例套件…', 'Loading suites…')}</div>
        ) : isError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {t('读取用例套件失败：', 'Failed to load suites: ')}{String((error as Error)?.message || error)}
          </div>
        ) : recordedSuites.length === 0 ? (
          <div className="rounded-lg border border-dashed border-hairline bg-canvas-soft px-4 py-10 text-center">
            <div className="text-sm font-semibold text-ink">{t('暂时没有录制用例套件', 'No recorded suites yet')}</div>
            <p className="mx-auto mt-1 max-w-md text-xs leading-5 text-ink-mute">{t('先到录制工作台完成一次录制并保存为用例套件，然后回来生成执行计划。', 'Record and save a case suite in the recorder first, then return here to create an execution plan.')}</p>
            <Link to="/recorder" className="mt-4 inline-flex rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90">
              {t('前往录制工作台', 'Go to recorder')}
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">{t('录制用例套件', 'Recorded suite')}</label>
              <select
                className="w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-primary"
                value={selectedSuite?.id || ''}
                onChange={event => selectSuite(event.target.value)}
              >
                {recordedSuites.map(suite => (
                  <option key={suite.id} value={suite.id}>{suite.name} · {suite.case_count} {t('条用例', 'cases')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">{t('调试设备（预留）', 'Debug device (reserved)')}</label>
              <select className="w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-primary"
                value={deviceId} onChange={event => {
                  setDeviceId(event.target.value)
                  const device = onlineDevices.find(item => item.id === event.target.value)
                  if (device?.platform) setPlatform(device.platform)
                }}>
                <option value="">{t('暂不选择设备', 'No device selected')}</option>
                {onlineDevices.map(device => <option key={device.id} value={device.id}>{device.name} · {device.platform || device.connection_type}</option>)}
              </select>
              <p className="mt-1 text-[11px] text-ink-faint">{selectedDevice ? t('已选择在线设备，真实执行入口尚未接入。', 'Online device selected; live execution is not connected yet.') : t('设备选择仅用于后续单步调试接入。', 'Device selection is reserved for future step debugging.')}</p>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">{t('目标平台', 'Target platform')}</label>
              <select className="w-full rounded-lg border border-hairline bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-primary"
                value={platform} onChange={event => setPlatform(event.target.value)}>
                <option value="android">Android</option>
                <option value="harmony">HarmonyOS</option>
              </select>
            </div>
          </div>
        )}
      </section>

      {selectedSuite && (
        <ExecutionPlanManager key={selectedSuite.id} suiteId={selectedSuite.id} platform={platform} />
      )}
    </div>
  )
}
