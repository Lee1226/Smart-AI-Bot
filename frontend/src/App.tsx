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
import Recordings from './pages/Recordings'
import Regressions from './pages/Regressions'
import RunDetail from './pages/RunDetail'
import Settings from './pages/Settings'
import RecordingSettings from './pages/RecordingSettings'
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
import ExecutionPlanStudio from './pages/ExecutionPlanStudio'
import AppManagement from './pages/AppManagement'
import ExplorationCenter from './pages/ExplorationCenter'
import SkillLibrary from './pages/SkillLibrary'

type NavigationItem = { to: string; label: { zh: string; en: string }; icon: string; iconStyle: string; paths: string[]; children?: NavigationItem[] }
const NAV_GROUPS: { label: { zh: string; en: string }; items: NavigationItem[] }[] = [
  { label: { zh: '测试工作台', en: 'Test workspace' }, items: [
    { to: '/', label: { zh: '工作台', en: 'Workbench' }, icon: '▦', iconStyle: 'from-blue-500 to-indigo-600', paths: ['/'] },
    { to: '/devices', label: { zh: '设备管理', en: 'Devices' }, icon: '▯', iconStyle: 'from-teal-400 to-cyan-600', paths: ['/devices'] },
    { to: '/debug', label: { zh: '调试控制台', en: 'Debug console' }, icon: '⌘', iconStyle: 'from-violet-500 to-indigo-600', paths: ['/debug'] },
    { to: '/quick', label: { zh: '任务执行', en: 'Execution' }, icon: 'ϟ', iconStyle: 'from-amber-400 to-orange-600', paths: ['/quick', '/regressions'] },
    { to: '/suites', label: { zh: '用例中心', en: 'Cases' }, icon: '⚗', iconStyle: 'from-fuchsia-500 to-purple-600', paths: ['/suites', '/library', '/automation', '/recorder', '/knowledge', '/execution-plans', '/apps'] },
    { to: '/runs', label: { zh: '测试结果中心', en: 'Test results' }, icon: '▤', iconStyle: 'from-sky-400 to-blue-600', paths: ['/runs', '/recordings'], children: [
      { to: '/runs', label: { zh: '测试任务', en: 'Test tasks' }, icon: '▶', iconStyle: 'from-sky-400 to-blue-600', paths: ['/runs'] },
      { to: '/runs/compare', label: { zh: '结果对比', en: 'Result comparison' }, icon: '⇄', iconStyle: 'from-indigo-400 to-violet-600', paths: ['/runs/compare'] },
      { to: '/recordings', label: { zh: '测试视频记录', en: 'Test video records' }, icon: '▣', iconStyle: 'from-pink-400 to-rose-600', paths: ['/recordings'] },
    ] },
  ] },
  { label: { zh: 'AI 探索', en: 'AI exploration' }, items: [
    { to: '/exploration', label: { zh: 'AI 探索中心', en: 'Exploration center' }, icon: '✧', iconStyle: 'from-cyan-400 to-violet-600', paths: ['/exploration'] },
    { to: '/skills', label: { zh: '技能库', en: 'Skill library' }, icon: '◇', iconStyle: 'from-violet-400 to-fuchsia-600', paths: ['/skills'] },
  ] },
  { label: { zh: '管理', en: 'Manage' }, items: [
    { to: '/settings', label: { zh: '系统设置', en: 'System settings' }, icon: '⚙', iconStyle: 'from-slate-400 to-slate-600', paths: ['/settings', '/account'] },
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
    { to: '/execution-plans', zh: '执行计划', en: 'Execution plans' },
    { to: '/apps', zh: '设备应用库', en: 'App library' },
    { to: '/knowledge', zh: '知识库', en: 'Knowledge' },
  ],
  results: [
    { to: '/runs', zh: '任务管理', en: 'Task management' },
    { to: '/runs/compare', zh: '结果对比', en: 'Compare' },
    { to: '/recordings', zh: '测试视频记录', en: 'Test video records' },
  ],
  settings: [
    { to: '/settings', zh: '平台设置', en: 'Settings' },
    { to: '/settings/recording', zh: '录制与存储', en: 'Recording & storage' },
    { to: '/account', zh: '账号与权限', en: 'Account & access' },
  ],
}

function matchesPath(pathname: string, path: string) {
  if (path === '/') return pathname === '/'
  // /runs is the task list/detail route; /runs/compare is a separate navigation item.
  if (path === '/runs' && pathname.startsWith('/runs/compare')) return false
  return pathname === path || pathname.startsWith(path + '/')
}

function PageTabs({ section, children }: { section: keyof typeof PAGE_TABS; children: React.ReactNode }) {
  const t = useT()
  const { pathname } = useLocation()
  const tabs = PAGE_TABS[section]
  const active = [...tabs].reverse().find(tab => matchesPath(pathname, tab.to))?.to
  return <>
    <nav aria-label={t('页面分栏', 'Page sections')} className="flex flex-wrap gap-1 border-b border-hairline mb-5">
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
  const initials = (me.name || me.email).trim().slice(0, 2).toUpperCase()

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={t('用户菜单', 'User menu')}
        className="flex items-center gap-2 rounded-full px-1.5 py-1 transition-colors hover:bg-slate-50"
      >
        <span className="grid h-10 w-10 place-items-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-base font-semibold text-white shadow-sm">
          {initials}
        </span>
        <span className="hidden max-w-[140px] truncate text-sm font-medium text-slate-700 xl:block">{me.name || me.email}</span>
        <span className="pr-1 text-base text-slate-500">{open ? '⌃' : '⌄'}</span>
      </button>
      {open && (
        <>
          <button aria-label={t('关闭用户菜单', 'Close user menu')} className="fixed inset-0 z-30 cursor-default" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-40 mt-3 w-56 rounded-2xl border border-slate-100 bg-white py-2 text-sm shadow-xl shadow-slate-200/70">
            <div className="border-b border-slate-100 px-4 py-3">
              <div className="font-semibold text-slate-800">{me.account_name}</div>
              <div className="mt-1 text-xs text-slate-400">{me.email} · {me.role}</div>
            </div>
            <Link to="/settings" onClick={() => setOpen(false)} className="block px-4 py-3 text-slate-600 hover:bg-blue-50 hover:text-blue-700">
              {t('设置', 'Settings')}
            </Link>
            <button onClick={handleLogout} className="w-full px-4 py-3 text-left text-rose-600 hover:bg-rose-50">
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
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
  const activeItem = NAV_GROUPS.flatMap(group => group.items.flatMap(item => [item, ...(item.children ?? [])])).find(item => item.paths.some(path => matchesPath(pathname, path)))
  const pageLabel = activeItem ? t(activeItem.label.zh, activeItem.label.en) : t('工作台', 'Workbench')
  const [resultsExpanded, setResultsExpanded] = useState(['/runs', '/recordings'].some(path => matchesPath(pathname, path)))
  useEffect(() => {
    if (['/runs', '/recordings'].some(path => matchesPath(pathname, path))) setResultsExpanded(true)
  }, [pathname])

  return (
    <div className="min-h-screen bg-canvas-soft text-ink">
      <header className="relative z-20 flex h-[68px] shrink-0 items-center border-b border-hairline bg-white/95 px-5 shadow-[0_2px_14px_rgba(39,87,150,0.035)] backdrop-blur-xl lg:px-7 2xl:h-[76px] 2xl:px-9">
        <div className="flex min-w-0 items-center gap-3">
          <img src="/brand-icon.svg" alt="" className="h-11 w-11 shrink-0 rounded-xl shadow-sm shadow-blue-200/40 2xl:h-12 2xl:w-12 2xl:rounded-xl" />
          <span className="whitespace-nowrap text-[18px] font-bold tracking-tight text-slate-900 sm:text-[18px] 2xl:text-[20px]">翼矩AI终端测试系统</span>
        </div>
        <div className="mx-4 hidden h-10 w-px bg-slate-200 md:block" />
        <button
          type="button"
          aria-label={t('折叠导航栏', 'Toggle navigation')}
          aria-expanded={!sidebarCollapsed}
          onClick={() => setSidebarCollapsed(value => !value)}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-xl text-slate-600 transition-colors hover:bg-blue-50 hover:text-blue-600"
        >
          <span aria-hidden="true">☰</span>
        </button>
        <div className="mx-4 hidden h-8 w-px bg-slate-100 sm:block" />
        <div className="flex min-w-0 items-center gap-2.5 text-slate-500">
          <span className="text-lg text-slate-500" aria-hidden="true">⌂</span>
          <span className="truncate text-sm font-medium text-slate-600 sm:text-[15px]">{pageLabel}</span>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-3 sm:gap-5">
          <div className="hidden h-10 w-px bg-slate-200 sm:block" />
          <div className="relative grid h-9 w-9 place-items-center text-2xl text-slate-700" aria-label={t('通知', 'Notifications')}>
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>
            <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full border-2 border-white bg-rose-400" />
          </div>
          <UserMenu />
        </div>
      </header>

      <div className="flex min-h-[calc(100vh-68px)] 2xl:min-h-[calc(100vh-76px)]">
        <aside className={`${sidebarCollapsed ? 'w-[72px]' : 'w-[232px] 2xl:w-[256px]'} sticky top-0 flex h-[calc(100vh-68px)] 2xl:h-[calc(100vh-76px)] shrink-0 flex-col border-r border-hairline bg-white/75 transition-[width] duration-200`}>
          <nav aria-label={t('主导航', 'Main navigation')} className={`flex-1 overflow-y-auto ${sidebarCollapsed ? 'px-2.5' : 'px-2.5 xl:px-3'} py-4`}>
            <div className="space-y-1.5">
              {NAV_GROUPS.map(group => <div key={group.label.en} className="space-y-2">
                {group.items.map(({ to, label, icon, iconStyle, paths, children }) => {
                  const active = paths.some(path => matchesPath(pathname, path))
                  if (children) return <div key={to} className="space-y-1">
                    <button
                      type="button"
                      aria-expanded={resultsExpanded}
                      aria-label={t(label.zh, label.en)}
                      title={sidebarCollapsed ? t(label.zh, label.en) : undefined}
                      onClick={() => {
                        if (sidebarCollapsed) {
                          setSidebarCollapsed(false)
                          setResultsExpanded(true)
                        } else {
                          setResultsExpanded(value => !value)
                        }
                      }}
                      className={`group relative flex min-h-[42px] 2xl:min-h-[44px] w-full items-center rounded-lg transition-colors duration-200 ease-out motion-reduce:transition-none ${sidebarCollapsed ? 'justify-center px-1.5' : 'gap-2.5 px-2.5'} ${active ? 'bg-[#F2F6FC] text-[#356FAF]' : 'text-slate-700 hover:bg-slate-50 hover:text-blue-700'}`}
                    >
                      <span aria-hidden="true" className={`flex h-7 w-7 2xl:h-8 2xl:w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br text-base font-semibold leading-none text-white shadow-sm shadow-blue-100/60 ${iconStyle}`}>{icon}</span>
                      {!sidebarCollapsed && <>
                        <span className="min-w-0 flex-1 truncate text-left text-[13px] font-medium 2xl:text-sm">{t(label.zh, label.en)}</span>
                        <span aria-hidden="true" className={`text-xs text-slate-400 transition-transform duration-200 ease-out motion-reduce:transition-none ${resultsExpanded ? 'rotate-180' : ''}`}>⌄</span>
                      </>}
                    </button>
                    {!sidebarCollapsed && <div className={`grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none ${resultsExpanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}>
                      <div className="min-h-0 overflow-hidden">
                        <div className="ml-7 space-y-1 pt-1">
                          {children.map(child => {
                            const childActive = child.paths.some(path => matchesPath(pathname, path))
                            return <Link key={child.to} to={child.to} aria-current={childActive ? "page" : undefined}
                              className={`relative flex min-h-[36px] items-center rounded-md px-3 text-[12px] transition-[background-color,color] duration-150 ease-out 2xl:text-[13px] ${childActive ? 'bg-[#EAF2FF] font-semibold text-[#2469D8] before:absolute before:bottom-2 before:left-0 before:top-2 before:w-[3px] before:rounded-full before:bg-[#3478F6]' : 'text-slate-600 hover:bg-slate-50 hover:text-blue-700'}`}>
                              <span className="min-w-0 truncate">{t(child.label.zh, child.label.en)}</span>
                            </Link>
                          })}
                        </div>
                      </div>
                    </div>}
                  </div>
                  return <Link
                    key={to}
                    to={to}
                    aria-current={active ? "page" : undefined}
                    title={sidebarCollapsed ? t(label.zh, label.en) : undefined}
                    className={`group relative flex min-h-[42px] 2xl:min-h-[44px] items-center rounded-lg transition-all duration-200 ${sidebarCollapsed ? 'justify-center px-1.5' : 'gap-2.5 px-2.5'} ${active ? 'bg-[#E8F1FF] text-[#2674F5] shadow-[inset_3px_0_0_#3478F6]' : 'text-slate-700 hover:bg-slate-50 hover:text-blue-700'}`}
                  >
                    <span aria-hidden="true" className={`flex h-7 w-7 2xl:h-8 2xl:w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br text-base font-semibold leading-none text-white shadow-sm shadow-blue-100/60 ${iconStyle}`}>{icon}</span>
                    {!sidebarCollapsed && <span className="min-w-0 truncate text-[13px] font-medium 2xl:text-sm">{t(label.zh, label.en)}</span>}
                  </Link>
                })}
              </div>)}
            </div>
          </nav>
        </aside>

        <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 sm:py-6 xl:px-7 xl:py-7 2xl:px-8">
          <div className="mx-auto max-w-[1700px]">
            <Routes>
              <Route path="/" element={<Workbench />} />
              <Route path="/devices" element={<Devices />} />
              <Route path="/debug" element={<DebugConsole />} />
              <Route path="/exploration" element={<ExplorationCenter />} />
              <Route path="/exploration/new" element={<ExplorationCenter />} />
              <Route path="/exploration/history" element={<ExplorationCenter />} />
              <Route path="/exploration/knowledge" element={<ExplorationCenter />} />
              <Route path="/exploration/plans" element={<ExplorationCenter />} />
              <Route path="/skills" element={<SkillLibrary />} />
              <Route path="/quick" element={<PageTabs section="execution"><QuickRun /></PageTabs>} />
              <Route path="/recorder" element={<PageTabs section="cases"><Recorder /></PageTabs>} />
              <Route path="/execution-plans" element={<PageTabs section="cases"><ExecutionPlanStudio /></PageTabs>} />
              <Route path="/apps" element={<PageTabs section="cases"><AppManagement /></PageTabs>} />
              <Route path="/suites" element={<PageTabs section="cases"><Suites /></PageTabs>} />
              <Route path="/suites/:suiteId" element={<PageTabs section="cases"><SuiteDetail /></PageTabs>} />
              <Route path="/library" element={<PageTabs section="cases"><Library /></PageTabs>} />
              <Route path="/automation" element={<PageTabs section="cases"><AutomationCatalog /></PageTabs>} />
              <Route path="/automation/:kind/:id" element={<PageTabs section="cases"><AutomationCatalog /></PageTabs>} />
              <Route path="/knowledge" element={<PageTabs section="cases"><Knowledge /></PageTabs>} />
              <Route path="/regressions" element={<PageTabs section="execution"><Regressions /></PageTabs>} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/recordings" element={<Recordings />} />
              <Route path="/runs/compare" element={<RunCompare />} />
              <Route path="/runs/:runId" element={<RunDetail />} />
              <Route path="/account" element={<PageTabs section="settings"><Account /></PageTabs>} />
              <Route path="/settings" element={<PageTabs section="settings"><Settings /></PageTabs>} />
              <Route path="/settings/recording" element={<PageTabs section="settings"><RecordingSettings /></PageTabs>} />
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
