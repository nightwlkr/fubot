# frontend/ — React Trading Dashboard

## Purpose

Real-time trading dashboard for the FUBOT bot. Displays positions, P&L, equity curve, trade log, bot status, and allows the operator to start/stop the bot and adjust settings.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/frontend/`
Status: **PHASE5 complete** — builds clean; bot-control and live-data flows wired to the backend (2026-10-11)

## Local Contracts

- Stack: React 19 + Vite 8 + TypeScript 6 + Tailwind v4 (CSS-first `@theme` in `src/styles/globals.css`, mirrored in `tailwind.config.js`) + Zustand 5
- State: Zustand (authStore, dashboardStore); TanStack Query 5, react-hook-form + zod also installed
- Routing: react-router-dom v7
- Charts: lightweight-charts v5 (TradingView) for equity/performance charts
- API: Axios client, base `VITE_API_URL` (default `/api`), Bearer header via `getToken()`, 401 interceptor → `clearToken()` → `/login`; Vite dev proxy `/api` → `http://localhost:8000`
- Auth token: `api/token.ts` owns token storage — **localStorage only** (key `fubot_token`). The backend's `fubot_token` cookie is httpOnly (invisible to JS); the frontend keeps its own durable copy in localStorage for the Bearer header, the WS auth message, and `checkAuth`. There is deliberately NO js-cookie duplicate (a same-name JS cookie went stale vs localStorage; `clearToken` couldn't remove the httpOnly one). `checkAuth` runs from App on load (isRestoring gate) and always attempts `/me` — even without a stored token, because the httpOnly cookie rides along; `/me` echoes the token so a cookie-only session re-seeds localStorage. The axios 401 interceptor hard-redirects to `/login` only for protected endpoints (not `/auth/login`/`/auth/me` — those are handled by the caller).
- Real-time: `wsManager` dials `API_BASE + /ws` (= `/api/ws`) with an `auth` message and auto-reconnect; backend serves both `/ws` and `/api/ws`, and the Vite dev proxy upgrades WebSocket (`ws: true`)
- Lint: oxlint via `npm run lint` (`.oxlintrc.json`)

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
- REST: Axios to `/api/...` endpoints (typed helpers in `src/api/client.ts`, paths in `src/api/endpoints.ts`)
- WebSocket: `wsManager` singleton in `src/api/websocket.ts` with message routing to stores

### Known Gaps
- Backend WebSocket + bot engine now wired (2026-10-11): `/ws` serves the auth handshake and `/api/bot/*` works in paper mode
- 16 oxlint warnings (exhaustive-deps class); single ~537 kB JS chunk — code-split when convenient
- All 25 REST endpoints in `endpoints.ts` verified to match the backend's OpenAPI paths (2026-10-11); backend `win_rate` is already percent-scaled — do not multiply again (fixed in TradeLog/Performance)

## Verification

- `npm run build` — verified: `tsc -b && vite build` succeeds, output at `frontend/dist/` (~537 kB JS + 25 kB CSS)
- `npm run lint` — oxlint: currently 16 warnings, 0 errors
- `npm run dev` — dev server on port 5173 with `/api` proxy to backend

## Child DOX Index

- **frontend/src/pages/** — 6 route pages; per-page section components (SignalCard, EquityChart, APIKeySection, …) live inline in the page files
- **frontend/src/components/** — ui/ (Button, Card, Badge, Table) + layout/ (Sidebar, Header)
- **frontend/src/store/** — Zustand stores (authStore, dashboardStore)
- **frontend/src/api/** — client.ts (Axios + interceptors), endpoints.ts, websocket.ts (wsManager)
- **frontend/src/types/** — Shared TypeScript interfaces
