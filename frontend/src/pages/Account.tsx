import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchMe, fetchApiKeys, createApiKey, deleteApiKey } from '../lib/api'
import { useT } from '../lib/i18n'

// 后端返回 naive UTC（无时区），JS 会当本地时间解析 → 补 Z 强制按 UTC 解析再转本地
function fmtTime(s: string): string {
  if (!s) return ''
  const iso = s.includes('Z') || s.includes('+') ? s : s.replace(' ', 'T') + 'Z'
  return new Date(iso).toLocaleString()
}

export default function Account() {
  const t = useT()
  const qc = useQueryClient()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const { data: keys = [] } = useQuery({ queryKey: ['api-keys'], queryFn: fetchApiKeys })
  const [newName, setNewName] = useState('')
  const [fullKey, setFullKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isAdmin = me?.role === 'owner' || me?.role === 'admin'

  const createMut = useMutation({
    mutationFn: () => createApiKey(newName.trim() || 'API Key'),
    onSuccess: (data) => {
      setFullKey(data.full_key)
      setNewName('')
      setError(null)
      qc.invalidateQueries({ queryKey: ['api-keys'] })
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : '创建失败，请检查账号权限或重新登录')
    },
  })

  const delMut = useMutation({
    mutationFn: (id: string) => deleteApiKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }),
    onError: () => setError('删除失败，请检查账号权限'),
  })

  const copyKey = async () => {
    if (!fullKey) return
    try {
      await navigator.clipboard.writeText(fullKey)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard may be blocked on http */
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">{t('账号 / API Key', 'Account / API Keys')}</h1>

      <div className="bg-white border rounded-lg p-6 shadow-sm space-y-5">
        {/* 账号信息 */}
        <div>
          <p className="text-sm text-gray-600">
            {t('账号', 'Account')}: <b>{me?.account_name}</b> · {me?.role}
          </p>
          <p className="text-xs text-gray-400 mt-1">
            {t('API Key 用于 sab CLI / 脚本 / CI 调用 SAB 接口。', 'API Keys are used by the sab CLI / scripts / CI to call SAB APIs.')}
          </p>
        </div>

        {/* 创建 API Key */}
        <div className="border-t pt-4">
          <h3 className="text-sm font-medium mb-2">{t('创建 API Key', 'Create API Key')}</h3>
          {!isAdmin ? (
            <p className="text-xs text-amber-600">
              {t('需要 owner / admin 角色才能创建 API Key。', 'Only owner / admin can create API keys.')}
            </p>
          ) : (
            <>
              <div className="flex gap-2">
                <input
                  className="flex-1 border rounded px-3 py-1.5 text-sm"
                  placeholder={t('名称（可选）', 'Name (optional)')}
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                />
                <button
                  className="px-4 py-1.5 bg-primary text-white text-sm rounded hover:bg-primary-deep disabled:opacity-50"
                  disabled={createMut.isPending}
                  onClick={() => createMut.mutate()}
                >
                  {createMut.isPending ? t('创建中…', 'Creating…') : t('+ 创建', '+ Create')}
                </button>
              </div>

              {fullKey && (
                <div className="mt-3 bg-amber-50 border border-amber-200 rounded p-3">
                  <p className="text-xs text-amber-700 mb-1">
                    {t('⚠️ 完整 Key 只显示这一次，请立即复制保存。', '⚠️ The full key is shown only once. Copy it now.')}
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 bg-white border rounded px-2 py-1 text-xs font-mono break-all">{fullKey}</code>
                    <button
                      className="px-3 py-1 border border-gray-300 text-xs rounded hover:bg-gray-50 whitespace-nowrap"
                      onClick={copyKey}
                    >
                      {copied ? t('✓ 已复制', '✓ Copied') : t('复制', 'Copy')}
                    </button>
                  </div>
                </div>
              )}

              {error && <p className="text-xs text-red-500 mt-2">{error}</p>}
            </>
          )}
        </div>

        {/* API Key 列表 */}
        <div className="border-t pt-4">
          <h3 className="text-sm font-medium mb-2">{t('已有 API Key', 'Existing API Keys')}</h3>
          {keys.length === 0 ? (
            <p className="text-sm text-gray-400">{t('还没有 API Key。', 'No API keys yet.')}</p>
          ) : (
            <ul className="divide-y">
              {keys.map(k => (
                <li key={k.id} className="flex items-center justify-between py-2 gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-mono">{k.key_prefix}</p>
                    <p className="text-xs text-gray-400">
                      {k.name} · {fmtTime(k.created_at)}
                    </p>
                  </div>
                  {isAdmin && (
                    <button
                      className="text-xs text-red-500 hover:text-red-700 whitespace-nowrap"
                      onClick={() => {
                        if (confirm(t('删除这个 API Key？', 'Delete this API key?'))) delMut.mutate(k.id)
                      }}
                    >
                      {t('删除', 'Delete')}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
