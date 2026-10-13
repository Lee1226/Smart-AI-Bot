import { useState } from 'react'
import { useNavigate, Link, Navigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { login, setToken, getToken, fetchAuthConfig } from '../lib/api'
import { useT } from '../lib/i18n'

export default function Login() {
  const t = useT()
  const navigate = useNavigate()
  const { data: authConfig } = useQuery({ queryKey: ['auth-config'], queryFn: fetchAuthConfig })

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const loginMut = useMutation({
    mutationFn: () => login({ email, password }),
    onSuccess: (data) => {
      setToken(data.access_token)
      navigate('/')
    },
    onError: () => setError(t('邮箱或密码错误', 'Invalid email or password')),
  })

  // Already have a long-lived token → skip login page
  if (getToken()) {
    return <Navigate to="/" replace />
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    loginMut.mutate()
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas-soft">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <img src="/favicon.png" alt="" className="w-12 h-12 mx-auto mb-3 rounded-lg" />
          <h1 className="text-xl font-bold">smart-ai-bot</h1>
        </div>
        <form onSubmit={handleSubmit} className="bg-white border rounded-lg p-6 shadow-sm space-y-4">
          {error && <div className="text-red-500 text-sm text-center">{error}</div>}
          <div>
            <label className="block text-sm font-medium mb-1">Email</label>
            <input
              type="email"
              className="w-full border rounded px-3 py-2 text-sm"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              autoFocus
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
            />
          </div>
          <button
            type="submit"
            className="w-full bg-primary text-white py-2 rounded font-medium hover:bg-primary-deep disabled:opacity-50"
            disabled={loginMut.isPending}
          >
            {loginMut.isPending ? t('登录中…', 'Logging in…') : t('登录', 'Login')}
          </button>
          {authConfig?.registration_enabled && (
            <p className="text-sm text-center text-gray-500">
              {t('还没有账号？', "Don't have an account?")}{' '}
              <Link to="/register" className="text-primary hover:underline">{t('注册', 'Register')}</Link>
            </p>
          )}
        </form>
      </div>
    </div>
  )
}
