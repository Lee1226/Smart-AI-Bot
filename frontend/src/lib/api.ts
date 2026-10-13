import axios from 'axios'

export const api = axios.create({ baseURL: '/api' })

// ── 401 interceptor: auto-redirect to login ──────────────────────────────────

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear stale token and redirect to login
      localStorage.removeItem('sab_token')
      delete api.defaults.headers.common['Authorization']
      // Only redirect if not already on login/register page
      if (!window.location.pathname.startsWith('/login') &&
          !window.location.pathname.startsWith('/register')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

// ── Types ────────────────────────────────────────────────────────────────────

export interface Device {
  id: string
  name: string
  token: string
  status: 'online' | 'offline'
  last_seen: string
  platform?: 'android' | 'harmony' | 'ios'
  connection_type: 'portal' | 'browser_usb' | 'adb_bridge' | 'hdc_bridge'
}

export interface Suite {
  id: string
  name: string
  source_format: string
  case_count: number
  created_at: string
  app_package?: string
  project_id?: string
  tag?: string
}

export interface SuiteListPage {
  total: number
  items: Suite[]
  tags: string[]
}

export interface Run {
  test_account_id?: string
  test_account_name?: string
  id: string
  suite_id: string
  suite_name: string | null
  device_id: string
  status: string
  provider: string
  model: string
  created_at: string
  finished_at: string | null
  passed: number
  failed: number
  errored: number
  skipped: number
  total: number
  total_tokens: number
  has_recording: boolean
}

export interface TestResult {
  id: string
  case_id: string
  path: string
  expected: string
  status: string
  reason: string
  steps: number
  screenshot_b64: string
  log: string
  started_at: string | null
  finished_at: string | null
  is_starred: boolean
  total_tokens: number
}

export interface StepLog {
  id: string
  step: number
  thought: string
  action: string
  action_result: string
  screenshot_b64: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  perception_ms: number
  llm_ms: number
  action_ms: number
  subgoal_index: number | null
  subgoal_desc: string
}

export interface Settings {
  openai_api_key: string
  anthropic_api_key: string
  anthropic_base_url: string
  gemini_api_key: string
  zhipu_api_key: string
  groq_api_key: string
  ollama_base_url: string
  ark_api_key: string
  ark_base_url: string
  aws_access_key_id: string
  aws_secret_access_key: string
  aws_region_name: string
  default_provider: string
  default_model: string
  verifier_provider: string
  verifier_model: string
  webhook_url: string
  webhook_type: string
  language: string
  configured_secrets: string[]
}

export interface AuthConfig {
  registration_enabled: boolean
}

export const fetchAuthConfig = () => api.get<AuthConfig>('/auth/config').then(r => r.data)

// ── Devices ──────────────────────────────────────────────────────────────────

export const fetchDevices = () => api.get<Device[]>('/devices').then(r => r.data)
export const createDevice = (name: string, connection_type: Device['connection_type'] = 'portal') =>
  api.post<Device>('/devices', { name, connection_type }).then(r => r.data)
export const deleteDevice = (id: string) => api.delete(`/devices/${id}`)
export const renameDevice = (id: string, name: string) =>
  api.patch<Device>(`/devices/${id}`, { name }).then(r => r.data)

// ── App distribution (APK QR download) ───────────────────────────────────────

export interface AppInfo {
  available: boolean
  version?: string
  filename?: string
  size?: number
}

export const fetchAppInfo = () => api.get<AppInfo>('/app/latest').then(r => r.data)

export interface ServerInfo {
  lan_ip: string
  port: number  // backend's own port — phones connect here directly (not the dev-proxy port)
}

export const fetchServerInfo = () => api.get<ServerInfo>('/server/info').then(r => r.data)

// ── Live device screen ───────────────────────────────────────────────────────

export interface DeviceCapabilities {
  online: boolean
  adb_available: boolean
  adb_serials: string[]
  video_available?: boolean
}

export const fetchDeviceCapabilities = (id: string) =>
  api.get<DeviceCapabilities>(`/devices/${id}/capabilities`).then(r => r.data)

// ── Suites ───────────────────────────────────────────────────────────────────

export const fetchSuites = (params?: { q?: string; tag?: string; page?: number; page_size?: number }) =>
  api.get<SuiteListPage>('/suites', { params }).then(r => r.data)
export const fetchSuite = (id: string) => api.get<Suite>(`/suites/${id}`).then(r => r.data)
export const createSuite = (data: { name: string; app_package: string; project_id?: string; tag: string }) =>
  api.post<Suite>('/suites/manual', data).then(r => r.data)
export const uploadSuite = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post<Suite>('/suites', form).then(r => r.data)
}
export const deleteSuite = (id: string) => api.delete(`/suites/${id}`)
export const importSuite = (data: any) =>
  api.post<Suite>('/suites/import', data).then(r => r.data)

