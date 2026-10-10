# Phase 5 — Step 2: Login Page + JWT Auth Flow
## FUBOT: Authentication UI and Token Persistence

---

## Objective
Build the Login page with form validation, JWT authentication via the backend API, token persistence in httpOnly cookies, and proper error handling.

---

## Context
- **Depends on**: Phase 5 Step 1 (frontend scaffold complete)
- **Backend endpoint**: `POST /api/auth/login` → `{ token, user }`
- **Token storage**: httpOnly cookie set by backend (via `withCredentials: true`)
- **Post-login redirect**: `/`

---

## Login Page

### `frontend/src/pages/Login.tsx`

```tsx
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
```

---

## Auth Check on App Load

Update `frontend/src/main.tsx` to check auth on startup:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles/globals.css'
import { useAuthStore } from './store/authStore'
import { useEffect } = 'react'

// Check auth on load
useAuthStore.getState().checkAuth()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

---

## Protected Route Wrapper

The `ProtectedLayout` in `App.tsx` already handles redirecting unauthenticated users to `/login`. Ensure `useAuthStore.checkAuth()` is called before the redirect logic runs — it is called in `main.tsx`.

---

## WebSocket Auto-Connect on Auth

When a user logs in successfully, connect the WebSocket:

```tsx
// In the login success handler (after await login(...) ):

// Connect WebSocket after successful login
import { wsManager } from '../api/websocket'
wsManager.connect()

navigate('/', { replace: true })
```

Also add `wsManager.connect()` to the `checkAuth` success path:

```tsx
// Updated authStore checkAuth success:
if (res.data.user) {
  wsManager.connect()
  set({ user: res.data.user, token })
}
```

---

## Logout Handler

In `Sidebar.tsx` or `Header.tsx`, wire up the logout button:

```tsx
import { useAuthStore } from '../../store/authStore'
import { wsManager } from '../../api/websocket'
import { useNavigate } from 'react-router-dom'

const { logout } = useAuthStore()
const navigate = useNavigate()

const handleLogout = async () => {
  wsManager.disconnect()
  await logout()
  navigate('/login')
}
```

---

## HTTP Interceptor for 401

The `api/client.ts` already has a 401 interceptor that removes the token cookie and redirects to `/login`. This covers the case where the JWT expires.

---

## Deliverables

1. **`frontend/src/pages/Login.tsx`** — Full login form with validation and error display
2. **`frontend/src/main.tsx`** — Auth check on app load
3. **`frontend/src/api/client.ts`** — 401 interceptor (already written in Step 1)
4. **WebSocket auto-connect** — On login success and auth check success
5. **Logout** — Disconnects WebSocket, clears cookie, redirects to login

---

## Validation

```bash
# 1. Start the backend (from backend/ directory)
cd /home/vick/fubot/backend
uvicorn main:app --host 0.0.0.0 --port 8000

# 2. Start the frontend dev server
cd /home/vick/fubot/frontend
npm run dev

# 3. Visit http://localhost:5173
#    - Should redirect to /login
#    - Enter correct credentials → redirect to Overview
#    - Enter wrong credentials → error displayed
#    - Refresh page → stays on Overview (auth persists)
#    - Token cookie visible in DevTools Application tab
```

---

## Next Step
Phase 5 Step 3: Sidebar Navigation + Routing

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
