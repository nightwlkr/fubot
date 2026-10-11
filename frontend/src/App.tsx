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

function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore()
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

// Simple polling hook for bot status
function useBotPolling(intervalMs = 10000) {
  const { fetchOverview } = useDashboardStore()
  useEffect(() => {
    fetchOverview()
    const id = setInterval(fetchOverview, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
}


function ProtectedLayoutWithPolling({ children }: { children: React.ReactNode }) {
  const { user, isRestoring } = useAuthStore()
  const fetchOverview = useDashboardStore(state => state.fetchOverview)

  // Poll only AFTER the session is restored and the user is known — firing
  // fetchOverview while isRestoring is true hits the API before auth is
  // confirmed, which can trigger a spurious 401 → hard redirect to /login.
  useEffect(() => {
    if (isRestoring || !user) return
    fetchOverview()
    const id = setInterval(fetchOverview, 10000)
    return () => clearInterval(id)
  }, [fetchOverview, isRestoring, user])

  if (isRestoring) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center text-muted">
        Restoring session…
      </div>
    )
  }
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

export default function App() {
  const { user, isRestoring, checkAuth } = useAuthStore()

  // Restore the session from the fubot_token cookie on page load — without
  // this every refresh bounced to /login.
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // Wire WebSocket messages: server alerts surface as toasts; data events
  // refresh the dashboard store (throttled so bursts don't stampede the API).
  // NOTE: both effects must stay ABOVE any conditional return — a hook after
  // an early return changes the hook count between renders (React #310).
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

  if (isRestoring) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center text-muted">
        Restoring session…
      </div>
    )
  }

  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Routes>
        <Route
          path="/login"
          element={
            isRestoring ? (
              <div className="min-h-screen bg-background flex items-center justify-center text-muted">
                Restoring session…
              </div>
            ) : user ? (
              <Navigate to="/" replace />
            ) : (
              <Login />
            )
          }
        />
        <Route
          path="/"
          element={<ProtectedLayoutWithPolling><Overview /></ProtectedLayoutWithPolling>}
        />
        <Route
          path="/trades"
          element={<ProtectedLayoutWithPolling><TradeLog /></ProtectedLayoutWithPolling>}
        />
        <Route
          path="/scanner"
          element={<ProtectedLayoutWithPolling><Scanner /></ProtectedLayoutWithPolling>}
        />
        <Route
          path="/performance"
          element={<ProtectedLayoutWithPolling><Performance /></ProtectedLayoutWithPolling>}
        />
        <Route
          path="/settings"
          element={<ProtectedLayoutWithPolling><Settings /></ProtectedLayoutWithPolling>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <ToastContainer />
      </ErrorBoundary>
    </BrowserRouter>
  )
}
