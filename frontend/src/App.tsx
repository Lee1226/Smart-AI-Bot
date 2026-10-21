import { useState, useEffect } from 'react'
import { BrowserRouter, Link, Route, Routes, Navigate, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useT } from './lib/i18n'
import { fetchMe, getToken, clearToken } from './lib/api'
import { disconnectUsb } from './lib/browserUsb'
import Devices from './pages/Devices'
import Suites from './pages/Suites'
import SuiteDetail from './pages/SuiteDetail'
import Runs from './pages/Runs'
import Regressions from './pages/Regressions'
import RunDetail from './pages/RunDetail'
import Settings from './pages/Settings'
import QuickRun from './pages/QuickRun'
import Recorder from './pages/Recorder'
import RunCompare from './pages/RunCompare'
import Library from './pages/Library'
import Knowledge from './pages/Knowledge'
import Login from './pages/Login'
import Register from './pages/Register'
import Account from './pages/Account'
import AutomationCatalog from './pages/AutomationCatalog'

type NavigationItem = { to: string; label: { zh: string; en: string }; icon: string; paths: string[] }
const NAV_GROUPS: { label: { zh: string; en: string }; items: NavigationItem[] }[] = [
  { label: { zh: '测试工作台', en: 'Test workspace' }, items: [
    { to: '/', label: { zh: '设备', en: 'Devices' }, icon: '📱', paths: ['/'] },
    { to: '/quick', label: { zh: '任务执行', en: 'Execution' }, icon: '⚡', paths: ['/quick', '/regressions'] },
    { to: '/suites', label: { zh: '用例中心', en: 'Cases' }, icon: '🧪', paths: ['/suites', '/library', '/automation', '/recorder', '/knowledge'] },
    { to: '/runs', label: { zh: '运行结果', en: 'Results' }, icon: '▶', paths: ['/runs'] },
  ] },
  { label: { zh: '管理', en: 'Manage' }, items: [
    { to: '/settings', label: { zh: '设置与账号', en: 'Settings & account' }, icon: '⚙', paths: ['/settings', '/account'] },
  ] },
]
const PAGE_TABS = {
  execution: [
    { to: '/quick', zh: '快速任务', en: 'Quick task' },
    { to: '/regressions', zh: '多设备回归', en: 'Regression' },
  ],
  cases: [
    { to: '/suites', zh: '用例套件', en: 'Suites' },
    { to: '/library', zh: '步骤复用库', en: 'Reusable steps' },
    { to: '/automation', zh: 'Maestro 用例', en: 'Maestro cases' },
    { to: '/recorder', zh: '录制用例', en: 'Record' },
    { to: '/knowledge', zh: '知识库', en: 'Knowledge' },
  ],
  results: [
    { to: '/runs', zh: '运行记录', en: 'Run history' },
    { to: '/runs/compare', zh: '结果对比', en: 'Compare' },
  ],
  settings: [
    { to: '/settings', zh: '平台设置', en: 'Settings' },
    { to: '/account', zh: '账号与权限', en: 'Account & access' },
  ],
}

function matchesPath(pathname: string, path: string) {
  return path === '/' ? pathname === '/' : pathname === path || pathname.startsWith(path + '/')
}

function PageTabs({ section, children }: { section: keyof typeof PAGE_TABS; children: React.ReactNode }) {
  const t = useT()
  const { pathname } = useLocation()
  const tabs = PAGE_TABS[section]
  const active = [...tabs].reverse().find(tab => matchesPath(pathname, tab.to))?.to
  return <>
    <nav aria-label={t('页面分栏', 'Page sections')} className="flex flex-wrap gap-1 border-b border-hairline mb-6">
      {tabs.map(tab => <Link key={tab.to} to={tab.to} aria-current={active === tab.to ? "page" : undefined}
        className={`px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors ${active === tab.to ? 'border-primary text-primary-deep font-semibold' : 'border-transparent text-ink-mute hover:text-ink hover:bg-canvas-cool rounded-t-md'}`}>
        {t(tab.zh, tab.en)}
      </Link>)}
    </nav>
    {children}
  </>
}

