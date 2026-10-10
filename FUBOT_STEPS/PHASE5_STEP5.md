# Phase 5 — Step 5: WebSocket Real-time Updates in React
## FUBOT: Live Data Without Polling

---

## Objective
Wire the WebSocket connection to React state so the dashboard updates live — positions, P&L, bot status, and alerts — without any page refresh or polling.

---

## Context
- **Depends on**: Phase 5 Steps 1–4 (WebSocketManager already written)
- **Connection management**: Auto-connect on login, disconnect on logout
- **Message types**: `position_update`, `pnl_update`, `bot_status`, `alert`
- **Persistence**: Reconnect automatically on connection drop

---

## WebSocket Hook

### `frontend/src/hooks/useWebSocket.ts`

```ts
import { useEffect } from 'react'
import { wsManager, WSMessage } from '../api/websocket'
import { useDashboardStore } from '../store/dashboardStore'
import { useAuthStore } from '../store/authStore'

export function useWebSocket() {
  const { user } = useAuthStore()
  const { applyWSupdate } = useDashboardStore()

  useEffect(() => {
    if (!user) return // Only connect when authenticated

    // Register message handler
    const unsubscribe = wsManager.onMessage((msg: WSMessage) => {
      switch (msg.type) {
        case 'position_update':
        case 'pnl_update':
        case 'bot_status':
        case 'signal_update':
        case 'order_update':
          applyWSupdate(msg.type, msg.data)
          break

        case 'alert':
          // Handle alerts (e.g., toast notification)
          console.log('[WS Alert]', msg.data)
          break

        default:
          break
      }
    })

    // Connect (or reconnect) if needed
    if (!wsManager.ws || wsManager.ws.readyState === WebSocket.CLOSED) {
      wsManager.connect()
    }

    return () => {
      unsubscribe()
      // Don't disconnect here — keep connection alive across page navigations
    }
  }, [user, applyWSupdate])
}
```

---

## Integrate useWebSocket into App

Update `App.tsx` to call `useWebSocket()` in the protected layout:

```tsx
import { useWebSocket } from './hooks/useWebSocket'

function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore()
  const navigate = useNavigate()

  useWebSocket() // ← Live WebSocket updates

  useBotPolling(10000)

  if (!user) return <Navigate to="/login" replace />
  // ...
}
```

---

## Alert Toast System (Optional But Recommended)

Create a simple toast store:

### `frontend/src/store/alertStore.ts`

```ts
import { create } from 'zustand'

export interface Toast {
  id: string
  level: 'info' | 'warn' | 'error' | 'success'
  message: string
  timestamp: Date
}

interface AlertState {
  toasts: Toast[]
  addToast: (level: Toast['level'], message: string) => void
  removeToast: (id: string) => void
}

export const useAlertStore = create<AlertState>((set) => ({
  toasts: [],

  addToast: (level, message) => {
    const id = Math.random().toString(36).slice(2)
    set((state) => ({
      toasts: [...state.toasts, { id, level, message, timestamp: new Date() }],
    }))
    // Auto-remove after 5 seconds
    setTimeout(() => {
      set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }))
    }, 5000)
  },

  removeToast: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}))
```

---

## Toast Component

### `frontend/src/components/ui/Toast.tsx`

```tsx
import { useAlertStore } from '../../store/alertStore'
import { X, AlertCircle, CheckCircle2, Info, AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'

const levelConfig = {
  info: { icon: Info, className: 'border-primary bg-primary/10 text-primary' },
  success: { icon: CheckCircle2, className: 'border-success bg-success/10 text-success' },
  warn: { icon: AlertTriangle, className: 'border-warning bg-warning/10 text-warning' },
  error: { icon: AlertCircle, className: 'border-danger bg-danger/10 text-danger' },
}

export function ToastContainer() {
  const { toasts, removeToast } = useAlertStore()

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => {
        const config = levelConfig[toast.level]
        const Icon = config.icon
        return (
          <div
            key={toast.id}
            className={clsx(
              'flex items-start gap-3 p-3 rounded-lg border shadow-lg backdrop-blur-sm',
              'animate-in slide-in-from-right-5',
              config.className
            )}
          >
            <Icon className="w-4 h-4 mt-0.5 shrink-0" />
            <p className="flex-1 text-sm">{toast.message}</p>
            <button
              onClick={() => removeToast(toast.id)}
              className="opacity-60 hover:opacity-100 transition-opacity"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
```

Add `<ToastContainer />` to `App.tsx` just inside the root div:

```tsx
// App.tsx
import { ToastContainer } from './components/ui/Toast'

function ProtectedLayout({ children }) {
  // ...
  return (
    <div className="flex h-screen ...">
      <Sidebar />
      <div className="flex-1 flex flex-col ...">
        <Header />
        <main className="...">{children}</main>
      </div>
      <ToastContainer /> {/* ← Add here */}
    </div>
  )
}
```

---

## Wire WebSocket Alerts to Toast

Update `useWebSocket.ts` to show toasts on alert messages:

```ts
import { useAlertStore } from '../store/alertStore'

// Inside useWebSocket useEffect:
const { addToast } = useAlertStore.getState()

const unsubscribe = wsManager.onMessage((msg: WSMessage) => {
  switch (msg.type) {
    // ...
    case 'alert': {
      const data = msg.data as { level: 'info' | 'warn' | 'error'; message: string }
      addToast(data.level, data.message)
      break
    }
    // ...
  }
})
```

---

## WS Reconnect Logic

The `WebSocketManager` (Phase 5 Step 1) already handles exponential backoff reconnect. Ensure `shouldReconnect` is reset on successful reconnect:

```ts
// In the connect() method's onopen handler:
this.ws.onopen = () => {
  this.reconnectDelay = 2000 // Reset backoff on connect
  const token = Cookies.get('fubot_token')
  if (token) {
    this.send({ type: 'auth', data: { token } })
  }
}
```

---

## Deliverables

1. **`frontend/src/hooks/useWebSocket.ts`** — Hook that connects WS, routes messages to stores
2. **`App.tsx`** — Updated to call `useWebSocket()`
3. **`frontend/src/store/alertStore.ts`** — Toast notification state
4. **`frontend/src/components/ui/Toast.tsx`** — Toast rendering component
5. **`App.tsx`** — Updated to include `<ToastContainer />`
6. **Toast on alerts** — WebSocket `alert` messages show as toast notifications

---

## Validation

```bash
cd /home/vick/fubot/frontend && npm run dev
# Open browser DevTools Console
# Start the bot from dashboard
# You should see console.log('[WS Alert]') messages when WS events fire
# Toast notifications appear for alert-level events
# If WS connection drops, it should auto-reconnect
```

---

## Next Step
Phase 5 Step 6: Trade Log Page with Filters + Pagination + CSV Export

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