export interface SuiteImportDraft {
  name: string
  platform: 'android' | 'ios' | 'harmony'
  project_id?: string
  app_package: string
  model: string
  sources: { id: string; path: string; expected: string; steps: { action: string; expected: string }[] }[]
  cases: { title: string; preconditions: string[]; steps: { action: string; assertions: { text: string; source_ids: string[] }[] }[] }[]
  changes: string[]
  review_notes: string[]
}
export const previewSuiteImport = (file: File, platform: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('platform', platform)
  return api.post<SuiteImportDraft>('/suites/import/ai-preview', form, { timeout: 150000 }).then(r => r.data)
}
export const previewSuiteOrganization = (suiteId: string, platform: string) =>
  api.post<SuiteImportDraft>(`/suites/${suiteId}/organize/preview`, { platform }, { timeout: 150000 }).then(r => r.data)
export const confirmSuiteImport = (draft: SuiteImportDraft) =>
  api.post<Suite>('/suites/import/ai-confirm', draft).then(r => r.data)
export const fetchSuiteImportMetadata = (suiteId: string) =>
  api.get<{ parameters: string }[]>(`/suites/${suiteId}/cases`).then(r => {
    const parsed = JSON.parse(r.data[0]?.parameters || '{}')
    return parsed as { source_cases?: SuiteImportDraft['sources']; changes?: string[]; review_notes?: string[]; platform?: string }
  })

export interface TrendPoint {
  run_id: string
  created_at: string
  provider: string
  model: string
  passed: number
  failed: number
  errored: number
  total: number
  pass_rate: number
}

export const fetchTrends = (suiteId: string) =>
  api.get<TrendPoint[]>(`/suites/${suiteId}/trends`).then(r => r.data)

// ── Runs ─────────────────────────────────────────────────────────────────────

export const fetchRuns = (suiteId?: string) =>
  api.get<Run[]>('/runs', { params: suiteId ? { suite_id: suiteId } : {} }).then(r => r.data)
export const fetchRun = (id: string) => api.get<Run>(`/runs/${id}`).then(r => r.data)
export const fetchResults = (runId: string) =>
  api.get<TestResult[]>(`/runs/${runId}/results`).then(r => r.data)
export const startRun = (body: {
  suite_id: string
  device_id: string
  provider: string
  model: string
  test_account_id?: string
  max_steps?: number
  max_retries?: number
  isolated?: boolean
  mode?: string
}) => api.post<Run>('/runs', body).then(r => r.data)
export const quickRun = (body: {
  goal: string
  expected?: string
  device_id: string
  provider: string
  model: string
  test_account_id?: string
  max_steps?: number
  app_package?: string
  project_id?: string
  mode?: string
}) => api.post<Run>('/runs/quick', body).then(r => r.data)
export const cancelRun = (id: string) => api.post(`/runs/${id}/cancel`)
export const deleteRun = (id: string) => api.delete(`/runs/${id}`)
export const starResult = (runId: string, resultId: string) =>
  api.post<{ id: string; is_starred: boolean }>(`/runs/${runId}/results/${resultId}/star`).then(r => r.data)
export const fetchSteps = (runId: string, resultId: string) =>
  api.get<StepLog[]>(`/runs/${runId}/results/${resultId}/steps`).then(r => r.data)

// ── Run Comparison ───────────────────────────────────────────────────────────

export interface CompareItem {
  case_id: string
  path: string
  expected: string
  status_a: string | null
  status_b: string | null
  reason_a: string
  reason_b: string
  steps_a: number
  steps_b: number
}

export interface CompareOut {
  run_a: Run
  run_b: Run
  cases: CompareItem[]
  summary: { improved: number; regressed: number; unchanged: number }
}

export const compareRuns = (a: string, b: string) =>
  api.get<CompareOut>('/runs/compare', { params: { a, b } }).then(r => r.data)

// ── Run records (per node — memory hygiene) ───────────────────────────────────

export interface CaseResult {
  id: string
  run_id: string
  status: string
  reason: string
  steps: number
  total_tokens: number
  is_starred: boolean
  provider: string
  model: string
  created_at: string
  finished_at: string | null
}

// ── Step-tree nodes ───────────────────────────────────────────────────────────

export interface StepNode {
  id: string
  suite_id: string
  parent_id: string | null
  action: string
  expected: string
  order: number
  reversible: boolean
  loop_task: boolean
  ref_id?: string | null   // set on a live-link node
  ref_path?: string        // the source flow's path (for display)
}

export const fetchNodes = (suiteId: string) =>
  api.get<StepNode[]>(`/suites/${suiteId}/nodes`).then(r => r.data)

