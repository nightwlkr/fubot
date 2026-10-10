# Phase 5 — Step 4: Overview Page — Dashboard Cards
## FUBOT: Real-time Overview with BotStatusCard, PnLCard, PositionsTable, EquityCurve

---

## Objective
Build the main Overview page with four dashboard sections: BotStatusCard (start/stop + mode), PnLCard (today's P&L + daily limit), PositionsTable (open positions with live mark price), and EquityCurve (7-day balance chart).

---

## Context
- **Depends on**: Phase 5 Steps 1–3 (layout, auth, stores)
- **Data source**: `GET /api/dashboard/overview` + `GET /api/dashboard/today`
- **Live updates**: WebSocket pushes `position_update`, `pnl_update`, `bot_status`
- **TradingView**: Use `lightweight-charts` for the equity curve

---

## BotStatusCard

### `frontend/src/components/dashboard/BotStatusCard.tsx`

```tsx
import { useState } from 'react'
import { Card, CardHeader, CardTitle } from '../ui/Card'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import { useDashboardStore } from '../../store/dashboardStore'
import { clsx } from 'clsx'
import { Play, Square, Loader2 } from 'lucide-react'

export default function BotStatusCard() {
  const { botStatus, startBot, stopBot } = useDashboardStore()
  const [isTransitioning, setIsTransitioning] = useState(false)

  const isRunning = botStatus?.status === 'running'
  const mode = botStatus?.mode || 'paper'

  const handleToggle = async () => {
    setIsTransitioning(true)
    try {
      if (isRunning) {
        await stopBot()
      } else {
        await startBot(mode)
      }
    } finally {
      setIsTransitioning(false)
    }
  }

  const uptime = botStatus?.uptime_seconds ?? 0
  const hours = Math.floor(uptime / 3600)
  const minutes = Math.floor((uptime % 3600) / 60)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Bot Status</CardTitle>
        <Badge variant={isRunning ? 'success' : 'neutral'}>
          {isRunning ? 'RUNNING' : 'STOPPED'}
        </Badge>
      </CardHeader>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-muted uppercase tracking-wide">Mode</p>
            <div className="flex gap-2 mt-1">
              <button
                onClick={() => !isRunning && startBot('paper')}
                disabled={isRunning}
                className={clsx(
                  'px-3 py-1 rounded text-xs font-mono font-medium border transition-colors',
                  mode === 'paper'
                    ? 'border-warning bg-warning/10 text-warning'
                    : 'border-border text-muted hover:text-text',
                  isRunning && 'opacity-50 cursor-not-allowed'
                )}
              >
                PAPER
              </button>
              <button
                onClick={() => !isRunning && startBot('live')}
                disabled={isRunning}
                className={clsx(
                  'px-3 py-1 rounded text-xs font-mono font-medium border transition-colors',
                  mode === 'live'
                    ? 'border-danger bg-danger/10 text-danger'
                    : 'border-border text-muted hover:text-text',
                  isRunning && 'opacity-50 cursor-not-allowed'
                )}
              >
                LIVE
              </button>
            </div>
          </div>

          <Button
            variant={isRunning ? 'danger' : 'primary'}
            size="md"
            onClick={handleToggle}
            isLoading={isTransitioning}
            disabled={isTransitioning}
          >
            {isTransitioning ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : isRunning ? (
              <>
                <Square className="w-4 h-4" /> Stop
              </>
            ) : (
              <>
                <Play className="w-4 h-4" /> Start
              </>
            )}
          </Button>
        </div>

        <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border/50">
          <div>
            <p className="text-xs text-muted uppercase tracking-wide">Uptime</p>
            <p className="text-lg font-mono font-bold mt-0.5">
              {isRunning ? `${hours}h ${minutes}m` : '—'}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted uppercase tracking-wide">Scans</p>
            <p className="text-lg font-mono font-bold mt-0.5">
              {botStatus?.scan_count?.toLocaleString() ?? '—'}
            </p>
          </div>
        </div>

        {botStatus?.last_scan && (
          <p className="text-xs text-muted font-mono">
            Last scan: {new Date(botStatus.last_scan).toLocaleTimeString()}
          </p>
        )}
      </div>
    </Card>
  )
}
```

---

## PnLCard

### `frontend/src/components/dashboard/PnLCard.tsx`

```tsx
import { Card, CardHeader, CardTitle } from '../ui/Card'
import { useDashboardStore } from '../../store/dashboardStore'
import { clsx } from 'clsx'
import { TrendingUp, TrendingDown, Target } from 'lucide-react'

export default function PnLCard() {
  const { todayStats } = useDashboardStore()

  const pnlR = todayStats?.pnl_r ?? 0
  const pnlUsdt = todayStats?.pnl_usdt ?? 0
  const wins = todayStats?.wins ?? 0
  const losses = todayStats?.losses ?? 0
  const trades = todayStats?.trades_count ?? 0
  const limit = todayStats?.daily_limit_status?.limit_r ?? 3
  const used = todayStats?.daily_limit_status?.used_r ?? 0
  const remaining = limit - used

  // Progress bar (0 to limit)
  const progressPct = Math.min(100, (used / limit) * 100)
  const isNearLimit = remaining <= 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>Today's Performance</CardTitle>
      </CardHeader>

      {/* Main P&L */}
      <div className="flex items-end gap-4 mb-4">
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">P&L</p>
          <p
            className={clsx(
              'text-3xl font-mono font-bold mt-0.5',
              pnlR >= 0 ? 'text-success' : 'text-danger'
            )}
          >
            {pnlR >= 0 ? '+' : ''}{pnlR.toFixed(2)}R
          </p>
          <p
            className={clsx(
              'text-sm font-mono mt-0.5',
              pnlUsdt >= 0 ? 'text-success/70' : 'text-danger/70'
            )}
          >
            {pnlUsdt >= 0 ? '+' : ''}{pnlUsdt.toFixed(2)} USDT
          </p>
        </div>
        <div className="flex-1" />
        <div className="text-right">
          {pnlR >= 0 ? (
            <TrendingUp className="w-6 h-6 text-success mb-1 ml-auto" />
          ) : (
            <TrendingDown className="w-6 h-6 text-danger mb-1 ml-auto" />
          )}
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        <div className="bg-surface2 rounded px-3 py-2 text-center">
          <p className="text-xs text-muted">Trades</p>
          <p className="text-lg font-mono font-bold mt-0.5">{trades}</p>
        </div>
        <div className="bg-surface2 rounded px-3 py-2 text-center">
          <p className="text-xs text-muted">Wins</p>
          <p className="text-lg font-mono font-bold text-success mt-0.5">{wins}</p>
        </div>
        <div className="bg-surface2 rounded px-3 py-2 text-center">
          <p className="text-xs text-muted">Losses</p>
          <p className="text-lg font-mono font-bold text-danger mt-0.5">{losses}</p>
        </div>
      </div>

      {/* Daily Loss Limit Progress */}
      <div>
        <div className="flex justify-between text-xs mb-1.5">
          <span className="text-muted uppercase tracking-wide">Daily Limit</span>
          <span className={clsx('font-mono', isNearLimit ? 'text-danger' : 'text-muted')}>
            {used.toFixed(2)}R / {limit}R
            {remaining > 0 && <span className="text-success ml-1">({remaining.toFixed(2)}R left)</span>}
          </span>
        </div>
        <div className="h-2 bg-surface2 rounded-full overflow-hidden">
          <div
            className={clsx(
              'h-full rounded-full transition-all duration-500',
              progressPct < 66 ? 'bg-success' : progressPct < 90 ? 'bg-warning' : 'bg-danger'
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>
    </Card>
  )
}
```

---

## PositionsTable

### `frontend/src/components/dashboard/PositionsTable.tsx`

```tsx
import { Card, CardHeader, CardTitle } from '../ui/Card'
import { Table, TableHead, TableRow, TableHeadCell, TableCell } from '../ui/Table'
import { Badge } from '../ui/Badge'
import { useDashboardStore } from '../../store/dashboardStore'
import { clsx } from 'clsx'
import { ExternalLink } from 'lucide-react'

function formatPrice(p: number) {
  return p >= 1000 ? p.toFixed(2) : p >= 1 ? p.toFixed(4) : p.toFixed(6)
}

function PnLBadge({ value }: { value: number }) {
  const isPositive = value >= 0
  return (
    <span
      className={clsx(
        'font-mono font-bold text-sm',
        isPositive ? 'text-success' : 'text-danger'
      )}
    >
      {isPositive ? '+' : ''}{value.toFixed(2)}R
    </span>
  )
}

export default function PositionsTable() {
  const { positions } = useDashboardStore()

  if (positions.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Open Positions</CardTitle>
          <Badge variant="neutral">{0}</Badge>
        </CardHeader>
        <div className="flex flex-col items-center justify-center py-12 text-muted">
          <ExternalLink className="w-8 h-8 mb-3 opacity-30" />
          <p className="text-sm">No open positions</p>
          <p className="text-xs mt-1">Bot will open positions when signals fire</p>
        </div>
      </Card>
    )
  }

  return (
    <Card className="p-0 overflow-hidden">
      <CardHeader className="px-6 pt-4 pb-2">
        <CardTitle>Open Positions</CardTitle>
        <Badge variant="info">{positions.length}</Badge>
      </CardHeader>
      <Table>
        <TableHead>
          <TableRow>
            <TableHeadCell>Symbol</TableHeadCell>
            <TableHeadCell>Side</TableHeadCell>
            <TableHeadCell className="text-right">Size</TableHeadCell>
            <TableHeadCell className="text-right">Entry</TableHeadCell>
            <TableHeadCell className="text-right">Mark</TableHeadCell>
            <TableHeadCell className="text-right">PnL (R)</TableHeadCell>
            <TableHeadCell className="text-right">Lev</TableHeadCell>
          </TableRow>
        </TableHead>
        <tbody>
          {positions.map((pos) => (
            <tr
              key={pos.id}
              className="border-b border-border/50 hover:bg-surface2/50 transition-colors"
            >
              <TableCell className="font-mono font-bold">{pos.symbol}</TableCell>
              <TableCell>
                <Badge variant={pos.side === 'LONG' ? 'success' : 'danger'}>
                  {pos.side}
                </Badge>
              </TableCell>
              <TableCell className="text-right font-mono text-sm">
                {pos.quantity.toFixed(4)}
              </TableCell>
              <TableCell className="text-right font-mono text-sm">
                {formatPrice(pos.entry_price)}
              </TableCell>
              <TableCell className="text-right font-mono text-sm">
                {formatPrice(pos.mark_price)}
              </TableCell>
              <TableCell className="text-right">
                <PnLBadge value={pos.unrealized_pnl / 100} />
              </TableCell>
              <TableCell className="text-right font-mono text-sm">
                {pos.leverage}x
              </TableCell>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  )
}
```

---

## EquityCurve (TradingView Lightweight Charts)

### `frontend/src/components/dashboard/EquityCurve.tsx`

```tsx
import { useEffect, useRef } from 'react'
import { Card, CardHeader, CardTitle } from '../ui/Card'
import { createChart, IChartApi, LineSeries, Time } from 'lightweight-charts'
import { useDashboardStore } from '../../store/dashboardStore'

export default function EquityCurve() {
  const { equityCurve } = useDashboardStore()
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#151920' },
        textColor: '#6B7280',
      },
      grid: {
        vertLines: { color: '#1C2028' },
        horzLines: { color: '#1C2028' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 200,
      timeScale: {
        borderColor: '#2A2F3A',
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: '#2A2F3A',
      },
    })

    const lineSeries = chart.addSeries(LineSeries, {
      color: '#3B82F6',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    chartRef.current = chart

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [])

  // Update data when it changes
  useEffect(() => {
    if (!chartRef.current || equityCurve.length === 0) return

    const lineSeries = chartRef.current.getSeries()[0]
    if (!lineSeries) return

    const data = equityCurve.map((point) => ({
      time: (new Date(point.date).getTime() / 1000) as Time,
      value: point.balance,
    }))

    lineSeries.setData(data)
    chartRef.current.timeScale().fitContent()
  }, [equityCurve])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Equity Curve (7D)</CardTitle>
      </CardHeader>
      <div ref={chartContainerRef} className="w-full" />
    </Card>
  )
}
```

---

## Overview Page

### `frontend/src/pages/Overview.tsx`

```tsx
import BotStatusCard from '../components/dashboard/BotStatusCard'
import PnLCard from '../components/dashboard/PnLCard'
import PositionsTable from '../components/dashboard/PositionsTable'
import EquityCurve from '../components/dashboard/EquityCurve'
import { useDashboardStore } from '../store/dashboardStore'
import { useEffect } from 'react'

export default function Overview() {
  const { fetchOverview, fetchToday, isLoading } = useDashboardStore()

  useEffect(() => {
    fetchOverview()
    fetchToday()
  }, [])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted mt-0.5">
          Real-time bot status and trading performance
        </p>
      </div>

      {/* Top row: Status + P&L */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <BotStatusCard />
        <PnLCard />
      </div>

      {/* Positions */}
      <PositionsTable />

      {/* Equity Curve */}
      <EquityCurve />
    </div>
  )
}
```

---

## Deliverables

1. **`frontend/src/components/dashboard/BotStatusCard.tsx`** — Start/stop toggle, mode selector, uptime, scan count
2. **`frontend/src/components/dashboard/PnLCard.tsx`** — Today's P&L in R and USDT, win/loss, daily limit progress bar
3. **`frontend/src/components/dashboard/PositionsTable.tsx`** — Live positions with side badge, P&L badge, empty state
4. **`frontend/src/components/dashboard/EquityCurve.tsx`** — TradingView Lightweight Charts equity line chart
5. **`frontend/src/pages/Overview.tsx`** — Assembles all four components
6. **`frontend/src/pages/Overview.tsx`** fetches data on mount

---

## Validation

```bash
cd /home/vick/fubot/frontend && npm run dev
# Visit http://localhost:5173 → Overview page
# - BotStatusCard shows current status (running/stopped)
# - PnLCard shows today's P&L and win/loss count
# - PositionsTable shows open positions or "No open positions" empty state
# - EquityCurve renders chart (or empty if no equity data)
```

---

## Next Step
Phase 5 Step 5: WebSocket Real-time Updates in React

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
