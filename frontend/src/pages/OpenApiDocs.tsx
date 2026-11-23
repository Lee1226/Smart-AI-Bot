import { useT } from '../lib/i18n'

const endpoints = [
  { method: 'GET', path: '/devices', scope: 'devices:read', desc: '查询当前账号下的设备列表。' },
  { method: 'GET', path: '/suites', scope: 'suites:read', desc: '查询测试用例套件列表及用例数量。' },
  { method: 'GET', path: '/runs', scope: 'runs:read', desc: '分页查询测试任务，可按状态筛选。' },
]

export default function OpenApiDocs() {
  const t = useT()
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs font-semibold tracking-[0.16em] text-blue-600">OPEN API · V1</p>
        <h1 className="mt-1 text-xl font-bold text-slate-900">{t('接口文档', 'API documentation')}</h1>
        <p className="mt-2 text-sm text-slate-500">{t('当前版本提供设备、用例套件和测试任务的只读查询能力。', 'This version provides read-only queries for devices, test suites and test runs.')}</p>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-800">{t('基础信息', 'Base information')}</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">{t('基础路径', 'Base URL')}</p><code className="mt-1 block text-sm">/api/open/v1</code></div>
          <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">{t('认证方式', 'Authentication')}</p><code className="mt-1 block text-sm">Authorization: Bearer sk-...</code></div>
        </div>
        <p className="mt-3 text-xs leading-5 text-slate-500">{t('所有接口均按 API Key 所属账号隔离数据。密钥必须具备对应读取权限；JWT 登录态仅用于平台内部会话。', 'All endpoints are tenant-scoped. API keys must include the corresponding read scope; JWT sessions are for the internal application.')}</p>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-800">{t('接口列表', 'Endpoints')}</h2>
        <div className="mt-3 space-y-3">
          {endpoints.map(item => (
            <div key={item.path} className="rounded-xl border border-slate-100 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">{item.method}</span>
                <code className="text-sm font-semibold text-slate-800">/api/open/v1{item.path}</code>
              </div>
              <p className="mt-2 text-xs text-slate-600">{t(item.desc, item.desc)}</p>
              <p className="mt-1 text-[11px] text-slate-400">{t('所需权限', 'Required scope')}: {item.scope}</p>
              {item.path === '/runs' && <p className="mt-1 text-[11px] text-slate-400">Query: page, page_size, status=all|active|done|problem</p>}
              {(item.path === '/devices' || item.path === '/suites') && <p className="mt-1 text-[11px] text-slate-400">Query: page, page_size</p>}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-800">{t('调用示例', 'Request example')}</h2>
        <p className="mt-2 text-xs text-slate-500">{t('将 BASE_URL 替换为部署平台的访问地址，将 API_KEY 替换为创建时保存的完整密钥。', 'Replace BASE_URL with your deployment URL and API_KEY with the full key saved at creation time.')}</p>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs leading-6 text-slate-100"><code>{`curl -X GET "BASE_URL/api/open/v1/devices?page=1&page_size=20" \\\n  -H "Authorization: Bearer API_KEY" \\\n  -H "Accept: application/json"`}</code></pre>
      </section>

      <section className="rounded-2xl border border-amber-200 bg-amber-50/60 p-4">
        <h2 className="text-sm font-semibold text-amber-900">{t('暂未开放的能力', 'Not available yet')}</h2>
        <p className="mt-1 text-xs leading-5 text-amber-800">{t('创建或启动测试任务、取消任务、读取测试结果详情、SSE 实时进度、视频下载和 Webhook 订阅尚未纳入当前公开 API。请勿直接调用平台内部接口替代。', 'Creating or starting runs, cancelling runs, fetching detailed results, SSE progress, video downloads and webhook subscriptions are not yet part of the public API. Do not substitute internal endpoints.')}</p>
      </section>
    </div>
  )
}
