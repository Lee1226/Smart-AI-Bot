import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchAuthConfig, register, setToken } from '../lib/api'
import { useT } from '../lib/i18n'

export default function Register() {
  const t = useT()
  const navigate = useNavigate()
  const { data: authConfig, isLoading } = useQuery({ queryKey: ['auth-config'], queryFn: fetchAuthConfig })
  const [accountName, setAccountName] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const regMut = useMutation({
    mutationFn: () => register({ account_name: accountName, email, password, name }),
    onSuccess: (data) => {
      setToken(data.access_token)
      navigate('/')
    },
    onError: (e: any) => {
      const msg = e?.response?.data?.detail || t('注册失败', 'Registration failed')
      setError(msg)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (password.length < 10) {
      setError(t('密码至少 10 位', 'Password must be at least 10 characters'))
      return
    }
    regMut.mutate()
  }

  if (!isLoading && authConfig && !authConfig.registration_enabled) {
    return (
      <div className="max-w-sm mx-auto mt-20 text-center">
        <h1 className="text-2xl font-bold mb-3">{t('公开注册已关闭', 'Public registration is closed')}</h1>
        <p className="text-sm text-gray-500 mb-4">{t('请联系管理员邀请你加入。', 'Ask an administrator to invite you.')}</p>
        <Link to="/login" className="text-primary hover:underline">{t('返回登录', 'Back to login')}</Link>
      </div>
    )
  }

  return (
    <div className="max-w-sm mx-auto mt-20">
      <h1 className="text-2xl font-bold mb-6 text-center">{t('注册', 'Register')}</h1>
      <form onSubmit={handleSubmit} className="bg-white border rounded-lg p-6 shadow-sm space-y-4">
        {error && <div className="text-red-500 text-sm">{error}</div>}
        <div>
          <label className="block text-sm font-medium mb-1">{t('团队/账号名', 'Team / Account name')}</label>
          <input
            type="text"
            className="w-full border rounded px-3 py-2 text-sm"
            value={accountName}
            onChange={e => setAccountName(e.target.value)}
            required
            autoFocus
            placeholder="MyTeam"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Email</label>
          <input
            type="email"
            className="w-full border rounded px-3 py-2 text-sm"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">{t('显示名称', 'Display name')}</label>
          <input
            type="text"
            className="w-full border rounded px-3 py-2 text-sm"
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">{t('密码', 'Password')}</label>
          <input
            type="password"
            className="w-full border rounded px-3 py-2 text-sm"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            minLength={10}
          />
        </div>
        <button
          type="submit"
          className="w-full bg-primary text-white py-2 rounded font-medium hover:bg-primary-deep disabled:opacity-50"
          disabled={regMut.isPending}
        >
          {regMut.isPending ? t('注册中…', 'Registering…') : t('注册', 'Register')}
        </button>
        <p className="text-sm text-center text-gray-500">
          {t('已有账号？', 'Already have an account?')}{' '}
          <Link to="/login" className="text-primary hover:underline">{t('登录', 'Login')}</Link>
        </p>
      </form>
    </div>
  )
}
