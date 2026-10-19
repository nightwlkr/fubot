import { useState, useEffect } from 'react'
import { api, errorDetail } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { clsx } from 'clsx'
import { Save, Eye, EyeOff, ShieldCheck, AlertTriangle, RefreshCw } from 'lucide-react'

interface APIKeyStatus {
  has_key: boolean
  masked_key: string | null
}

interface TradingSettings {
  risk_percent: number
  max_positions: number
  daily_loss_limit_r: number
  rr_min: number
  check_interval_seconds: number
  paper_use_real_balance: boolean
  paper_balance_usdt: number
  trade_neutral_markets: boolean
  trade_min_trend_strength: string
  volume_filter_enabled: boolean
  symbol_cooldown_minutes: number
  max_daily_trades: number
  max_position_notional_pct: number
  be_trail_trigger_r: number
  profit_lock_trigger_r: number
  profit_lock_trail_r: number
  profit_lock_trigger_r2: number
  profit_lock_trail_r2: number
  stale_trade_hours: number
  strategy_obfvg_enabled: boolean
  strategy_obfvg_max_age_bars: number
  weekly_drawdown_limit_r: number
}

interface SymbolConfig {
  symbol: string
  enabled: boolean
  max_leverage: number
}

function APIKeySection() {
  const [status, setStatus] = useState<APIKeyStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<APIKeyStatus>('/settings/apikey')
      .then(res => { setStatus(res.data); setIsLoading(false) })
      .catch(() => setIsLoading(false))
  }, [])

  const handleSave = async () => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      setError('Both API Key and Secret are required')
      return
    }
    setError('')
    setIsSaving(true)
    try {
      await api.post('/settings/apikey', { api_key: apiKey.trim(), api_secret: apiSecret.trim() })
      setSaved(true)
      setApiKey('')
      setApiSecret('')
      setTimeout(() => setSaved(false), 3000)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save API key')
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return <div className="h-32 flex items-center justify-center text-muted">Loading...</div>
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-primary" />
          <CardTitle>Binance API Key</CardTitle>
        </div>
        {status?.has_key && (
          <Badge variant="success"><ShieldCheck className="w-3 h-3 mr-1" />Configured</Badge>
        )}
      </CardHeader>

      <div className="space-y-4">
        {status?.has_key && (
          <div className="bg-surface2 rounded px-3 py-2 text-sm font-mono">
            <span className="text-muted">Current: </span>
            {status.masked_key || '**** **** ****'}
          </div>
        )}

        <div className="p-3 bg-warning/10 border border-warning/30 rounded-md">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-warning mt-0.5 shrink-0" />
            <p className="text-xs text-warning leading-relaxed">
              Your API key is encrypted with AES-256 and stored securely. It is never logged or
              exposed in plain text. Ensure your API key has <strong>Futures trading permission</strong> and
              no withdrawal access.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">API Key</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Your Binance API key"
                className="w-full bg-surface2 border border-border rounded-md px-3 py-2 pr-10 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/50"
              />
              <button type="button" onClick={() => setShowKey(!showKey)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-text">
                {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">API Secret</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder="Your Binance API secret"
                className="w-full bg-surface2 border border-border rounded-md px-3 py-2 pr-10 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/50"
              />
              <button type="button" onClick={() => setShowKey(!showKey)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-text">
                {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
        </div>

        {error && (
          <p className="text-danger text-sm bg-danger/10 border border-danger/30 rounded px-3 py-2">{error}</p>
        )}

        {saved && (
          <p className="text-success text-sm bg-success/10 border border-success/30 rounded px-3 py-2">API key saved successfully!</p>
        )}

        <div className="flex items-center justify-between">
          <ExchangeSyncButton />
          <Button onClick={handleSave} isLoading={isSaving} disabled={isSaving || !apiKey || !apiSecret}>
            <Save className="w-4 h-4" />
            Save API Key
          </Button>
        </div>
      </div>
    </Card>
  )
}

function ExchangeSyncButton() {
  const [isSyncing, setIsSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<string | null>(null)

  const handleSync = async () => {
    setIsSyncing(true)
    setSyncResult(null)
    try {
      const res = await api.post<{ checked: number; fixed: number }>('/settings/exchange-sync')
      const { checked, fixed } = res.data
      setSyncResult(`Synced: ${checked} trades checked, ${fixed} fixed`)
      setTimeout(() => setSyncResult(null), 8000)
    } catch (err: any) {
      setSyncResult(`Error: ${err.response?.data?.detail || 'Sync failed'}`)
      setTimeout(() => setSyncResult(null), 8000)
    } finally {
      setIsSyncing(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <Button variant="secondary" onClick={handleSync} isLoading={isSyncing} disabled={isSyncing}>
        <RefreshCw className="w-4 h-4" />
        Exchange Sync
      </Button>
      {syncResult && (
        <span className={`text-sm ${syncResult.startsWith('Error') ? 'text-danger' : 'text-success'}`}>
          {syncResult}
        </span>
      )}
    </div>
  )
}

function TradingParamsSection() {
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [riskPercent, setRiskPercent] = useState(2)
  const [maxPositions, setMaxPositions] = useState(5)
  const [dailyLossLimit, setDailyLossLimit] = useState(3)
  const [rrMin, setRrMin] = useState(3.0)
  const [checkInterval, setCheckInterval] = useState(60)
  const [paperUseRealBalance, setPaperUseRealBalance] = useState(true)
  const [paperBalanceUsdt, setPaperBalanceUsdt] = useState(10000)
  const [tradeNeutralMarkets, setTradeNeutralMarkets] = useState(true)
  const [tradeMinTrendStrength, setTradeMinTrendStrength] = useState('MODERATE')
  const [volumeFilterEnabled, setVolumeFilterEnabled] = useState(false)
  const [symbolCooldownMinutes, setSymbolCooldownMinutes] = useState(240)
  const [maxDailyTrades, setMaxDailyTrades] = useState(12)
  const [maxPositionNotionalPct, setMaxPositionNotionalPct] = useState(50)
  const [beTrailTriggerR, setBeTrailTriggerR] = useState(0.5)
  const [profitLockTriggerR, setProfitLockTriggerR] = useState(1.0)
  const [profitLockTrailR, setProfitLockTrailR] = useState(0.5)
  const [profitLockTriggerR2, setProfitLockTriggerR2] = useState(1.0)
  const [profitLockTrailR2, setProfitLockTrailR2] = useState(0.25)
  const [staleTradeHours, setStaleTradeHours] = useState(24)
  const [obfvgEnabled, setObfvgEnabled] = useState(true)
  const [obfvgMaxAgeBars, setObfvgMaxAgeBars] = useState(2)
  const [weeklyDrawdownLimit, setWeeklyDrawdownLimit] = useState(5.0)

  useEffect(() => {
    api.get<TradingSettings>(ENDPOINTS.settingsTrading)
      .then(res => {
        const s = res.data
        setRiskPercent(s.risk_percent)
        setMaxPositions(s.max_positions)
        // Stored negative (e.g. -3.0) and consumed via abs() everywhere, but
        // the slider is a positive-magnitude control with min: 1 — a controlled
        // range clamps an out-of-range value to min, so the label used to read
        // "1R" for a real 3R stop-out threshold.
        setDailyLossLimit(Math.abs(s.daily_loss_limit_r))
        setRrMin(s.rr_min)
        setCheckInterval(s.check_interval_seconds)
        setPaperUseRealBalance(s.paper_use_real_balance ?? true)
        setPaperBalanceUsdt(s.paper_balance_usdt ?? 10000)
        setTradeNeutralMarkets(s.trade_neutral_markets ?? true)
        setTradeMinTrendStrength(s.trade_min_trend_strength ?? 'MODERATE')
        setVolumeFilterEnabled(s.volume_filter_enabled ?? false)
        setSymbolCooldownMinutes(s.symbol_cooldown_minutes ?? 240)
        setMaxDailyTrades(s.max_daily_trades ?? 12)
        setMaxPositionNotionalPct(s.max_position_notional_pct ?? 50)
        setBeTrailTriggerR(s.be_trail_trigger_r ?? 0.5)
        setProfitLockTriggerR(s.profit_lock_trigger_r ?? 1.0)
        setProfitLockTrailR(s.profit_lock_trail_r ?? 0.5)
        setProfitLockTriggerR2(s.profit_lock_trigger_r2 ?? 1.0)
        setProfitLockTrailR2(s.profit_lock_trail_r2 ?? 0.25)
        setStaleTradeHours(s.stale_trade_hours ?? 24)
        setObfvgEnabled(s.strategy_obfvg_enabled ?? true)
        setObfvgMaxAgeBars(s.strategy_obfvg_max_age_bars ?? 2)
        setWeeklyDrawdownLimit(s.weekly_drawdown_limit_r ?? 5.0)
        setIsLoading(false)
      })
      .catch(() => setIsLoading(false))
  }, [])

  const handleSave = async () => {
    setIsSaving(true)
    setSaveError(null)
    try {
      await api.put(ENDPOINTS.settingsTrading, {
        risk_percent: riskPercent,
        max_positions: maxPositions,
        // The slider holds a positive magnitude; the stored convention is
        // negative (get_limit_r() takes abs(), so both read the same).
        daily_loss_limit_r: -dailyLossLimit,
        rr_min: rrMin,
        check_interval_seconds: checkInterval,
        paper_use_real_balance: paperUseRealBalance,
        paper_balance_usdt: paperBalanceUsdt,
        trade_neutral_markets: tradeNeutralMarkets,
        trade_min_trend_strength: tradeMinTrendStrength,
        volume_filter_enabled: volumeFilterEnabled,
        symbol_cooldown_minutes: symbolCooldownMinutes,
        max_daily_trades: maxDailyTrades,
        max_position_notional_pct: maxPositionNotionalPct,
        be_trail_trigger_r: beTrailTriggerR,
        profit_lock_trigger_r: profitLockTriggerR,
        profit_lock_trail_r: profitLockTrailR,
        profit_lock_trigger_r2: profitLockTriggerR2,
        profit_lock_trail_r2: profitLockTrailR2,
        stale_trade_hours: staleTradeHours,
        strategy_obfvg_enabled: obfvgEnabled,
        strategy_obfvg_max_age_bars: obfvgMaxAgeBars,
        weekly_drawdown_limit_r: weeklyDrawdownLimit,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      console.error('Failed to save trading settings:', err)
      // Silent failure here is money-relevant (Risk Per Trade / Max Position
      // Notional): the operator must not believe a rejected change is live.
      setSaveError(errorDetail(err, 'Failed to save trading parameters — changes were NOT applied'))
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return <div className="h-32 flex items-center justify-center text-muted">Loading...</div>
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trading Parameters</CardTitle>
        {saved && <Badge variant="success">Saved!</Badge>}
      </CardHeader>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        {[
          { label: 'Risk Per Trade', value: riskPercent, setValue: setRiskPercent, min: 0.5, max: 20, step: 0.5, suffix: '%', desc: 'Percentage of balance risked per trade (high risk = larger swings)' },
          { label: 'Max Position Notional', value: maxPositionNotionalPct, setValue: setMaxPositionNotionalPct, min: 50, max: 1000, step: 50, suffix: '%', desc: 'Hard cap on position notional as % of balance. This CAP overrides Risk Per Trade when it binds: with ~1% stops, 50% caps risk at ~$1-2 per $200 balance (raise to ~500% to actually risk 5%)' },
          { label: 'BE Trail Trigger', value: beTrailTriggerR, setValue: setBeTrailTriggerR, min: 0, max: 3, step: 0.25, suffix: 'R', desc: 'Move the stop to break-even once the trade is this far in profit. 0 = immediately (many scratches), 0.5 = let it prove itself first' },
          { label: 'Profit Lock Trigger', value: profitLockTriggerR, setValue: setProfitLockTriggerR, min: 0.5, max: 3, step: 0.25, suffix: 'R', desc: 'At this profit level, start trailing the stop to lock in gains. 1.0 = lock once trade is +1R deep' },
          { label: 'Profit Lock Trail', value: profitLockTrailR, setValue: setProfitLockTrailR, min: 0.1, max: 2, step: 0.1, suffix: 'R', desc: 'How far behind price (in R) the profit-lock stop sits. 0.5 = locks in at least half the current profit' },
          { label: 'Profit Lock 2 Trigger', value: profitLockTriggerR2, setValue: setProfitLockTriggerR2, min: 0.5, max: 5, step: 0.25, suffix: 'R', desc: 'Second profit lock stage — activates when trade reaches this R level. 1.0 = triggers at +1R' },
          { label: 'Profit Lock 2 Trail', value: profitLockTrailR2, setValue: setProfitLockTrailR2, min: 0.05, max: 2, step: 0.05, suffix: 'R', desc: 'Tight trailing distance for stage 2. 0.25 = secures +0.75R if stopped (locks $5.17 on a $6.89 risk trade)' },
          { label: 'Stale Trade Exit', value: staleTradeHours, setValue: setStaleTradeHours, min: 0, max: 72, step: 1, suffix: 'h', desc: 'Close profitable trades after this many hours if they haven\'t hit TP. 0 = disabled. Prevents stale trades from tying up margin' },
          { label: 'Max Open Positions', value: maxPositions, setValue: setMaxPositions, min: 1, max: 10, step: 1, suffix: '', desc: 'Maximum concurrent positions' },
          { label: 'Daily Loss Limit', value: dailyLossLimit, setValue: setDailyLossLimit, min: 1, max: 10, step: 0.5, suffix: 'R', desc: 'Bot stops trading after this loss (in R)' },
          { label: 'Weekly Drawdown Limit', value: weeklyDrawdownLimit, setValue: setWeeklyDrawdownLimit, min: 1, max: 20, step: 0.5, suffix: 'R', desc: 'Bot halts when weekly losses exceed this threshold. Reset via Bot Controls if tripped' },
          { label: 'Min R:R Ratio', value: rrMin, setValue: setRrMin, min: 1.5, max: 5, step: 0.5, suffix: ':1', desc: 'Minimum risk:reward to execute a trade' },
          { label: 'Check Interval', value: checkInterval, setValue: setCheckInterval, min: 30, max: 300, step: 30, suffix: 's', desc: 'How often the bot scans for signals' },
        ].map(({ label, value, setValue, min, max, step, suffix, desc }) => (
          <div key={label}>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">{label}</label>
            <div className="flex items-center gap-3">
              <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => setValue(Number(e.target.value))} className="flex-1 accent-primary" />
              <span className="text-sm font-mono font-bold w-16 text-right">{value}{suffix}</span>
            </div>
            <p className="text-xs text-muted mt-1">{desc}</p>
          </div>
        ))}
      </div>

      {/* Paper balance source */}
      <div className="mt-6 pt-4 border-t border-border">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-muted uppercase tracking-wide">Paper Balance Source</p>
            <p className="text-xs text-muted mt-1">
              Size paper trades against your real Binance USDT balance (availableBalance), or a fixed simulated balance.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={paperUseRealBalance}
            onClick={() => setPaperUseRealBalance(v => !v)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${paperUseRealBalance ? 'bg-primary' : 'bg-surface2'}`}
          >
            <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${paperUseRealBalance ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>
        <p className="text-xs mt-2">
          <span className={`font-medium ${paperUseRealBalance ? 'text-primary' : 'text-muted'}`}>
            {paperUseRealBalance ? 'Real balance (recommended)' : 'Simulated balance'}
          </span>
          {!paperUseRealBalance && (
            <span className="text-muted"> — fixed USDT balance used for paper sizing</span>
          )}
        </p>
        {!paperUseRealBalance && (
          <div className="mt-3 max-w-xs">
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Simulated Balance (USDT)</label>
            <input
              type="number"
              min={1}
              value={paperBalanceUsdt}
              onChange={(e) => setPaperBalanceUsdt(Number(e.target.value))}
              className="w-full bg-surface2 border border-border rounded-md px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          </div>
        )}
      </div>

      {/* Strategies — per-strategy on/off. A "disabled" strategy must be
          toggled here or the scanner keeps firing it. */}
      <div className="mt-6 pt-4 border-t border-border">
        <p className="text-xs text-muted uppercase tracking-wide mb-3">Strategies</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="flex items-center justify-between">
            <div className="pr-3">
              <p className="text-sm">Order Block + FVG</p>
              <p className="text-xs text-muted">1h gap confirmed by a 5m order block (backtest: +0.32R)</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={obfvgEnabled}
              onClick={() => setObfvgEnabled(v => !v)}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${obfvgEnabled ? 'bg-primary' : 'bg-surface2'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${obfvgEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">OB+FVG Gap Age</label>
            <div className="flex items-center gap-3">
              <input type="range" min={1} max={12} step={1} value={obfvgMaxAgeBars} onChange={(e) => setObfvgMaxAgeBars(Number(e.target.value))} className="flex-1 accent-primary" />
              <span className="text-sm font-mono font-bold w-16 text-right">{obfvgMaxAgeBars}h</span>
            </div>
            <p className="text-xs text-muted mt-1">Only gaps formed within this many closed 1h bars are traded</p>
          </div>
        </div>
      </div>

      {/* Strategy Filters — toggles for the signal-pipeline gates */}
      <div className="mt-6 pt-4 border-t border-border">
        <p className="text-xs text-muted uppercase tracking-wide mb-3">Strategy Filters</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="flex items-center justify-between">
            <div className="pr-3">
              <p className="text-sm">Allow Neutral Markets</p>
              <p className="text-xs text-muted">Trade when the trend is choppy/directionless</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={tradeNeutralMarkets}
              onClick={() => setTradeNeutralMarkets(v => !v)}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${tradeNeutralMarkets ? 'bg-primary' : 'bg-surface2'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${tradeNeutralMarkets ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div className="flex items-center justify-between">
            <div className="pr-3">
              <p className="text-sm">Volume Filter</p>
              <p className="text-xs text-muted">Require dominant-side volume (backtest: reduces profit)</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={volumeFilterEnabled}
              onClick={() => setVolumeFilterEnabled(v => !v)}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${volumeFilterEnabled ? 'bg-primary' : 'bg-surface2'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${volumeFilterEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Min Trend Strength</label>
            <select
              value={tradeMinTrendStrength}
              onChange={(e) => setTradeMinTrendStrength(e.target.value)}
              className="w-full bg-surface2 border border-border rounded-md px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              <option value="WEAK">Weak (most trades)</option>
              <option value="MODERATE">Moderate (balanced)</option>
              <option value="STRONG">Strong (fewest, highest conviction)</option>
            </select>
            <p className="text-xs text-muted mt-1">Minimum aligned-trend strength to enter</p>
          </div>
        </div>
      </div>

      {/* Overtrading guards */}
      <div className="mt-6 pt-4 border-t border-border">
        <p className="text-xs text-muted uppercase tracking-wide mb-3">Overtrading Guards</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Max Daily Trades</label>
            <div className="flex items-center gap-3">
              <input type="range" min={0} max={50} step={1} value={maxDailyTrades} onChange={(e) => setMaxDailyTrades(Number(e.target.value))} className="flex-1 accent-primary" />
              <span className="text-sm font-mono font-bold w-16 text-right">{maxDailyTrades === 0 ? '∞' : maxDailyTrades}</span>
            </div>
            <p className="text-xs text-muted mt-1">Bot stops opening new trades after this many per day (0 = no cap)</p>
          </div>
          <div>
            <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Per-Symbol Cooldown</label>
            <div className="flex items-center gap-3">
              <input type="range" min={0} max={1440} step={30} value={symbolCooldownMinutes} onChange={(e) => setSymbolCooldownMinutes(Number(e.target.value))} className="flex-1 accent-primary" />
              <span className="text-sm font-mono font-bold w-16 text-right">{symbolCooldownMinutes < 60 ? `${symbolCooldownMinutes}m` : `${Math.floor(symbolCooldownMinutes / 60)}h`}</span>
            </div>
            <p className="text-xs text-muted mt-1">Wait before re-entering the same symbol (0 = no cooldown)</p>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-3 mt-6 pt-4 border-t border-border">
        {saveError && (
          <span className="text-xs text-danger text-right">{saveError}</span>
        )}
        <Button onClick={handleSave} isLoading={isSaving}>
          <Save className="w-4 h-4" />
          Save Parameters
        </Button>
      </div>
    </Card>
  )
}

function SymbolConfigSection() {
  const [symbols, setSymbols] = useState<SymbolConfig[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<{ symbols: SymbolConfig[] }>(ENDPOINTS.settingsSymbols)
      .then(res => setSymbols(Array.isArray(res.data.symbols) ? res.data.symbols : []))
      .catch(console.error)
      .finally(() => setIsLoading(false))
  }, [])

  const toggleSymbol = async (symbol: string, enabled: boolean) => {
    setError(null)
    try {
      await api.put(ENDPOINTS.settingsSymbol(symbol), { enabled })
      setSymbols(prev => prev.map(s => s.symbol === symbol ? { ...s, enabled } : s))
    } catch (err) {
      console.error('Failed to toggle symbol:', err)
      // Reverted silently before: the operator saw the old state with no idea
      // the request was rejected.
      setError(errorDetail(err, `Failed to ${enabled ? 'enable' : 'disable'} ${symbol} — unchanged`))
    }
  }

  const updateLeverage = async (symbol: string, max_leverage: number) => {
    setError(null)
    try {
      await api.put(ENDPOINTS.settingsSymbol(symbol), { max_leverage })
      setSymbols(prev => prev.map(s => s.symbol === symbol ? { ...s, max_leverage } : s))
    } catch (err) {
      console.error('Failed to update leverage:', err)
      setError(errorDetail(err, `Failed to set ${symbol} leverage to ${max_leverage}x — unchanged`))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Symbol Configuration</CardTitle>
      </CardHeader>

      {error && (
        <p className="text-xs text-danger px-4 pb-2">{error}</p>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-muted">Loading symbols...</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-xs font-medium text-muted uppercase tracking-wide py-3 px-4">Symbol</th>
                <th className="text-center text-xs font-medium text-muted uppercase tracking-wide py-3 px-4">Enabled</th>
                <th className="text-center text-xs font-medium text-muted uppercase tracking-wide py-3 px-4">Max Leverage</th>
              </tr>
            </thead>
            <tbody>
              {symbols.map(sym => (
                <tr key={sym.symbol} className="border-b border-border/50 hover:bg-surface2/30">
                  <td className="py-3 px-4 font-mono font-bold">{sym.symbol}</td>
                  <td className="py-3 px-4 text-center">
                    <button
                      onClick={() => toggleSymbol(sym.symbol, !sym.enabled)}
                      className={clsx(
                        'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                        sym.enabled ? 'bg-success' : 'bg-surface2'
                      )}
                    >
                      <span
                        className={clsx(
                          'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
                          sym.enabled ? 'translate-x-6' : 'translate-x-1'
                        )}
                      />
                    </button>
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center justify-center gap-2">
                      <input
                        type="range" min={1} max={20} value={sym.max_leverage}
                        onChange={(e) => updateLeverage(sym.symbol, Number(e.target.value))}
                        className="w-20 accent-primary"
                        disabled={!sym.enabled}
                      />
                      <span className="text-xs font-mono w-8">{sym.max_leverage}x</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function Settings() {
  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted mt-0.5">Configure API keys, trading parameters, and symbol settings</p>
      </div>

      <APIKeySection />
      <TradingParamsSection />
      <SymbolConfigSection />
    </div>
  )
}