export const addNode = (
  suiteId: string,
  data: { parent_id: string | null; action: string; expected?: string; loop_task?: boolean },
) => api.post<StepNode>(`/suites/${suiteId}/nodes`, data).then(r => r.data)

export const updateNode = (
  suiteId: string,
  nodeId: string,
  data: { action?: string; expected?: string; loop_task?: boolean; reversible?: boolean },
) => api.put<StepNode>(`/suites/${suiteId}/nodes/${nodeId}`, data).then(r => r.data)

export const moveNode = (suiteId: string, nodeId: string, newParentId: string | null) =>
  api.post<StepNode>(`/suites/${suiteId}/nodes/${nodeId}/move`, { new_parent_id: newParentId }).then(r => r.data)

export const deleteNode = (suiteId: string, nodeId: string) =>
  api.delete(`/suites/${suiteId}/nodes/${nodeId}`)

export const runTree = (data: { suite_id: string; device_id: string; provider: string; model: string; max_steps: number; test_account_id?: string }) =>
  api.post<Run>('/runs/tree', data).then(r => r.data)

export const runNode = (data: { suite_id: string; device_id: string; node_id: string; provider: string; model: string; max_steps: number; test_account_id?: string }) =>
  api.post<Run>('/runs/node', data).then(r => r.data)

export interface NodeSearchHit {
  node_id: string
  suite_id: string
  suite_name: string
  path: string
  expected: string
}

export const searchNodes = (q: string) =>
  api.get<NodeSearchHit[]>('/nodes/search', { params: { q } }).then(r => r.data)

export const copyNode = (suiteId: string, sourceNodeId: string, parentId: string | null, link = false) =>
  api.post<StepNode[]>(`/suites/${suiteId}/nodes/copy`, { source_node_id: sourceNodeId, parent_id: parentId, link }).then(r => r.data)

export const fetchNodeResults = (nodeId: string) =>
  api.get<CaseResult[]>(`/nodes/${nodeId}/results`).then(r => r.data)

export const deleteNodeResult = (nodeId: string, resultId: string) =>
  api.delete(`/nodes/${nodeId}/results/${resultId}`)

export const purgeNodeResults = (nodeId: string, scope: 'all' | 'failed') =>
  api.delete<{ deleted: number }>(`/nodes/${nodeId}/results`, { params: { scope } }).then(r => r.data)

export interface NodeUsageRef {
  node_id: string
  suite_id: string
  suite_name: string
  path: string
  kind: 'link' | 'copy'
}

export const fetchNodeUsageCounts = () =>
  api.get<Record<string, { links: number; copies: number }>>('/nodes/usage').then(r => r.data)

export const fetchNodeUsage = (nodeId: string) =>
  api.get<NodeUsageRef[]>(`/nodes/${nodeId}/usage`).then(r => r.data)

export interface NodeListItem {
  node_id: string
  suite_id: string
  suite_name: string
  action: string
  expected: string
  path: string
  reuse_count: number
  child_count: number
  is_link: boolean
}

export interface NodeBrief { node_id: string; action: string; expected: string }

export interface NodeContext {
  node_id: string
  suite_id: string
  suite_name: string
  path: string
  parent: NodeBrief | null
  children: NodeBrief[]
  referrers: NodeUsageRef[]      // who reuses THIS node
  reuses: NodeUsageRef | null    // what THIS node reuses (its source)
}

export const fetchAllNodes = (params: { q?: string; suite_id?: string; include_derived?: boolean; offset?: number; limit?: number }) =>
  api.get<{ total: number; items: NodeListItem[] }>('/nodes/all', { params }).then(r => r.data)

export const fetchNodeContext = (nodeId: string) =>
  api.get<NodeContext>(`/nodes/${nodeId}/context`).then(r => r.data)

// ── Settings ─────────────────────────────────────────────────────────────────

export const fetchSettings = () => api.get<Settings>('/settings').then(r => r.data)
export const saveSettings = (data: Partial<Settings> & { clear_secrets?: string[] }) =>
  api.put<Settings>('/settings', data).then(r => r.data)

// ── Project Profiles (external KB / skills / source) ──────────────────────────

export interface Project {
  id: string
  name: string
  app_package: string
  app_ids: Record<string, string>
  kb_path: string
  source_root: string
  kb_search_cmd: string
}

export const fetchProjects = () => api.get<Project[]>('/projects').then(r => r.data)
export const createProject = (data: Partial<Project>) =>
  api.post<Project>('/projects', data).then(r => r.data)
export const updateProject = (id: string, data: Partial<Project>) =>
  api.put<Project>(`/projects/${id}`, data).then(r => r.data)
export const deleteProject = (id: string) => api.delete(`/projects/${id}`)

