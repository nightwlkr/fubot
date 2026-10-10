# frontend/ — React Trading Dashboard

## Purpose

Real-time trading dashboard for the FUBOT bot. Displays positions, P&L, equity curve, trade log, bot status, and allows the operator to start/stop the bot and adjust settings.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/frontend/`
Status: **Phase 5 complete** — React + Vite + TypeScript + Tailwind v4 + Zustand dashboard built per FUBOT_STEPS PHASE5 (all 10 steps)

## Local Contracts

- Stack: React 18 + Vite + TypeScript + Tailwind v4 (CSS-first config) + Zustand
- State management: Zustand for UI state (authStore, dashboardStore)
- Routing: React Router v6
- Charts: Lightweight Charts (TradingView) for equity curve and performance charts
- API: Axios client with JWT cookie auth + 401 interceptor; Vite proxy to backend on /api in dev
- Real-time: WebSocket at /ws with JWT auth + auto-reconnect

## Work Guidance

### Design System
- Dark theme — trading terminal aesthetic
- Primary: `#3B82F6` (blue), Success: `#22C55E` (green), Danger: `#EF4444` (red)
- Background: `#0B0E11`, Surface: `#151920`, Surface2: `#1C2028`, Border: `#2A2F3A`
- Monospace fonts for numbers and code

### Pages
1. **Login** (`/login`) — Form with validation, JWT auth, error display
2. **Overview** (`/`) — BotStatusCard, PnLCard, PositionsTable, EquityCurve (LiveChart)
3. **Trade Log** (`/trades`) — Filterable, paginated table + CSV export
4. **Scanner** (`/scanner`) — Signal cards, filter tabs, auto-refresh
5. **Performance** (`/performance`) — Equity chart, monthly returns, R distribution
6. **Settings** (`/settings`) — API key entry, trading params sliders, symbol config

### API Integration
- REST: Axios to `/api/...` endpoints
- WebSocket: `wsManager` singleton with message routing to stores

## Verification

- `npm run build` completes without TypeScript or CSS errors
- `npm run dev` starts dev server on port 5173 with Vite proxy to backend
- All pages render with data from backend (when backend is running)
- Production build at `frontend/dist/`

## Child DOX Index

- **frontend/src/pages/** — Route page components (Login, Overview, TradeLog, Scanner, Performance, Settings)
- **frontend/src/components/** — UI primitives (Button, Card, Badge, Table) + layout (Sidebar, Header) + feature components
- **frontend/src/store/** — Zustand stores (authStore, dashboardStore)
- **frontend/src/api/** — Axios client, WebSocket manager, endpoint constants
- **frontend/src/types/** — Shared TypeScript interfaces
