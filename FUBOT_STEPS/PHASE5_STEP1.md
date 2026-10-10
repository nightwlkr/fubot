# Phase 5 — Step 1: Frontend Project Setup
## FUBOT: React + Vite + TypeScript + Tailwind + Zustand

---

## Objective
Scaffold the FUBOT frontend project: React + Vite + TypeScript + Tailwind CSS + Zustand state management. The dev server must run locally and build for production.

---

## Context
- **Depends on**: Phase 1 complete (backend API is running)
- **Frontend runs on**: Port 5173 dev, builds to `dist/`
- **Node.js**: Required on the build machine (v18+)
- **Package manager**: npm (use npm, not yarn/pnpm for consistency)

---

## Project Initialization

### 1. Create the React app

```bash
cd /home/vick/fubot
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

### 2. Install core dependencies

```bash
npm install \
  react-router-dom \
  zustand \
  axios \
  react-hook-form \
  @hookform/resolvers \
  zod \
  clsx \
  tailwind-merge \
  lightweight-charts \
  @tanstack/react-query \
  js-cookie \
  @types/js-cookie
```

### 3. Install Tailwind CSS

```bash
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

### 4. Configure `tailwind.config.js`

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark trading theme
        background: '#0B0E11',
        surface: '#151920',
        surface2: '#1C2028',
        border: '#2A2F3A',
        primary: '#3B82F6',     // Blue accent
        success: '#22C55E',      // Green (P&L positive)
        danger: '#EF4444',      // Red (P&L negative / sells)
        warning: '#F59E0B',     // Amber
        text: '#F9FAFB',
        muted: '#6B7280',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
```

### 5. Configure `src/styles/globals.css`

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-background text-text font-sans;
    -webkit-font-smoothing: antialiased;
  }

  * {
    @apply border-border;
  }
}

@layer utilities {
  .pnl-positive { @apply text-success; }
  .pnl-negative { @apply text-danger; }
  .badge-long { @apply bg-success/20 text-success px-2 py-0.5 rounded text-xs font-mono; }
  .badge-short { @apply bg-danger/20 text-danger px-2 py-0.5 rounded text-xs font-mono; }
  .card { @apply bg-surface border border-border rounded-lg p-4; }
}
```

### 6. Install shadcn/ui-style utility (optional but recommended)

Instead of installing a full component library, build minimal primitives:

```bash
npm install lucide-react
```

---

## Project Structure

Create the following directory structure under `frontend/src/`:

```
frontend/src/
├── main.tsx
├── App.tsx
├── api/
│   ├── client.ts          # Axios fetch wrapper with auth
│   ├── websocket.ts        # WebSocket connection manager
│   └── endpoints.ts        # API route constants
├── hooks/
│   ├── useAuth.ts
│   ├── useWebSocket.ts
│   ├── usePositions.ts
│   ├── useDashboard.ts
│   └── useTrades.ts
├── store/
│   ├── authStore.ts         # Zustand: user, token, login/logout
│   ├── dashboardStore.ts    # Zustand: positions, pnl, bot status
│   └── settingsStore.ts     # Zustand: app settings
├── pages/
│   ├── Login.tsx
│   ├── Overview.tsx
│   ├── TradeLog.tsx
│   ├── Scanner.tsx
│   ├── Performance.tsx
│   └── Settings.tsx
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx
│   │   └── Header.tsx
│   ├── dashboard/
│   │   ├── BotStatusCard.tsx
│   │   ├── PnLCard.tsx
│   │   ├── PositionsTable.tsx
│   │   └── EquityCurve.tsx
│   ├── trades/
│   │   └── TradeTable.tsx
│   ├── signals/
│   │   └── SignalCard.tsx
│   ├── charts/
│   │   └── EquityChart.tsx
│   └── ui/
│       ├── Button.tsx
│       ├── Card.tsx
│       ├── Input.tsx
│       ├── Table.tsx
│       └── Badge.tsx
├── types/
│   └── index.ts             # Shared TypeScript types (TS interfaces)
└── styles/
    └── globals.css
```

Create all directories:

```bash
mkdir -p frontend/src/{api,hooks,store,pages,components/{layout,dashboard,trades,signals,charts,ui},types,styles}
```

---

## API Client Setup

### `frontend/src/api/endpoints.ts`