export const setSuiteAppPackage = (suiteId: string, app_package: string) =>
  api.patch<Suite>(`/suites/${suiteId}`, { app_package }).then(r => r.data)

export const setSuiteTag = (suiteId: string, tag: string) =>
  api.patch<Suite>(`/suites/${suiteId}`, { tag }).then(r => r.data)

// ── Self knowledge base (dictated notes, LLM-organized) ──────────────────────

export interface KnowledgeNote {
  id: string
  title: string
  body: string
  keywords: string[]
  aliases: string[]
  raw_input: string
  created_at: string
  updated_at: string
}

export const fetchKnowledge = (q?: string) =>
  api.get<KnowledgeNote[]>('/knowledge', { params: q ? { q } : {} }).then(r => r.data)
export const addKnowledge = (text: string) =>
  api.post<KnowledgeNote>('/knowledge', { text }).then(r => r.data)
export const updateKnowledge = (
  id: string,
  data: Partial<Pick<KnowledgeNote, 'title' | 'body' | 'keywords' | 'aliases'>>,
) => api.put<KnowledgeNote>(`/knowledge/${id}`, data).then(r => r.data)
export const deleteKnowledge = (id: string) => api.delete(`/knowledge/${id}`)

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string
  email: string
  name: string
  account_id: string
  account_name: string
  role: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

export interface MeResponse {
  user_id: string
  email: string
  name: string
  account_id: string
  account_name: string
  role: string
}

export interface ApiKeyInfo {
  id: string
  name: string
  key_prefix: string
  created_at: string
  last_used_at: string | null
}

export interface CreateApiKeyResponse {
  api_key: ApiKeyInfo
  full_key: string
}

export interface UserInfo {
  id: string
  email: string
  name: string
  role: string
  created_at: string
  last_login_at: string | null
}

export function getToken(): string | null {
  return localStorage.getItem('sab_token')
}

export function setToken(token: string) {
  localStorage.setItem('sab_token', token)
  api.defaults.headers.common['Authorization'] = `Bearer ${token}`
}

export function clearToken() {
  localStorage.removeItem('sab_token')
  delete api.defaults.headers.common['Authorization']
}

// Initialize auth header from stored token
const storedToken = getToken()
if (storedToken) {
  api.defaults.headers.common['Authorization'] = `Bearer ${storedToken}`
}

export const register = (data: { account_name: string; email: string; password: string; name: string }) =>
  api.post<AuthResponse>('/auth/register', data).then(r => r.data)

export const login = (data: { email: string; password: string }) =>
  api.post<AuthResponse>('/auth/login', data).then(r => r.data)

export const fetchMe = () =>
  api.get<MeResponse>('/auth/me').then(r => r.data)

export const fetchApiKeys = () =>
  api.get<ApiKeyInfo[]>('/account/api-keys').then(r => r.data)

export const createApiKey = (name: string) =>
  api.post<CreateApiKeyResponse>('/account/api-keys', { name }).then(r => r.data)

export const deleteApiKey = (id: string) =>
  api.delete(`/account/api-keys/${id}`)

export const fetchAccountUsers = () =>
  api.get<UserInfo[]>('/account/users').then(r => r.data)

export const createAccountUser = (data: { email: string; password: string; name: string; role: string }) =>
  api.post<UserInfo>('/account/users', data).then(r => r.data)

export const deleteAccountUser = (id: string) =>
  api.delete(`/account/users/${id}`)

export const setSuiteProject = (suiteId: string, project_id: string) =>
  api.patch<Suite>(`/suites/${suiteId}`, { project_id }).then(r => r.data)

export interface ProjectTestAccount {
  id: string
  project_id: string
  name: string
  environment: string
  platforms: string[]
  login_mode: 'phone' | 'email' | 'manual' | 'password'
  username_masked: string
  password_configured: boolean
  verification_code_configured: boolean
  notes: string
  is_default: boolean
}
export type TestAccountInput = Pick<ProjectTestAccount, 'name' | 'environment' | 'platforms' | 'notes' | 'is_default'> & { login_mode: 'phone' | 'email' | 'password'; username?: string; password?: string; verification_code?: string }
export const fetchTestAccounts = (projectId: string) => api.get<ProjectTestAccount[]>(`/projects/${projectId}/test-accounts`).then(r => r.data)
export const saveTestAccount = (projectId: string, body: TestAccountInput, id?: string) => id
  ? api.put<ProjectTestAccount>(`/projects/${projectId}/test-accounts/${id}`, body).then(r => r.data)
  : api.post<ProjectTestAccount>(`/projects/${projectId}/test-accounts`, body).then(r => r.data)
export const deleteTestAccount = (projectId: string, id: string) => api.delete(`/projects/${projectId}/test-accounts/${id}`)
