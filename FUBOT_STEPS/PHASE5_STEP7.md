# Phase 5 — Step 7: Scanner Page — Live Signals and Market Mood
## FUBOT: Real-time Signal Monitoring

---

## Objective
Build the Scanner page showing all pending/executed signals across symbols, their R:R ratios, strategy types, and market mood indicators (RSI zone, 50 SMA bias).

---

## Context
- **Depends on**: Phase 5 Steps 1–6
- **API**: `GET /api/signals`, `GET /api/signals/active`
- **Signal types**: FVG, 3CANDLE, CHoCH, BOS
- **Market mood**: RSI zone (<30 bullish, >70 bearish), 50 SMA bias (above/below price)

---

## Signal Card Component

### `frontend/src/components/signals/SignalCard.tsx`

```tsx
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { clsx } from 'clsx'
import { TrendingUp, TrendingDown, Clock, Target } from 'lucide-react'

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
  r_ risk: number
  status: 'PENDING' | 'EXECUTED' | 'SKIPPED' | 'EXPIRED'
  skip_reason?: string
  trend_bias: string
  rsi_zone: string
  volume_confirmed: boolean
  created_at: string
  executed_at?: string
}

interface SignalCardProps {
  signal: Signal
}

const strategyColors: Record<string, string> = {
  FVG: 'border-l-primary',
  '3CANDLE': 'border-l-warning',
  CHoCH: 'border-l-danger',
  BOS: 'border-l-success',
}

export default function SignalCard({ signal }: SignalCardProps) {
  const isLong = signal.direction === 'LONG'
  const rr = signal.rr_ratio

  return (
    <Card
      className={clsx(
        'p-4 border-l-4 border-l-border',
        strategyColors[signal.strategy_type] || 'border-l-border'
      )}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          {/* Direction Icon */}
          <div
            className={clsx(
              'w-8 h-8 rounded flex items-center justify-center',
              isLong ? 'bg-success/10' : 'bg-danger/10'
            )}
          >
            {isLong ? (
              <TrendingUp className="w-4 h-4 text-success" />
            ) : (
              <TrendingDown className="w-4 h-4 text-danger" />
            )}
          </div>
          <div>
            <p className="font-mono font-bold text-base">{signal.symbol}</p>
            <div className="flex items-center gap-2 mt-0.5">
              <Badge variant={isLong ? 'success' : 'danger'}>{signal.direction}</Badge>
              <Badge variant="neutral" className="font-mono text-xs uppercase">
                {signal.strategy_type}
              </Badge>
            </div>
          </div>
        </div>

        {/* Status */}
        <div className="text-right">
          <Badge
            variant={
              signal.status === 'EXECUTED'
                ? 'success'
                : signal.status === 'PENDING'
                ? 'info'
                : signal.status === 'SKIPPED'
                ? 'warning'
                : 'neutral'
            }
          >
            {signal.status}
          </Badge>
          {signal.skip_reason && (
            <p className="text-xs text-muted mt-1 max-w-[160px] text-right">
              {signal.skip_reason}
            </p>
          )}
        </div>
      </div>

      {/* Entry Zone + Risk/Reward */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Entry Zone</p>
          <p className="text-sm font-mono font-medium mt-0.5">
            {signal.entry_zone_min.toFixed(4)}
            <span className="text-muted text-xs"> – {signal.entry_zone_max.toFixed(4)}</span>
          </p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Stop Loss</p>
          <p className="text-sm font-mono text-danger font-medium mt-0.5">
            {signal.stop_loss.toFixed(4)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Take Profit</p>
          <p className="text-sm font-mono text-success font-medium mt-0.5">
            {signal.take_profit.toFixed(4)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">R:R</p>
          <p className="text-sm font-mono font-bold mt-0.5 flex items-center gap-1">
            <Target className="w-3 h-3 text-muted" />
            <span className={rr >= 3 ? 'text-success' : rr >= 2 ? 'text-warning' : 'text-danger'}>
              1:{rr.toFixed(1)}
            </span>
          </p>
        </div>
      </div>

      {/* Market Context */}
      <div className="flex items-center gap-4 text-xs text-muted border-t border-border/50 pt-2">
        <div className="flex items-center gap-1">
          <span className="font-medium text-text">Bias:</span>
          <span className={signal.trend_bias === 'BULLISH' ? 'text-success' : 'text-danger'}>
            {signal.trend_bias}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <span className="font-medium text-text">RSI:</span>
          <span
            className={
              signal.rsi_zone === 'OVERSOLD'
                ? 'text-success'
                : signal.rsi_zone === 'OVERBOUGHT'
                ? 'text-danger'
                : 'text-muted'
            }
          >
            {signal.rsi_zone}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <span className="font-medium text-text">Vol:</span>
          <span className={signal.volume_confirmed ? 'text-success' : 'text-danger'}>
            {signal.volume_confirmed ? 'Confirmed' : 'Weak'}
          </span>
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <Clock className="w-3 h-3" />
          <span>{new Date(signal.created_at).toLocaleTimeString()}</span>
        </div>
      </div>
    </Card>
  )
}
```