```ts
// Base URL — in production this is the domain
export const API_BASE = import.meta.env.VITE_API_URL || 'https://fubot.bedikian.cc/api'

export const ENDPOINTS = {
  // Auth
  login: '/auth/login',
  logout: '/auth/logout',
  me: '/auth/me',

  // Dashboard
  dashboardOverview: '/dashboard/overview',
  dashboardToday: '/dashboard/today',

  // Bot control
  botStart: '/bot/start',
  botStop: '/bot/stop',
  botStatus: '/bot/status',

  // Positions
  positions: '/positions',
  position: (id: number) => `/positions/${id}`,

  // Trades
  trades: '/trades',
  trade: (id: number) => `/trades/${id}`,
  tradeStats: '/trades/stats',

  // Signals
  signals: '/signals',
  signalsActive: '/signals/active',
  signalsMissed: '/signals/missed',

  // Orders
  orders: '/orders',
  order: (id: number) => `/orders/${id}`,

  // Settings
  settings: '/settings',
  settingsTrading: '/settings/trading',
  settingsSymbols: '/settings/symbols',
  settingsSymbol: (symbol: string) => `/settings/symbols/${symbol}`,

  // Performance
  performanceEquity: '/performance/equity',
  performanceMonthly: '/performance/monthly',
  performanceStrategies: '/performance/strategies',
} as const
```

### `frontend/src/api/client.ts`

```ts
import axios from 'axios'
import Cookies from 'js-cookie'
import { API_BASE, ENDPOINTS } from './endpoints'

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true, // Send httpOnly cookie
})

// Attach JWT token to every request
apiClient.interceptors.request.use((config) => {
  const token = Cookies.get('fubot_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 → redirect to login
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      Cookies.remove('fubot_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

// Typed helpers
export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) =>
    apiClient.get<T>(url, { params }),

  post: <T>(url: string, data?: unknown) =>
    apiClient.post<T>(url, data),

  patch: <T>(url: string, data?: unknown) =>
    apiClient.patch<T>(url, data),

  put: <T>(url: string, data?: unknown) =>
    apiClient.put<T>(url, data),

  delete: <T>(url: string) =>
    apiClient.delete<T>(url),
}
```

### `frontend/src/api/websocket.ts`

```ts
import { API_BASE } from './endpoints'
import Cookies from 'js-cookie'

export type WSType =
  | 'auth'
  | 'position_update'
  | 'order_update'
  | 'signal_update'
  | 'pnl_update'
  | 'bot_status'
  | 'alert'

export interface WSMessage {
  type: WSType
  data?: unknown
}

type WSHandler = (msg: WSMessage) => void

class WebSocketManager {
  private ws: WebSocket | null = null
  private handlers: Set<WSHandler> = new Set()
  private reconnectDelay = 2000
  private maxReconnectDelay = 30000
  private shouldReconnect = true

  connect() {
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws'
    this.ws = new WebSocket(wsUrl)

    this.ws.onopen = () => {
      // Authenticate immediately
      const token = Cookies.get('fubot_token')
      if (token) {
        this.send({ type: 'auth', data: { token } })
      }
      this.reconnectDelay = 2000 // Reset backoff
    }

    this.ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        this.handlers.forEach((h) => h(msg))
      } catch {
        // Ignore parse errors
      }
    }

    this.ws.onclose = () => {
      if (this.shouldReconnect) {
        setTimeout(() => {
          this.reconnectDelay = Math.min(
            this.reconnectDelay * 1.5,
            this.maxReconnectDelay
          )
          this.connect()
        }, this.reconnectDelay)
      }
    }
  }

  disconnect() {
    this.shouldReconnect = false
    this.ws?.close()
  }

  send(msg: WSMessage) {
    this.ws?.send(JSON.stringify(msg))
  }

  onMessage(handler: WSHandler) {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }
}

export const wsManager = new WebSocketManager()
```

---

## Zustand Stores

### `frontend/src/store/authStore.ts`

