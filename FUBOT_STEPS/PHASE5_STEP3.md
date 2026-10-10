# Phase 5 — Step 3: Sidebar Navigation + Header
## FUBOT: Layout Shell with Navigation

---

## Objective
Build the application layout shell: a fixed sidebar with navigation links and an app header showing bot status, live P&L, and user menu.

---

## Context
- **Depends on**: Phase 5 Step 2 (auth works)
- **Navigation**: 5 pages — Overview, Trade Log, Scanner, Performance, Settings
- **Active state**: Highlight current route in sidebar

---

## Sidebar Component

### `frontend/src/components/layout/Sidebar.tsx`

```tsx
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import { wsManager } from '../../api/websocket'
import {
  LayoutDashboard,
  ScrollText,
  Radio,
  BarChart3,
  Settings,
  LogOut,
  Bot,
} from 'lucide-react'
import { clsx } from 'clsx'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Overview', exact: true },
  { to: '/trades', icon: ScrollText, label: 'Trade Log' },
  { to: '/scanner', icon: Radio, label: 'Scanner' },
  { to: '/performance', icon: BarChart3, label: 'Performance' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Sidebar() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = async () => {
    wsManager.disconnect()
    await logout()
    navigate('/login')
  }

  return (
    <aside className="w-56 bg-surface border-r border-border flex flex-col h-full shrink-0">
      {/* Logo */}
      <div className="p-5 border-b border-border">
        <div className="flex items-center gap-2">
          <Bot className="w-6 h-6 text-primary" />
          <span className="text-lg font-mono font-bold text-primary tracking-tight">
            FUBOT
          </span>
        </div>
        <p className="text-muted text-xs mt-1">Binance Futures Bot</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-1">
        {navItems.map(({ to, icon: Icon, label, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-muted hover:bg-surface2 hover:text-text'
              )
            }
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* User / Logout */}
      <div className="p-3 border-t border-border">
        <div className="flex items-center gap-3 px-3 py-2">
          <div className="w-8 h-8 rounded-full bg-surface2 flex items-center justify-center text-xs font-mono text-muted">
            {user?.username?.charAt(0).toUpperCase() || '?'}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{user?.username}</p>
            <p className="text-xs text-muted truncate">Admin</p>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 w-full px-3 py-2 mt-1 rounded-md text-sm text-muted hover:bg-surface2 hover:text-danger transition-colors"
        >
          <LogOut className="w-4 h-4 shrink-0" />
          Sign Out
        </button>
      </div>
    </aside>
  )
}
```

---

## Header Component

### `frontend/src/components/layout/Header.tsx`

```tsx
import { useEffect, useState } from 'react'
import { useDashboardStore } from '../../store/dashboardStore'
import { Badge } from '../ui/Badge'
import { api } from '../../api/client'
import { ENDPOINTS } from '../../api/endpoints'
import { clsx } from 'clsx'

export default function Header() {
  const { botStatus, todayStats } = useDashboardStore()
  const [now, setNow] = useState(new Date())

  // Update clock every second
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const isRunning = botStatus?.status === 'running'
  const mode = botStatus?.mode || 'unknown'

  const pnlR = todayStats?.pnl_r ?? 0
  const pnlUsdt = todayStats?.pnl_usdt ?? 0

  return (
    <header className="h-14 bg-surface border-b border-border flex items-center justify-between px-6 shrink-0">
      {/* Left: Bot Status */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div
            className={clsx(
              'w-2 h-2 rounded-full',
              isRunning ? 'bg-success animate-pulse' : 'bg-muted'
            )}
          />
          <span className="text-sm font-medium">
            {isRunning ? 'Bot Running' : 'Bot Stopped'}
          </span>
        </div>

        <Badge variant={mode === 'paper' ? 'warning' : 'danger'}>
          {mode.toUpperCase()}
        </Badge>

        {botStatus && (
          <span className="text-xs text-muted font-mono hidden sm:block">
            SCAN #{botStatus.scan_count.toLocaleString()}
          </span>
        )}
      </div>

      {/* Right: P&L + Clock */}
      <div className="flex items-center gap-6">
        {/* Today's P&L */}
        <div className="text-right hidden sm:block">
          <p className="text-xs text-muted uppercase tracking-wide">Today P&L</p>
          <p
            className={clsx(
              'text-sm font-mono font-bold',
              pnlR >= 0 ? 'text-success' : 'text-danger'
            )}
          >
            {pnlR >= 0 ? '+' : ''}{pnlR.toFixed(2)}R
            <span className="text-muted font-normal text-xs ml-1">
              ({pnlUsdt >= 0 ? '+' : ''}{pnlUsdt.toFixed(2)} USDT)
            </span>
          </p>
        </div>

        {/* UTC Clock */}
        <div className="text-right">
          <p className="text-xs text-muted uppercase tracking-wide">UTC</p>
          <p className="text-sm font-mono text-text">
            {now.toISOString().slice(11, 19)}
          </p>
        </div>
      </div>
    </header>
  )
}
```

---

## Update `App.tsx` with Header Import

Ensure `App.tsx` (written in Phase 5 Step 1) imports and uses both `Sidebar` and `Header`:

```tsx
// App.tsx — relevant snippet
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
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
```

---

## Bot Status Polling

The Header needs live bot status. Add a polling effect to `main.tsx` or create a hook:

```tsx
// frontend/src/hooks/usePolling.ts
import { useEffect } from 'react'
import { useDashboardStore } from '../store/dashboardStore'

export function useBotPolling(intervalMs = 10000) {
  const { fetchOverview } = useDashboardStore()

  useEffect(() => {
    fetchOverview()
    const id = setInterval(fetchOverview, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
}
```

Update `App.tsx` to call the polling hook:

```tsx
import { useBotPolling } from './hooks/usePolling'

function ProtectedLayout({ children }: { children: React.ReactNode }) {
  useBotPolling(10000) // Poll every 10 seconds
  // ...
}
```

---

## Sticky Header Option

For scrollable pages, the header is already fixed at the top of the flex column. No changes needed — the `main` area scrolls independently.

---

## Deliverables

1. **`frontend/src/components/layout/Sidebar.tsx`** — Fixed sidebar with logo, nav links (with active state), user info, logout
2. **`frontend/src/components/layout/Header.tsx`** — Top bar with bot status badge, live P&L display, UTC clock
3. **`frontend/src/hooks/usePolling.ts`** — Polling hook for bot status
4. **`App.tsx`** — Updated to wire Sidebar + Header into ProtectedLayout

---

## Validation

```bash
cd /home/vick/fubot/frontend && npm run dev
# Visit http://localhost:5173
# - Login → Sidebar visible with all 5 nav items
# - Header shows bot status and "Today P&L"
# - Clicking nav items highlights the active route
# - Clicking "Sign Out" → redirects to /login
# - Clock updates every second
```

---

## Next Step
Phase 5 Step 4: Overview Page — Dashboard Cards

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
