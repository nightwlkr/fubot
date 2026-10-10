import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { Button } from '../components/ui/Button'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'

export default function Login() {
  const navigate = useNavigate()
  const { login, isLoading, error } = useAuthStore()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ username?: string; password?: string }>({})

  const validate = () => {
    const errors: { username?: string; password?: string } = {}
    if (!username.trim()) errors.username = 'Username is required'
    if (!password) errors.password = 'Password is required'
    if (password && password.length < 4) errors.password = 'Password too short'
    setFieldErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch {
      // Error is set in store
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo / Branding */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-mono font-bold text-primary tracking-tight">
            FUBOT
          </h1>
          <p className="text-muted mt-2 text-sm">
            Automated Binance Futures Trading
          </p>
        </div>

        <Card className="p-8">
          <CardHeader className="mb-6">
            <CardTitle>Sign In</CardTitle>
          </CardHeader>

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            {/* Username */}
            <div>
              <label
                htmlFor="username"
                className="block text-xs font-medium text-muted uppercase tracking-wide mb-1.5"
              >
                Username
              </label>
              <input
                id="username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => {
                  setUsername(e.target.value)
                  if (fieldErrors.username) setFieldErrors((f) => ({ ...f, username: undefined }))
                }}
                className={`w-full bg-surface2 border rounded-md px-3 py-2 text-sm text-text
                  placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-primary/50
                  ${fieldErrors.username ? 'border-danger' : 'border-border'}`}
                placeholder="admin"
                disabled={isLoading}
              />
              {fieldErrors.username && (
                <p className="text-danger text-xs mt-1">{fieldErrors.username}</p>
              )}
            </div>

            {/* Password */}
            <div>
              <label
                htmlFor="password"
                className="block text-xs font-medium text-muted uppercase tracking-wide mb-1.5"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  if (fieldErrors.password) setFieldErrors((f) => ({ ...f, password: undefined }))
                }}
                className={`w-full bg-surface2 border rounded-md px-3 py-2 text-sm text-text
                  placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-primary/50
                  ${fieldErrors.password ? 'border-danger' : 'border-border'}`}
                placeholder="••••••••"
                disabled={isLoading}
              />
              {fieldErrors.password && (
                <p className="text-danger text-xs mt-1">{fieldErrors.password}</p>
              )}
            </div>

            {/* API Error */}
            {error && (
              <div className="bg-danger/10 border border-danger/30 rounded-md px-3 py-2">
                <p className="text-danger text-xs">{error}</p>
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              className="w-full"
              size="lg"
              isLoading={isLoading}
              disabled={isLoading}
            >
              {isLoading ? 'Signing in…' : 'Sign In'}
            </Button>
          </form>
        </Card>

        <p className="text-center text-muted text-xs mt-6">
          FUBOT — Automated Binance Futures Trading Bot
        </p>
      </div>
    </div>
  )
}
