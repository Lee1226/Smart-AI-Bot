import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchTestAccounts } from '../lib/api'

export default function TestAccountSelect({ projectId, value, onChange, platform }: { projectId?: string; value: string; onChange: (value: string) => void; platform?: string }) {
  const { data: accounts = [], isError } = useQuery({ queryKey: ['test-accounts', projectId], queryFn: () => fetchTestAccounts(projectId!), enabled: !!projectId })
  useEffect(() => { onChange('') }, [projectId])
  if (!projectId) return null
  return <div>
    <label className="block text-sm font-medium mb-1">测试账号</label>
    <select className="w-full border rounded px-2 py-1.5 text-sm" value={value} onChange={e => onChange(e.target.value)}>
      <option value="">项目默认账号{accounts.find(a => a.is_default) ? ` · ${accounts.find(a => a.is_default)!.name}` : '（未配置）'}</option>
      <option value="-">不使用测试账号</option>
      {accounts.map(a => <option key={a.id} value={a.id} disabled={!!platform && !a.platforms.includes(platform)}>{a.name} · {a.environment} · {a.username_masked}{platform && !a.platforms.includes(platform) ? '（平台不适用）' : ''}</option>)}
    </select>
    <p className="text-xs text-gray-400 mt-1">已登录时先核对身份；需要验证码时请人工登录。前置条件可写在用例中。</p>
    {isError && <p className="text-xs text-red-600">测试账号加载失败</p>}
  </div>
}
