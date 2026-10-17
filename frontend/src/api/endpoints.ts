// Base URL — use proxy in dev, full URL in production
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

// COLLECTION ROUTES NEED A TRAILING SLASH.
//
// Starlette's `redirect_slashes` answers a request for a collection root
// WITHOUT its slash (e.g. GET /api/trades) with a 307 to the slashed form.
// That redirect DISCARDS THE QUERY STRING: `GET /api/trades?side=SHORT`
// becomes `GET /api/trades/` — the filter is silently dropped and the client
// gets every trade back. axios follows the redirect transparently, so nothing
// errored and the Trade Log filters simply appeared to do nothing.
//
// Only the four routers whose `@router.get("/")` sits at the collection root
// are affected; sub-paths (`/trades/stats`, `/performance/equity`) never
// redirect and already work.
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
  botCloseAll: '/bot/close-all',
  botClose: (positionId: number) => `/bot/close/${positionId}`,
  botStatus: '/bot/status',

  // Positions
  positions: '/positions/',
  position: (id: number) => `/positions/${id}`,

  // Trades
  trades: '/trades/',
  trade: (id: number) => `/trades/${id}`,
  tradeStats: '/trades/stats',

  // Signals
  signals: '/signals/',
  signalsActive: '/signals/active',
  signalsMissed: '/signals/missed',

  // Orders
  orders: '/orders/',
  order: (id: number) => `/orders/${id}`,

  // Settings
  settings: '/settings',
  settingsTrading: '/settings/trading',
  settingsSymbols: '/settings/symbols',
  settingsSymbol: (symbol: string) => `/settings/symbols/${symbol}`,

  // Performance
  performanceEquity: '/performance/equity',
  performancePnL: '/performance/pnl',
  performanceMonthly: '/performance/monthly',
  performanceStrategies: '/performance/strategies',
} as const
