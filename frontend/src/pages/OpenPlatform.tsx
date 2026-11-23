import { Link } from 'react-router-dom'
import { useT } from '../lib/i18n'

const capabilities = [
  { icon: '▯', title: '设备与资产', description: '查询设备状态、测试用例套件及可复用技能。' },
  { icon: '▶', title: '测试任务', description: '通过接口创建、查询和管理测试任务。' },
  { icon: '◷', title: '实时进度', description: '通过服务端事件流获取任务执行进度。' },
  { icon: '▤', title: '结果与视频', description: '获取测试结果、评估信息和测试视频记录。' },
]

export default function OpenPlatform() {
  const t = useT()

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="rounded-2xl bg-gradient-to-br from-[#175CD3] via-[#2878E8] to-[#38A6D9] p-5 text-white shadow-[0_16px_40px_rgba(37,99,235,0.18)] sm:p-6">
        <div className="text-xs font-semibold tracking-[0.18em] text-blue-100">AI TERMINAL · DEVELOPER PLATFORM</div>
        <h1 className="mt-2 text-xl font-bold">{t('开放平台', 'Open Platform')}</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100">
          {t('通过标准 HTTP REST API，将设备管理、AI 测试执行和测试结果能力集成到第三方系统。', 'Integrate device management, AI test execution and test results into external systems through standard HTTP REST APIs.')}
        </p>
      </header>

      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800">{t('接口服务建设', 'API service status')}</h2>
            <p className="mt-1 text-sm text-slate-500">{t('第一阶段：统一 HTTP REST API', 'Phase 1: Unified HTTP REST API')}</p>
          </div>
          <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">{t('建设中', 'In progress')}</span>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-slate-50 p-4">
            <p className="text-xs text-slate-500">{t('规划接口前缀', 'Planned API base path')}</p>
            <code className="mt-2 block text-sm font-semibold text-slate-800">/api/open/v1</code>
          </div>
          <div className="rounded-xl bg-slate-50 p-4">
            <p className="text-xs text-slate-500">{t('认证方式', 'Authentication')}</p>
            <p className="mt-2 text-sm font-semibold text-slate-800">Bearer API Key</p>
          </div>
        </div>
        <p className="mt-3 text-xs leading-5 text-slate-500">
          {t('说明：上述为规划中的开放接口规范。专用接口路由、权限校验和 OpenAPI 文档尚在建设，当前不能将该前缀视为已可调用的正式 API。', 'Note: This is the planned public API contract. Dedicated routes, permission checks and OpenAPI documentation are still being implemented; this prefix is not yet a production-ready API.')}
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        {capabilities.map(item => (
          <div key={item.title} className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_28px_rgba(31,75,135,0.04)]">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-lg font-semibold text-blue-600">{item.icon}</div>
            <h3 className="mt-3 text-sm font-semibold text-slate-800">{t(item.title, item.title)}</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">{t(item.description, item.description)}</p>
          </div>
        ))}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_28px_rgba(31,75,135,0.05)] sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800">{t('接入凭证', 'Access credentials')}</h2>
            <p className="mt-1 text-sm text-slate-500">{t('创建和管理外部系统使用的 API Key。', 'Create and manage API keys for external integrations.')}</p>
          </div>
          <Link to="/open-platform/keys" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700">
            {t('管理 API 密钥', 'Manage API keys')}
          </Link>
        </div>
      </section>
    </div>
  )
}
