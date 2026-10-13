// Base URL — use proxy in dev, full URL in production
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

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
  performancePnL: '/performance/pnl',
  performanceMonthly: '/performance/monthly',
  performanceStrategies: '/performance/strategies',
} as const