function UserMenu() {
  const t = useT()
  const qc = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    staleTime: 5 * 60 * 1000,
  })
  const [open, setOpen] = useState(false)

  if (!me) return null

  const handleLogout = () => {
    disconnectUsb()
    clearToken()
    qc.clear()
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-canvas-cool rounded"
      >
        <span className="w-5 h-5 rounded-full bg-primary-soft text-primary-deep text-xs flex items-center justify-center font-bold">
          {(me.name || me.email)[0].toUpperCase()}
        </span>
        <span className="truncate flex-1">{me.name || me.email}</span>
        <span className="text-gray-400 text-xs">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 mb-1 w-48 bg-white border rounded-lg shadow-lg z-20 py-1 text-sm">
            <div className="px-3 py-2 border-b text-xs text-gray-400">
              {me.account_name} · {me.role}
            </div>
            <a href="/settings" className="block px-3 py-1.5 hover:bg-canvas-cool">
              {t('设置', 'Settings')}
            </a>
            <button
              onClick={handleLogout}
              className="w-full text-left px-3 py-1.5 hover:bg-canvas-cool text-red-500"
            >
              {t('退出登录', 'Logout')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/** Require auth: if no token, redirect to /login. Children only render when logged in. */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const hasToken = !!getToken()

  const { data: me, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    enabled: hasToken,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })

  // Show nothing while briefly checking token validity
  if (hasToken && isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas-soft">
        <div className="text-gray-400 text-sm">Loading…</div>
      </div>
    )
  }

  if (!me) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}

function AppLayout() {
  const { pathname } = useLocation()
  useEffect(() => {
    const close = () => disconnectUsb()
    const changedAccount = (event: StorageEvent) => { if (event.key === 'sab_token') close() }
    window.addEventListener('pagehide', close)
    window.addEventListener('storage', changedAccount)
    return () => {
      window.removeEventListener('pagehide', close)
      window.removeEventListener('storage', changedAccount)
      close()
    }
  }, [])
  const t = useT()
  return (
    <div className="min-h-screen flex bg-canvas-soft text-ink">
      {/* Left sidebar */}
      <aside className="w-52 shrink-0 border-r border-hairline bg-canvas flex flex-col sticky top-0 h-screen">
        <div className="h-14 flex items-center gap-2 px-4 border-b border-hairline">
          <img src="/favicon.png" alt="" className="w-6 h-6 rounded-md" />
          <span className="font-mono font-semibold text-base tracking-tight text-ink">smart-ai-bot</span>
        </div>
        <nav aria-label={t('主导航', 'Main navigation')} className="flex-1 p-2 space-y-6 overflow-y-auto">
          {NAV_GROUPS.map(group => <div key={group.label.en}>
            <div className="px-3 pt-3 pb-2 text-[11px] font-medium text-ink-faint tracking-wide">{t(group.label.zh, group.label.en)}</div>
            <div className="space-y-1">{group.items.map(({ to, label, icon, paths }) => {
              const active = paths.some(path => matchesPath(pathname, path))
              return <Link key={to} to={to} aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-md text-sm font-medium transition-colors ${active ? 'bg-primary-soft text-primary-deep' : 'text-ink-mute hover:bg-canvas-cool hover:text-ink'}`}>
                <span aria-hidden="true" className="w-4 text-center text-xs">{icon}</span>
                {t(label.zh, label.en)}
              </Link>
            })}</div>
          </div>)}
        </nav>
        <div className="border-t border-hairline p-2">
          <UserMenu />
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 px-8 py-8">
        <div className="max-w-[1700px] mx-auto">
          <Routes>
            <Route path="/" element={<Devices />} />
            <Route path="/quick" element={<PageTabs section="execution"><QuickRun /></PageTabs>} />
            <Route path="/recorder" element={<PageTabs section="cases"><Recorder /></PageTabs>} />
            <Route path="/suites" element={<PageTabs section="cases"><Suites /></PageTabs>} />
            <Route path="/suites/:suiteId" element={<PageTabs section="cases"><SuiteDetail /></PageTabs>} />
            <Route path="/library" element={<PageTabs section="cases"><Library /></PageTabs>} />
            <Route path="/automation" element={<PageTabs section="cases"><AutomationCatalog /></PageTabs>} />
            <Route path="/automation/:kind/:id" element={<PageTabs section="cases"><AutomationCatalog /></PageTabs>} />
            <Route path="/knowledge" element={<PageTabs section="cases"><Knowledge /></PageTabs>} />
            <Route path="/regressions" element={<PageTabs section="execution"><Regressions /></PageTabs>} />
            <Route path="/runs" element={<PageTabs section="results"><Runs /></PageTabs>} />
            <Route path="/runs/compare" element={<PageTabs section="results"><RunCompare /></PageTabs>} />
            <Route path="/runs/:runId" element={<PageTabs section="results"><RunDetail /></PageTabs>} />
            <Route path="/account" element={<PageTabs section="settings"><Account /></PageTabs>} />
            <Route path="/settings" element={<PageTabs section="settings"><Settings /></PageTabs>} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public: login / register (full screen, no sidebar) */}
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />

        {/* Protected: everything else */}
        <Route
          path="/*"
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