```ts
import { create } from 'zustand'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import Cookies from 'js-cookie'

interface User {
  id: number
  username: string
}

interface AuthState {
  user: User | null
  token: string | null
  isLoading: boolean
  error: string | null

  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: Cookies.get('fubot_token') || null,
  isLoading: false,
  error: null,

  login: async (username, password) => {
    set({ isLoading: true, error: null })
    try {
      const res = await api.post<{ token: string; user: User }>(
        ENDPOINTS.login,
        { username, password }
      )
      const { token, user } = res.data
      Cookies.set('fubot_token', token, { expires: 7 })
      set({ user, token, isLoading: false })
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || 'Login failed',
        isLoading: false,
      })
      throw err
    }
  },

  logout: async () => {
    try {
      await api.post(ENDPOINTS.logout)
    } catch {
      // Ignore logout errors
    }
    Cookies.remove('fubot_token')
    set({ user: null, token: null })
  },

  checkAuth: async () => {
    const token = Cookies.get('fubot_token')
    if (!token) return
    try {
      const res = await api.get<{ user: User }>(ENDPOINTS.me)
      set({ user: res.data.user, token })
    } catch {
      Cookies.remove('fubot_token')
      set({ user: null, token: null })
    }
  },
}))
```

### `frontend/src/store/dashboardStore.ts`

```ts
import { create } from 'zustand'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'

export interface Position {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  quantity: number
  entry_price: number
  mark_price: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  stop_loss: number | null
  take_profit: number | null
  leverage: number
  opened_at: string
}

export interface BotStatus {
  status: 'running' | 'stopped'
  mode: 'paper' | 'live'
  uptime_seconds: number
  scan_count: number
  last_scan: string | null
}

export interface TodayStats {
  pnl_r: number
  pnl_usdt: number
  trades_count: number
  wins: number
  losses: number
  daily_limit_status: {
    used_r: number
    limit_r: number
    remaining_r: number
  }
}

interface DashboardState {
  botStatus: BotStatus | null
  positions: Position[]
  todayStats: TodayStats | null
  equityCurve: { date: string; balance: number }[]
  isLoading: boolean

  fetchOverview: () => Promise<void>
  fetchToday: () => Promise<void>
  startBot: (mode: 'paper' | 'live') => Promise<void>
  stopBot: () => Promise<void>
  applyWSupdate: (type: string, data: unknown) => void
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  botStatus: null,
  positions: [],
  todayStats: null,
  equityCurve: [],
  isLoading: false,

  fetchOverview: async () => {
    set({ isLoading: true })
    try {
      const [overviewRes, todayRes] = await Promise.all([
        api.get<{
          bot_status: BotStatus
          open_positions: Position[]
          equity_curve: { date: string; balance: number }[]
          quick_stats: Record<string, unknown>
        }>(ENDPOINTS.dashboardOverview),
        api.get<TodayStats>(ENDPOINTS.dashboardToday),
      ])
      set({
        botStatus: overviewRes.data.bot_status,
        positions: overviewRes.data.open_positions,
        equityCurve: overviewRes.data.equity_curve,
        todayStats: todayRes.data,
        isLoading: false,
      })
    } catch {
      set({ isLoading: false })
    }
  },

  fetchToday: async () => {
    try {
      const res = await api.get<TodayStats>(ENDPOINTS.dashboardToday)
      set({ todayStats: res.data })
    } catch {
      // Ignore
    }
  },

  startBot: async (mode) => {
    await api.post(ENDPOINTS.botStart, { mode })
    get().fetchOverview()
  },

  stopBot: async () => {
    await api.post(ENDPOINTS.botStop)
    get().fetchOverview()
  },

  applyWSupdate: (type, data) => {
    if (type === 'position_update') {
      set({ positions: data as Position[] })
    }
    if (type === 'bot_status') {
      set({ botStatus: data as BotStatus })
    }
    if (type === 'pnl_update') {
      set({ todayStats: data as TodayStats })
    }
  },
}))
```

---

## Routing Setup

### `frontend/src/App.tsx`

```tsx
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
          element={<ProtectedLayout><Overview /></ProtectedLayout>}
        />
        <Route
          path="/trades"
          element={<ProtectedLayout><TradeLog /></ProtectedLayout>}
        />
        <Route
          path="/scanner"
          element={<ProtectedLayout><Scanner /></ProtectedLayout>}
        />
        <Route
          path="/performance"
          element={<ProtectedLayout><Performance /></ProtectedLayout>}
        />
        <Route
          path="/settings"
          element={<ProtectedLayout><Settings /></ProtectedLayout>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
```

---

## UI Primitives

### `frontend/src/components/ui/Button.tsx`

