import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './store/authStore'
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

// Import dashboard store for polling
import { useDashboardStore } from './store/dashboardStore'
import { useEffect } from 'react'

function ProtectedLayoutWithPolling({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore()
  const fetchOverview = useDashboardStore(state => state.fetchOverview)

  useEffect(() => {
    fetchOverview()
    const id = setInterval(fetchOverview, 10000)
    return () => clearInterval(id)
  }, [fetchOverview])

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
  const { user } = useAuthStore()

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={user ? <Navigate to="/" replace /> : <Login />}
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
    </BrowserRouter>
  )
}
