# Phase 5 — Step 10: Polish — Responsive Layout, Loading States, Error Handling, Mobile
## FUBOT: Final UI Polish and Quality Pass

---

## Objective
Polish the FUBOT dashboard for production: responsive layouts that work on mobile, comprehensive loading states, global error handling, and empty states for all pages.

---

## Context
- **Depends on**: Phase 5 Steps 1–9 (all pages built)
- **Goal**: All pages render correctly on mobile (320px–1440px), show appropriate loading/success/error states, handle API failures gracefully

---

## 1. Global Error Boundary

React needs an error boundary to catch rendering crashes:

### `frontend/src/components/ui/ErrorBoundary.tsx`

```tsx
import { Component, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props { children: ReactNode }
interface State { hasError: boolean; error: string | null }

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[200px] p-8 text-center">
          <AlertTriangle className="w-10 h-10 text-danger mb-4" />
          <h2 className="text-lg font-semibold text-danger mb-2">Something went wrong</h2>
          <p className="text-sm text-muted max-w-sm">
            {this.state.error || 'An unexpected error occurred. Try refreshing the page.'}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-surface2 border border-border rounded text-sm hover:bg-surface transition-colors"
          >
            Refresh Page
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
```

Update `App.tsx` to wrap routes:

```tsx
import { ErrorBoundary } from './components/ui/ErrorBoundary'

// In ProtectedLayout:
return (
  <ErrorBoundary>
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          {children}
        </main>
      </div>
      <ToastContainer />
    </div>
  </ErrorBoundary>
)
```

---

## 2. Loading Skeleton Components

### `frontend/src/components/ui/Skeleton.tsx`

```tsx
import { clsx } from 'clsx'

interface SkeletonProps {
  className?: string
  lines?: number
}

export function Skeleton({ className, lines = 1 }: SkeletonProps) {
  if (lines === 1) {
    return <div className={clsx('animate-pulse bg-surface2 rounded', className)} />
  }
  return (
    <div className={clsx('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="animate-pulse bg-surface2 rounded h-4" style={{ width: `${85 + Math.random() * 15}%` }} />
      ))}
    </div>
  )
}

export function CardSkeleton() {
  return (
    <div className="card space-y-3">
      <div className="flex justify-between">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-5 w-16 rounded" />
      </div>
      <Skeleton className="h-8 w-32" />
      <Skeleton lines={2} className="mt-4" />
    </div>
  )
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full rounded" />
      ))}
    </div>
  )
}
```

---

## 3. Update Overview Page with Loading Skeletons

In `Overview.tsx`, add loading state:

```tsx
import { CardSkeleton, TableSkeleton } from '../components/ui/Skeleton'
import { useDashboardStore } from '../store/dashboardStore'

export default function Overview() {
  const { isLoading, fetchOverview, fetchToday } = useDashboardStore()

  useEffect(() => {
    fetchOverview()
    fetchToday()
  }, [])

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-48 animate-pulse bg-surface2 rounded" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <CardSkeleton />
          <CardSkeleton />
        </div>
        <CardSkeleton />
        <CardSkeleton />
      </div>
    )
  }

  // ... normal render
}
```

---

## 4. Responsive Layout Tweaks

### Sidebar Mobile Behavior

Update `Sidebar.tsx` to support collapsible mobile sidebar:

```tsx
// Add mobile state
const [mobileOpen, setMobileOpen] = useState(false)

// Wrap in a portal/drawer on mobile
// For simplicity, hide on mobile and show hamburger menu in Header

// In Header, add hamburger:
{mobileOpen && (
  <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setMobileOpen(false)}>
    <div className="absolute left-0 top-0 bottom-0 w-56" onClick={(e) => e.stopPropagation()}>
      <SidebarInner /> {/* extracted Sidebar content */}
    </div>
  </div>
)}
```

Or use a simpler approach — just add a hamburger to the Header on mobile:

```tsx
// In Header.tsx:
import { Menu } from 'lucide-react'
const [sidebarOpen, setSidebarOpen] = useState(false)

// In JSX, before Sidebar:
{sidebarOpen && (
  <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setSidebarOpen(false)}>
    <div className="absolute left-0 top-0 bottom-0 w-56 bg-surface border-r border-border" onClick={(e) => e.stopPropagation()}>
      {/* Inline Sidebar content for mobile */}
    </div>
  </div>
)}
```

### Better approach: CSS-based responsive sidebar

In `App.tsx`, use Tailwind's hidden/sm:block:

