import { useState, useEffect } from 'react'
import { BrowserRouter, Link, Route, Routes, Navigate, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useT } from './lib/i18n'
import { fetchMe, getToken, clearToken } from './lib/api'
import { disconnectUsb } from './lib/browserUsb'
import Devices from './pages/Devices'
import Workbench from './pages/Workbench'
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
import DebugConsole from './pages/DebugConsole'

type NavigationItem = { to: string; label: { zh: string; en: string }; icon: string; paths: string[] }
const NAV_GROUPS: { label: { zh: string; en: string }; items: NavigationItem[] }[] = [
  { label: { zh: '测试工作台', en: 'Test workspace' }, items: [
    { to: '/', label: { zh: '工作台', en: 'Workbench' }, icon: 'grid', paths: ['/'] },
    { to: '/devices', label: { zh: '设备管理', en: 'Devices' }, icon: 'device', paths: ['/devices'] },
    { to: '/debug', label: { zh: '调试控制台', en: 'Debug console' }, icon: 'terminal', paths: ['/debug'] },
    { to: '/quick', label: { zh: '任务执行', en: 'Execution' }, icon: 'zap', paths: ['/quick', '/regressions'] },
    { to: '/suites', label: { zh: '用例中心', en: 'Cases' }, icon: 'flask', paths: ['/suites', '/library', '/automation', '/recorder', '/knowledge'] },
    { to: '/runs', label: { zh: '测试任务', en: 'Test tasks' }, icon: 'play', paths: ['/runs'] },
  ] },
  { label: { zh: '管理', en: 'Manage' }, items: [
    { to: '/settings', label: { zh: '设置与账号', en: 'Settings & account' }, icon: 'settings', paths: ['/settings', '/account'] },
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
    { to: '/runs', zh: '任务管理', en: 'Task management' },
    { to: '/runs/compare', zh: '结果对比', en: 'Compare' },
  ],
  settings: [
    { to: '/settings', zh: '平台设置', en: 'Settings' },
    { to: '/account', zh: '账号与权限', en: 'Account & access' },
  ],
}


function NavIcon({ name, active = false }: { name: string; active?: boolean }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  const paths: Record<string, React.ReactNode> = {
    grid: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.4" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.4" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.4" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.4" /></>,
    device: <><rect x="6.5" y="2.5" width="11" height="19" rx="2.2" /><path d="M10 18.5h4" /></>,
    terminal: <><rect x="2.8" y="3.5" width="18.4" height="17" rx="2" /><path d="m7 9 3 3-3 3m6 0h4" /></>,
    zap: <path d="m13 2.8-9 11h6l-1 7.4 11-12h-6l1-6.4Z" />,
    flask: <><path d="M9 3h6m-5 0v6l-5.4 9.2A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-2.8L14 9V3" /><path d="M7.5 15h9" /></>,
    play: <><circle cx="12" cy="12" r="9" /><path d="m10 8 6 4-6 4V8Z" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.1 1.9-1.8 3.1-2.2-.5a8 8 0 0 1-1.7 1l-.4 2.2h-3.6l-.4-2.2a8 8 0 0 1-1.7-1l-2.2.5-1.8-3.1 1.1-1.9A8 8 0 0 1 5.8 13l-2-1v-3l2-1a8 8 0 0 1 .9-1.8L5.6 4.3l1.8-3.1 2.2.5a8 8 0 0 1 1.7-1L11.7-1h3.6l.4 2.2a8 8 0 0 1 1.7 1l2.2-.5 1.8 3.1-1.1 1.9a8 8 0 0 1 .9 1.8l2 1v3l-2 1a8 8 0 0 1-.8 1.5Z" transform="translate(-1 1) scale(.92)" /></>,
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24" className={`h-[18px] w-[18px] shrink-0 transition-transform ${active ? 'scale-[1.04]' : ''}`} {...common}>{paths[name] || paths.grid}</svg>
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
      {/* Brand navigation */}
      <aside className="w-[232px] shrink-0 border-r border-hairline bg-white flex flex-col sticky top-0 h-screen shadow-[2px_0_12px_rgba(35,70,105,0.025)]">
        <div className="h-[72px] flex items-center gap-3 px-5 border-b border-hairline">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-[#2878D0] to-[#13A6B5] shadow-[0_4px_10px_rgba(40,120,208,0.22)]">
            <img src="/favicon.png" alt="" className="h-6 w-6 rounded-md" />
          </div>
          <div className="min-w-0">
            <div className="font-semibold tracking-tight text-[15px] text-ink">Smart-AI-Bot</div>
            <div className="mt-0.5 text-[10px] font-medium tracking-[0.13em] text-ink-faint">AI DEVICE LAB</div>
          </div>
        </div>
        <nav aria-label={t('主导航', 'Main navigation')} className="flex-1 p-3 space-y-6 overflow-y-auto">
          {NAV_GROUPS.map(group => <div key={group.label.en}>
            <div className="px-3 pt-3 pb-2 text-[10px] font-bold text-ink-faint tracking-[0.14em]">{t(group.label.zh, group.label.en)}</div>
            <div className="space-y-1">{group.items.map(({ to, label, icon, paths }) => {
              const active = paths.some(path => matchesPath(pathname, path))
              return <Link key={to} to={to} aria-current={active ? "page" : undefined}
                className={`group flex items-center gap-3 px-3 py-[11px] rounded-xl text-[13px] font-semibold transition-all duration-200 ${active ? 'bg-gradient-to-r from-[#EAF3FC] to-[#F1F8FE] text-primary-deep shadow-[inset_3px_0_0_#2878D0,0_2px_5px_rgba(40,120,208,0.045)]' : 'text-ink-mute hover:bg-[#F3F7FB] hover:text-ink'}`}>
                <span className={`grid h-8 w-8 place-items-center rounded-lg transition-colors ${active ? 'bg-white text-primary shadow-sm' : 'bg-transparent text-ink-faint group-hover:bg-white group-hover:text-primary'}`}><NavIcon name={icon} active={active} /></span>
                {t(label.zh, label.en)}
              </Link>
            })}</div>
          </div>)}
        </nav>
        <div className="border-t border-hairline p-2">
          <UserMenu />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-[60px] items-center justify-between border-b border-hairline/90 bg-white px-7 shadow-[0_2px_8px_rgba(35,70,105,0.025)]">
          <div className="flex items-center gap-2 text-[12px] text-ink-mute">
            <span className="font-medium text-ink-secondary">Smart-AI-Bot</span>
            <span className="text-ink-faint">/</span>
            <span>{pathname === '/' ? t('工作台', 'Workbench') : (NAV_GROUPS.flatMap(g => g.items).find(item => item.paths.some(path => matchesPath(pathname, path)))?.label.zh || t('平台管理', 'Platform'))}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden sm:inline-flex items-center gap-2 rounded-full border border-[#D9ECE6] bg-[#F0F8F4] px-3 py-1.5 text-[11px] font-semibold text-[#21835F]"><span className="h-1.5 w-1.5 rounded-full bg-[#22A477]" />{t('AI 测试平台', 'AI Testing Platform')}</span>
            <div className="h-7 w-px bg-hairline" />
            <span className="text-[11px] font-medium tracking-wide text-ink-faint">LAB / CONSOLE</span>
          </div>
        </header>
        {/* Main content */}
        <main className="flex-1 min-w-0 px-6 py-7 xl:px-8">
          <div className="max-w-[1700px] mx-auto">
          <Routes>
            <Route path="/" element={<Workbench />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/debug" element={<DebugConsole />} />
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
