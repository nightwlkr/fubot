export interface Signal {
  id: number
  symbol: string
  strategy_type: string
  direction: 'LONG' | 'SHORT'
  entry_zone_min: number
  entry_zone_max: number
  stop_loss: number
  take_profit: number
  rr_ratio: number
  volume_confirmed: boolean
  status: 'PENDING' | 'TRIGGERED' | 'EXPIRED' | 'SKIPPED'
  created_at: string
  skip_reason?: string
}

export interface Trade {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  entry_price: number
  exit_price: number
  quantity: number
  pnl_usdt: number
  pnl_r: number
  exit_reason: string
  strategy_type: string
  closed_at: string
}

export interface TradeStats {
  total_trades: number
  win_rate: number
  avg_r: number
  best_trade: number
  worst_trade: number
  wins: number
  losses: number
  /** Neither a win nor a loss. `wins + losses` will not reconcile with
   *  `total_trades` without it, so the Wins tile shows it as a suffix. */
  breakevens: number
  /** Summed dollar P&L. Every R figure on this page is normalised, and 1R
   *  varies with whether the notional cap bound, so USD is the honest total. */
  total_pnl_usdt: number
}

export interface StrategyBreakdown {
  strategy: string
  total_trades: number
  wins: number
  losses: number
  breakevens: number
  win_rate: number
  sum_r: number
  avg_r: number
  /** Gross win / gross loss. null when the strategy has no losing trade —
   *  undefined arithmetic, so NOT 0.0 (which would rank it last). */
  profit_factor: number | null
  total_pnl_usdt: number
}

export interface MonthlyReturn {
  month: string
  pnl_r: number
  trades: number
  win_rate: number
}

export interface TradingSettings {
  risk_percent: number
  max_positions: number
  daily_loss_limit_r: number
  rr_min: number
  check_interval_seconds: number
  leverage_btc: number
  leverage_alts: number
  strategy_fvg_enabled: boolean
  strategy_3candle_enabled: boolean
  strategy_choch_enabled: boolean
  strategy_obfvg_enabled: boolean
  strategy_obfvg_max_age_bars: number
}

export interface SymbolConfig {
  symbol: string
  enabled: boolean
  max_leverage: number
}