```tsx
<div className="flex h-screen bg-background overflow-hidden">
  {/* Desktop sidebar — hidden on mobile */}
  <div className="hidden lg:flex">
    <Sidebar />
  </div>

  {/* Mobile header with hamburger */}
  <div className="flex-1 flex flex-col overflow-hidden lg:hidden">
    <header className="h-14 bg-surface border-b border-border flex items-center px-4">
      <button onClick={() => setMobileMenuOpen(true)} className="p-2 -ml-2">
        <Menu className="w-5 h-5" />
      </button>
      <span className="ml-3 font-mono font-bold text-primary">FUBOT</span>
    </header>
    {mobileMenuOpen && (
      <div className="fixed inset-0 z-50">
        <div className="absolute inset-0 bg-black/50" onClick={() => setMobileMenuOpen(false)} />
        <div className="relative w-56 h-full bg-surface border-r">
          <Sidebar />
        </div>
      </div>
    )}
    <main className="flex-1 overflow-y-auto p-4">
      {children}
    </main>
  </div>

  {/* Desktop main area */}
  <div className="flex-1 flex flex-col overflow-hidden hidden lg:flex">
    <Header />
    <main className="flex-1 overflow-y-auto p-6">
      {children}
    </main>
  </div>

  <ToastContainer />
</div>
```

---

## 5. Empty States for All Pages

Add empty state components to pages that don't have them:

**TradeLog.tsx** — already has empty state ✓
**Scanner.tsx** — already has empty state ✓
**PositionsTable** — already has empty state ✓
**EquityCurve** — show "No data yet" when `equityCurve.length === 0`:

```tsx
// In EquityCurve.tsx, after the chart:
{equityCurve.length === 0 && (
  <div className="absolute inset-0 flex items-center justify-center text-muted text-sm">
    No equity data yet. The bot needs to run before data appears.
  </div>
)}
```

---

## 6. Network Error Handling

Add a global network error toast in the API client:

```ts
// In api/client.ts:
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      // Already handled
    } else if (err.response?.status === 429) {
      useAlertStore.getState().addToast('warn', 'Rate limited. Please wait.')
    } else if (!err.response) {
      useAlertStore.getState().addToast('error', 'Network error. Check your connection.')
    }
    return Promise.reject(err)
  }
)
```

---

## 7. Favicon + App Icon

Create `frontend/public/favicon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#151920"/>
  <text x="16" y="22" font-family="monospace" font-size="16" font-weight="bold" fill="#3B82F6" text-anchor="middle">F</text>
</svg>
```

And update `frontend/index.html`:

```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<title>FUBOT — Binance Futures Trading Bot</title>
```

---

## 8. `vite.config.ts` Proxy (for local dev)

Configure Vite to proxy API requests to the backend during development:

```ts
// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

Update `frontend/src/api/endpoints.ts` to use relative URLs in dev:

```ts
// Use relative URL so proxy works
export const API_BASE = import.meta.env.VITE_API_URL || '/api'
```

---

## 9. Production Build Test

```bash
cd /home/vick/fubot/frontend
npm run build
# Check for any TypeScript errors
# Verify dist/ directory is created
```

---

## 10. Final Visual Checkpoints

After all polish is applied:

- [ ] Login page renders correctly on mobile (320px)
- [ ] Overview page renders correctly on mobile — stacked cards
- [ ] Sidebar collapses (hamburger menu) on mobile
- [ ] All pages show loading skeletons while fetching data
- [ ] All pages show empty states when no data
- [ ] Network errors show toast notification
- [ ] 401 errors redirect to /login
- [ ] Favicon shows FUBOT icon
- [ ] `npm run build` completes without errors
- [ ] `dist/` contains valid production assets

---

## Deliverables

1. **`frontend/src/components/ui/ErrorBoundary.tsx`** — Catches React rendering errors
2. **`frontend/src/components/ui/Skeleton.tsx`** — `Skeleton`, `CardSkeleton`, `TableSkeleton`
3. **`Overview.tsx`** — Loading skeletons while fetching
4. **Responsive layout** — Mobile hamburger menu for sidebar, stacked cards
5. **Empty states** — All chart/table components handle empty data gracefully
6. **Global error handling** — Network errors, 401 redirects, rate limit toasts
7. **`vite.config.ts`** — API proxy for local dev
8. **`frontend/public/favicon.svg`** — FUBOT logo favicon
9. **Production build** — `npm run build` succeeds

---

## Next Step
Phase 6 Step 1: End-to-End Test — Full Trade Flow Validation

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
