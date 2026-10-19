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
  liquidation_price: number
  margin_usd: number
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

export interface DailyLimitStatus {
  // `used_r` is NULLABLE and the null is meaningful: on the server's
  // fail-closed path (`used_r_from` returns `inf` for a non-finite daily total
  // so the breaker halts) Pydantic serialises `inf` as JSON `null`. Never
  // `?? 0` this — that renders a halted bot as a day with nothing consumed.
  used_r: number | null
  limit_r: number
  // Always a finite number: floored at 0 server-side, 0 on the halt path.
  remaining_r: number
}

export interface DailyTradesLimitStatus {
  used_trades: number
  limit_trades: number
  remaining_trades: number
}

export interface WeeklyLimitStatus {
  used_r: number
  limit_r: number
  remaining_r: number
  is_paused: boolean
}

export interface TodayStats {
  pnl_r: number
  pnl_usdt: number
  trades_count: number
  wins: number
  losses: number
  daily_limit_status: DailyLimitStatus
  daily_trades_limit_status: DailyTradesLimitStatus
  weekly_limit_status: WeeklyLimitStatus
}

export interface EquityPoint {
  date: string
  balance: number
}

interface DashboardState {
  botStatus: BotStatus | null
  positions: Position[]
  todayStats: TodayStats | null
  equityCurve: EquityPoint[]
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
          equity_curve: EquityPoint[]
          quick_stats: Record<string, unknown>
        }>(ENDPOINTS.dashboardOverview),
        api.get<TodayStats>(ENDPOINTS.dashboardToday),
      ])
      set({
        botStatus: overviewRes.data.bot_status,
        // The backend SPA catch-all (/{full_path:path}) can answer an /api/...
        // request with index.html at HTTP 200, so a missing array field must
        // never reach the page: undefined would throw on .length and take the
        // whole app shell down with it.
        positions: Array.isArray(overviewRes.data.open_positions) ? overviewRes.data.open_positions : [],
        equityCurve: Array.isArray(overviewRes.data.equity_curve) ? overviewRes.data.equity_curve : [],
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
    const state = get()
    switch (type) {
      case 'position_update':
      case 'pnl_update':
      case 'bot_status':
      case 'signal_update':
      case 'order_update':
        // Let pages handle specific updates
        break
      default:
        break
    }
  },
}))
