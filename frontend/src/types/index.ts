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
}

export interface SymbolConfig {
  symbol: string
  enabled: boolean
  max_leverage: number
}