```tsx
import { ButtonHTMLAttributes, forwardRef } from 'react'
import { clsx } from 'clsx'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  isLoading?: boolean
}

const variants: Record<Variant, string> = {
  primary: 'bg-primary hover:bg-primary/80 text-white',
  secondary: 'bg-surface2 hover:bg-surface border border-border text-text',
  danger: 'bg-danger hover:bg-danger/80 text-white',
  ghost: 'hover:bg-surface2 text-muted hover:text-text',
}

const sizes: Record<Size, string> = {
  sm: 'px-3 py-1.5 text-xs rounded',
  md: 'px-4 py-2 text-sm rounded-md',
  lg: 'px-6 py-3 text-base rounded-lg',
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', isLoading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || isLoading}
      className={clsx(
        'inline-flex items-center justify-center gap-2 font-medium transition-colors',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {isLoading && (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
)
Button.displayName = 'Button'
export { Button }
```

### `frontend/src/components/ui/Card.tsx`

```tsx
import { HTMLAttributes, forwardRef } from 'react'
import { clsx } from 'clsx'

const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={clsx('card', className)}
      {...props}
    />
  )
)
Card.displayName = 'Card'

const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={clsx('flex items-center justify-between mb-4', className)} {...props} />
  )
)
CardHeader.displayName = 'CardHeader'

const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3 ref={ref} className={clsx('text-sm font-semibold text-muted uppercase tracking-wide', className)} {...props} />
  )
)
CardTitle.displayName = 'CardTitle'

export { Card, CardHeader, CardTitle }
```

### `frontend/src/components/ui/Badge.tsx`

```tsx
import { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

type BadgeVariant = 'success' | 'danger' | 'warning' | 'neutral' | 'info'

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
}

const variantClasses: Record<BadgeVariant, string> = {
  success: 'bg-success/20 text-success',
  danger: 'bg-danger/20 text-danger',
  warning: 'bg-warning/20 text-warning',
  neutral: 'bg-surface2 text-muted',
  info: 'bg-primary/20 text-primary',
}

export function Badge({ variant = 'neutral', className, ...props }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium',
        variantClasses[variant],
        className
      )}
      {...props}
    />
  )
}
```

### `frontend/src/components/ui/Table.tsx`

```tsx
import { HTMLAttributes, forwardRef } from 'react'
import { clsx } from 'clsx'

const Table = forwardRef<HTMLTableElement, HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <div className="w-full overflow-auto">
      <table ref={ref} className={clsx('w-full text-sm', className)} {...props} />
    </div>
  )
)
Table.displayName = 'Table'

const TableHead = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <thead ref={ref} className={clsx('border-b border-border', className)} {...props} />
  )
)
TableHead.displayName = 'TableHead'

const TableRow = forwardRef<HTMLTableRowElement, HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr ref={ref} className={clsx('border-b border-border/50 hover:bg-surface2/50 transition-colors', className)} {...props} />
  )
)
TableRow.displayName = 'TableRow'

const TableHeadCell = forwardRef<HTMLTableCellElement, HTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <th ref={ref} className={clsx('text-left text-xs font-medium text-muted uppercase tracking-wide py-3 px-4', className)} {...props} />
  )
)
TableHeadCell.displayName = 'TableHeadCell'

const TableCell = forwardRef<HTMLTableCellElement, HTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <td ref={ref} className={clsx('py-3 px-4 text-text', className)} {...props} />
  )
)
TableCell.displayName = 'TableCell'

export { Table, TableHead, TableRow, TableHeadCell, TableCell }
```

---

## Deliverables

1. **`frontend/` directory** — Vite React TypeScript app with all dependencies installed
2. **`frontend/src/api/`** — `client.ts`, `websocket.ts`, `endpoints.ts` working
3. **`frontend/src/store/`** — `authStore.ts`, `dashboardStore.ts` with Zustand
4. **`frontend/src/components/ui/`** — `Button`, `Card`, `Badge`, `Table`
5. **`frontend/src/App.tsx`** — React Router setup with protected routes
6. **Dev server** — `npm run dev` starts without error on port 5173
7. **`frontend/.env.example`** — Documents required env vars:
   ```
   VITE_API_URL=https://fubot.bedikian.cc/api
   ```

---

## Validation

```bash
cd /home/vick/fubot/frontend
npm run dev -- --host 0.0.0.0  # Should start on port 5173 without error
# Visit http://localhost:5173 — should show Login page (redirect works)
```

---

## Next Step
Phase 5 Step 2: Login Page + JWT Auth Flow

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