---

## Scanner Page

### `frontend/src/pages/Scanner.tsx`

```tsx
import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import SignalCard, { Signal } from '../components/signals/SignalCard'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { RefreshCw, Radio } from 'lucide-react'

type Filter = 'ALL' | 'PENDING' | 'EXECUTED' | 'SKIPPED'

export default function Scanner() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [filter, setFilter] = useState<Filter>('ALL')
  const [lastRefresh, setLastRefresh] = useState(new Date())
  const [isRefreshing, setIsRefreshing] = useState(false)

  const fetchSignals = useCallback(async (showLoader = true) => {
    if (showLoader) setIsLoading(true)
    try {
      const params: Record<string, string> = {}
      if (filter !== 'ALL') params.status = filter
      const res = await api.get<{ signals: Signal[] }>(ENDPOINTS.signals, { params })
      setSignals(res.data.signals)
      setLastRefresh(new Date())
    } catch (err) {
      console.error('Failed to fetch signals:', err)
    } finally {
      setIsLoading(false)
      setIsRefreshing(false)
    }
  }, [filter])

  useEffect(() => {
    fetchSignals()
  }, [fetchSignals])

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const id = setInterval(() => fetchSignals(false), 30000)
    return () => clearInterval(id)
  }, [fetchSignals])

  const handleRefresh = () => {
    setIsRefreshing(true)
    fetchSignals(false)
  }

  const pendingCount = signals.filter((s) => s.status === 'PENDING').length
  const executedCount = signals.filter((s) => s.status === 'EXECUTED').length
  const skippedCount = signals.filter((s) => s.status === 'SKIPPED').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Radio className="w-5 h-5 text-primary" />
            Scanner
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Live signal detection — auto-refreshes every 30s
          </p>
        </div>
        <div className="flex items-center gap-3">
          <p className="text-xs text-muted font-mono">
            Updated {lastRefresh.toLocaleTimeString()}
          </p>
          <button
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="p-2 rounded-md hover:bg-surface2 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 text-muted ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Pending', count: pendingCount, variant: 'info' as const },
          { label: 'Executed', count: executedCount, variant: 'success' as const },
          { label: 'Skipped', count: skippedCount, variant: 'warning' as const },
        ].map(({ label, count, variant }) => (
          <Card key={label} className="p-3">
            <p className="text-xs text-muted uppercase tracking-wide">{label}</p>
            <div className="flex items-end gap-2 mt-1">
              <p className="text-2xl font-mono font-bold">{count}</p>
              <Badge variant={variant}>{label}</Badge>
            </div>
          </Card>
        ))}
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {(['ALL', 'PENDING', 'EXECUTED', 'SKIPPED'] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              filter === f
                ? 'border-primary text-primary'
                : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {f.charAt(0) + f.slice(1).toLowerCase()}
            {f === 'ALL' && signals.length > 0 && (
              <span className="ml-2 text-xs bg-surface2 px-1.5 py-0.5 rounded font-mono">
                {signals.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Signals List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-muted">
          <div className="animate-spin w-6 h-6 border-2 border-primary border-t-transparent rounded-full mr-3" />
          Scanning markets...
        </div>
      ) : signals.length === 0 ? (
        <Card className="flex flex-col items-center justify-center py-20 text-muted">
          <Radio className="w-10 h-10 mb-4 opacity-20" />
          <p className="text-sm">No signals found</p>
          <p className="text-xs mt-1">
            {filter === 'ALL'
              ? 'Signals will appear when the bot detects setups'
              : `No ${filter.toLowerCase()} signals`}
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          {signals.map((signal) => (
            <SignalCard key={signal.id} signal={signal} />
          ))}
        </div>
      )}
    </div>
  )
}
```

---

## Deliverables

1. **`frontend/src/components/signals/SignalCard.tsx`** — Card showing symbol, direction, strategy type, entry zone, SL, TP, R:R, trend bias, RSI zone, volume, status
2. **`frontend/src/pages/Scanner.tsx`** — Full page with signal count summary, filter tabs (All/Pending/Executed/Skipped), auto-refresh every 30s, manual refresh button
3. **WebSocket live updates** — New signals appear automatically (via `signal_update` WS message type)
4. **Empty state** — Shows appropriate message when no signals match filter

---

## Validation

```bash
cd /home/vick/fubot/frontend && npm run dev
# Visit http://localhost:5173/scanner
# - Shows signal cards or "No signals found" empty state
# - Filter tabs switch correctly
# - Refresh button triggers a manual reload
# - After 30 seconds, page auto-refreshes signals
```

---

## Next Step
Phase 5 Step 8: Performance Page — Equity Curve, Monthly Returns, R Distribution

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
