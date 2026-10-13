import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchTestAccounts, saveTestAccount, deleteTestAccount, ProjectTestAccount, TestAccountInput } from '../lib/api'

function AccountForm({ projectId, account, close }: { projectId: string; account?: ProjectTestAccount; close: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<TestAccountInput>({
    name: account?.name || '', environment: account?.environment || '测试服',
    platforms: account?.platforms || ['android', 'harmony'], login_mode: account?.login_mode === 'manual' ? 'phone' : account?.login_mode || 'phone',
    notes: account?.notes || '', is_default: account?.is_default || false,
  })
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [verificationCode, setVerificationCode] = useState('')
  const mut = useMutation({
    mutationFn: () => saveTestAccount(projectId, { ...form, username: username || undefined, password: ['password', 'email'].includes(form.login_mode) ? password || undefined : undefined, verification_code: form.login_mode === 'phone' ? verificationCode || undefined : undefined }, account?.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['test-accounts', projectId] }); setUsername(''); setPassword(''); setVerificationCode(''); close() },
  })
  return <form autoComplete="off" className="border rounded bg-gray-50 p-3 space-y-3" onSubmit={e => { e.preventDefault(); mut.mutate() }}>
    {account?.login_mode === 'manual' && <p className="text-xs text-amber-700">旧配置需要更新：请选择实际登录方式并保存；原密码不会自动作为验证码使用。</p>}
    <div className="grid sm:grid-cols-2 gap-3">
      <label className="text-xs">账号名称<input required maxLength={100} className="block border rounded px-2 py-1 w-full" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="普通用户 / 超级用户" /></label>
      <label className="text-xs">环境<input required className="block border rounded px-2 py-1 w-full" value={form.environment} onChange={e => setForm({ ...form, environment: e.target.value })} /></label>
      <label className="text-xs">{form.login_mode === 'phone' ? '手机号' : form.login_mode === 'email' ? '邮箱' : '登录账号'}<input autoComplete="off" name="test-login-id" required={!account} className="block border rounded px-2 py-1 w-full" value={username} onChange={e => setUsername(e.target.value)} type={form.login_mode === 'email' ? 'email' : form.login_mode === 'phone' ? 'tel' : 'text'} placeholder={account ? `保持原账号 ${account.username_masked}（填入则替换）` : form.login_mode === 'email' ? 'user@example.com' : form.login_mode === 'phone' ? '手机号（可含国家区号）' : '手机号 / 邮箱 / 用户名'} /></label>
      {['password', 'email'].includes(form.login_mode) && <label className="text-xs">密码<input type="password" autoComplete="new-password" name="test-login-password" required={!account?.password_configured} className="block border rounded px-2 py-1 w-full" value={password} onChange={e => setPassword(e.target.value)} placeholder={account?.password_configured ? '已配置，留空保持原密码' : '请输入密码'} /></label>}
      {form.login_mode === 'phone' && <label className="text-xs">验证码<input type="password" autoComplete="off" name="test-login-code" className="block border rounded px-2 py-1 w-full" value={verificationCode} onChange={e => setVerificationCode(e.target.value)} placeholder={account?.verification_code_configured ? '已配置，留空保持原验证码' : '请输入可用验证码'} /><span className="text-gray-500">支持测试环境固定验证码；短信验证码过期后需更新。</span></label>}
      <label className="text-xs">登录方式<select className="block border rounded px-2 py-1 w-full" value={form.login_mode} onChange={e => setForm({ ...form, login_mode: e.target.value as TestAccountInput['login_mode'] })}>
        <option value="phone">手机号验证码登录</option><option value="email">邮箱密码登录</option><option value="password">密码登录（可选）</option>
      </select></label>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={form.is_default} onChange={e => setForm({ ...form, is_default: e.target.checked })} />项目默认账号</label>
    </div>
    <div className="flex gap-3 text-xs">适用平台{['android', 'harmony', 'ios'].map(p => <label key={p} className="flex gap-1 items-center"><input type="checkbox" checked={form.platforms.includes(p)} onChange={e => setForm({ ...form, platforms: e.target.checked ? [...form.platforms, p] : form.platforms.filter(v => v !== p) })} />{p}</label>)}</div>
    <label className="block text-xs">使用说明 / 前置条件<textarea className="block w-full border rounded px-2 py-1 mt-1" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} placeholder="已开通超级用户；不要在此填写密码或验证码" /></label>
    {mut.isError && <p role="alert" className="text-red-600 text-xs">保存失败，请检查账号、密码和平台；运行中的账号暂不可修改。</p>}
    <div className="flex gap-2"><button type="submit" disabled={mut.isPending || !form.platforms.length} className="bg-primary text-white rounded px-3 py-1 text-xs">保存测试账号</button><button type="button" onClick={close} className="border rounded px-3 py-1 text-xs">取消</button></div>
  </form>
}

export default function ProjectTestAccounts({ projectId }: { projectId: string }) {
  const qc = useQueryClient()
  const { data: accounts = [], isError } = useQuery({ queryKey: ['test-accounts', projectId], queryFn: () => fetchTestAccounts(projectId) })
  const [editing, setEditing] = useState<ProjectTestAccount | 'new' | null>(null)
  const del = useMutation({ mutationFn: (id: string) => deleteTestAccount(projectId, id), onSuccess: () => qc.invalidateQueries({ queryKey: ['test-accounts', projectId] }) })
  return <div className="border-t mt-3 pt-3 space-y-2">
    <div className="flex justify-between"><h3 className="text-sm font-medium">测试账号</h3><button type="button" className="text-xs text-primary" onClick={() => setEditing('new')}>+ 添加测试账号</button></div>
    <p className="text-xs text-gray-500">登录信息加密保存。用例写账号角色和登录前置条件，运行时选择账号；AI 会填写配置的手机号与验证码，或邮箱与密码。</p>
    {isError && <p className="text-xs text-red-600">测试账号加载失败</p>}
    {accounts.map(a => <div key={a.id} className="flex justify-between gap-2 items-center border rounded p-2 text-xs">
      <div><b>{a.name}</b> {a.is_default && '· 默认'}<div className="text-gray-500">{a.environment} · {a.platforms.join(' / ')} · {a.username_masked} · {a.login_mode === 'email' ? '邮箱密码登录' : a.login_mode === 'phone' ? '手机号验证码登录' : a.login_mode === 'password' ? '密码登录' : '待更新登录方式'}</div></div>
      <div className="flex gap-2"><button type="button" onClick={() => setEditing(a)} className="text-primary">编辑</button><button type="button" disabled={del.isPending} onClick={() => { if (confirm('删除此测试账号的登录信息？历史运行保留账号名称。')) del.mutate(a.id) }} className="text-red-600">删除</button></div>
    </div>)}
    {del.isError && <p role="alert" className="text-red-600 text-xs">删除失败，账号可能正在运行中。</p>}
    {editing && <AccountForm key={editing === 'new' ? 'new' : editing.id} projectId={projectId} account={editing === 'new' ? undefined : editing} close={() => setEditing(null)} />}
  </div>
}
