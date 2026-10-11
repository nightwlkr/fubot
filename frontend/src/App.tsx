import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect } from 'react'
import { useAuthStore } from './store/authStore'
import { useDashboardStore } from './store/dashboardStore'
import { useAlertStore } from './store/alertStore'
import { wsManager } from './api/websocket'
import ErrorBoundary from './components/ErrorBoundary'
import ToastContainer from './components/ToastContainer'
import Login from './pages/Login'
import Overview from './pages/Overview'
import TradeLog from './pages/TradeLog'
import Scanner from './pages/Scanner'
import Performance from './pages/Performance'
import Settings from './pages/Settings'
import Sidebar from './components/layout/Sidebar'
import Header from './components/layout/Header'

function RestoreScreen() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center text-muted">
      Restoring session…
    </div>
  )
}

/**
 * Gate for authenticated pages. While the session is still being restored
 * (isRestoring) it shows the restore screen INSTEAD of redirecting — the old
 * bug redirected to /login before checkAuth resolved, bouncing a valid session
 * to the login page on every refresh.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, isRestoring } = useAuthStore()
  if (isRestoring) return <RestoreScreen />
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  )
}

/**
 * Polling for the dashboard. Lives inside RequireAuth so it only runs once
 * the user is confirmed — never before auth resolves.
 */
function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore()
  const fetchOverview = useDashboardStore(state => state.fetchOverview)

  useEffect(() => {
    if (!user) return
    fetchOverview()
    const id = setInterval(fetchOverview, 10000)
    return () => clearInterval(id)
  }, [fetchOverview, user])

  return <>{children}</>
}

/** Login route: while restoring show the gate; if already authed go home. */
function LoginRoute() {
  const { user, isRestoring } = useAuthStore()
  if (isRestoring) return <RestoreScreen />
  if (user) return <Navigate to="/" replace />
  return <Login />
}

export default function App() {
  const { checkAuth } = useAuthStore()

  // Restore the session from localStorage / the httpOnly cookie on page load.
  // Runs once; the gate above keeps the UI in "Restoring" until it resolves.
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // Wire WebSocket messages: server alerts surface as toasts; data events
  // refresh the dashboard store (throttled so bursts don't stampede the API).
  useEffect(() => {
    let lastRefresh = 0
    const off = wsManager.onMessage((msg) => {
      if (msg.type === 'alert') {
        const data = (msg.data ?? {}) as { level?: string; message?: string }
        useAlertStore.getState().push(data.level ?? 'INFO', data.message ?? '')
        return
      }
      if (['bot_status', 'pnl_update', 'position_update', 'position_closed',
           'signal_update', 'equity_snapshot'].includes(msg.type)) {
        const now = Date.now()
        if (now - lastRefresh > 2000) {
          lastRefresh = now
          useDashboardStore.getState().fetchOverview()
        }
      }
    })
    return () => { off() }
  }, [])

  // BrowserRouter is ALWAYS mounted — never conditionally. Mounting it only
  // after the restore finished made the router read window.location at a
  // moment that raced the store, which is what bounced refreshes to /login.
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Routes>
          <Route path="/login" element={<LoginRoute />} />
          <Route path="/" element={<RequireAuth><DashboardLayout><Overview /></DashboardLayout></RequireAuth>} />
          <Route path="/trades" element={<RequireAuth><TradeLog /></RequireAuth>} />
          <Route path="/scanner" element={<RequireAuth><Scanner /></RequireAuth>} />
          <Route path="/performance" element={<RequireAuth><Performance /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <ToastContainer />
      </ErrorBoundary>
    </BrowserRouter>
  )
}
