# frontend/ — React Trading Dashboard

## Purpose

Real-time trading dashboard for the FUBOT bot. Displays positions, P&L, equity curve, trade log, bot status, and allows the operator to start/stop the bot and adjust settings.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/frontend/`
Status: **Not yet scaffolded** — PHASE5 of the build steps

## Local Contracts

- Stack: React 18 + Vite + TypeScript
- State management: React Query (TanStack Query) for server state, Zustand for UI state
- Routing: React Router v6
- Charts: Lightweight charts (TradingView) for equity curve and price charts
- No Tailwind CSS — dark trading terminal aesthetic with custom CSS

## Work Guidance

### Design System
- Dark theme only — trading terminal aesthetic
- Primary color: `#3B82F6` (blue)
- Success (profit): `#22C55E` (green)
- Danger (loss): `#EF4444` (red)
- Background: `#0F172A` / `#1E293B`
- Monospace fonts for numbers and code

### Pages
1. **Dashboard** (`/`) — equity curve, open positions, daily P&L, bot status
2. **Trade Log** (`/trades`) — historical trades with P&L, R-multiple, date
3. **Performance** (`/performance`) — win rate, best/worst trade, R-multiple distribution
4. **Settings** (`/settings`) — bot params (risk %, stop loss, take profit, symbols)
5. **Login** (`/login`) — operator authentication

### API Integration
- REST: `fetch` or `axios` to `/api/v1/...`
- Real-time: WebSocket at `/api/v1/ws` for position updates, trade fills, equity ticks
- All requests include JWT bearer token from localStorage

## Verification

- `vite build` completes without errors
- `npm run dev` starts dev server on port 5173
- Dashboard loads with live data from backend

## Child DOX Index

- **frontend/src/pages/** — Route page components
- **frontend/src/components/** — Shared UI components
- **frontend/src/hooks/** — Custom React hooks (usePositions, useTrades, etc.)
