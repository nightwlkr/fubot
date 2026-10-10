# Phase 5 — Step 8: Performance Page — Equity Curve, Monthly Returns, R Distribution
## FUBOT: TradingView Charts + Performance Analytics

---

## Objective
Build the Performance page with: 30-day equity curve chart, monthly returns bar chart, and R:R distribution histogram — all using TradingView Lightweight Charts.

---

## Context
- **Depends on**: Phase 5 Steps 1–7
- **APIs**: `GET /api/performance/equity`, `GET /api/performance/monthly`, `GET /api/trades/stats`
- **Charting**: TradingView Lightweight Charts (`lightweight-charts`)
- **Time range**: Equity curve supports 7d / 30d / 90d / 365d selector

---

## Equity Chart with Time Range Selector

### `frontend/src/components/charts/EquityChart.tsx`

```tsx
import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, LineSeries, HistogramSeries, Time } from 'lightweight-charts'
import { api } from '../../api/client'
import { ENDPOINTS } from '../../api/endpoints'
import { Card, CardHeader, CardTitle } from '../ui/Card'

type Range = '7d' | '30d' | '90d' | '365d'

const RANGE_DAYS: Record<Range, number> = { '7d': 7, '30d': 30, '90d': 90, '365d': 365 }

export default function EquityChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [range, setRange] = useState<Range>('30d')
  const [data, setData] = useState<{ date: string; balance: number }[]>([])

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
      height: 280,
      timeScale: {
        borderColor: '#2A2F3A',
        timeVisible: true,
      },
      rightPriceScale: { borderColor: '#2A2F3A' },
      crosshair: {
        vertLine: { color: '#3B82F6', labelBackgroundColor: '#3B82F6' },
        horzLine: { color: '#3B82F6', labelBackgroundColor: '#3B82F6' },
      },
    })

    const lineSeries = chart.addSeries(LineSeries, {
      color: '#3B82F6',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      lastValueVisible: true,
      title: 'Balance (USDT)',
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

  // Fetch data when range changes
  useEffect(() => {
    const days = RANGE_DAYS[range]
    api
      .get<{ data: { date: string; balance: number }[] }>(ENDPOINTS.performanceEquity, {
        params: { days },
      })
      .then((res) => {
        setData(res.data.data)
        if (!chartRef.current) return
        const lineSeries = chartRef.current.getSeries()[0]
        if (!lineSeries) return
        const chartData = res.data.data.map((point) => ({
          time: (new Date(point.date).getTime() / 1000) as Time,
          value: point.balance,
        }))
        lineSeries.setData(chartData)
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [range])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Equity Curve</CardTitle>
        <div className="flex gap-1">
          {(['7d', '30d', '90d', '365d'] as Range[]).map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
                range === r
                  ? 'bg-primary text-white'
                  : 'bg-surface2 text-muted hover:text-text'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </CardHeader>
      <div ref={chartContainerRef} className="w-full" />
    </Card>
  )
}
```

---

## Monthly Returns Chart

### `frontend/src/components/charts/MonthlyReturnsChart.tsx`

```tsx
import { useEffect, useRef } from 'react'
import { createChart, IChartApi, HistogramSeries, Time } from 'lightweight-charts'
import { api } from '../../api/client'
import { ENDPOINTS } from '../../api/endpoints'
import { Card, CardHeader, CardTitle } from '../ui/Card'
import { useState } from 'react'

interface MonthlyData {
  month: string   // "2026-01"
  pnl_r: number
  trades: number
  win_rate: number
}

export default function MonthlyReturnsChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [data, setData] = useState<MonthlyData[]>([])

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
      timeScale: { borderColor: '#2A2F3A' },
      rightPriceScale: { borderColor: '#2A2F3A' },
    })

    const histSeries = chart.addSeries(HistogramSeries, {
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

  useEffect(() => {
    api
      .get<{ data: MonthlyData[] }>(ENDPOINTS.performanceMonthly)
      .then((res) => {
        setData(res.data.data)
        if (!chartRef.current) return
        const histSeries = chartRef.current.getSeries()[0]
        if (!histSeries) return

        const chartData = res.data.data.map((point) => ({
          time: (new Date(point.month + '-01').getTime() / 1000) as Time,
          value: point.pnl_r,
          color:
            point.pnl_r >= 0
              ? 'rgba(34, 197, 94, 0.7)'
              : 'rgba(239, 68, 68, 0.7)',
        }))

        histSeries.setData(chartData)
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [])

  const totalR = data.reduce((sum, d) => sum + d.pnl_r, 0)
  const winMonths = data.filter((d) => d.pnl_r > 0).length

  return (
    <Card>
      <CardHeader>
        <CardTitle>Monthly Returns (R)</CardTitle>
        <div className="flex gap-4 text-xs">
          <span className="text-muted">
            Total:{' '}
            <span className={totalR >= 0 ? 'text-success' : 'text-danger'}>
              {totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R
            </span>
          </span>
          <span className="text-muted">
            Win months: <span className="text-success">{winMonths}/{data.length}</span>
          </span>
        </div>
      </CardHeader>
      <div ref={chartContainerRef} className="w-full" />
    </Card>
  )
}
```

---

## R Distribution Chart

### `frontend/src/components/charts/RDistributionChart.tsx`

```tsx
import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, HistogramSeries, Time } from 'lightweight-charts'
import { api } from '../../api/client'
import { ENDPOINTS } from '../../api/endpoints'
import { Card, CardHeader, CardTitle } from '../ui/Card'

interface RRDatum {
  bucket: string  // e.g. "0 to 0.5R", "0.5 to 1R", "1 to 2R", "2 to 3R", "3R+"
  count: number
}

export default function RDistributionChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [data, setData] = useState<RRDatum[]>([])

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
      height: 180,
      timeScale: { visible: false },
      rightPriceScale: { visible: false },
    })

    const barSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
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

  useEffect(() => {
    api
      .get<{ data: RRDatum[] }>(ENDPOINTS.performanceStrategies)
      .then((res) => {
        setData(res.data.data)
        if (!chartRef.current) return
        const barSeries = chartRef.current.getSeries()[0]
        if (!barSeries) return

        const chartData = res.data.data.map((bucket, i) => ({
          time: (i + 1) as Time, // Sequential index as time
          value: bucket.count,
          color:
            bucket.bucket.startsWith('0') || bucket.bucket.startsWith('-')
              ? 'rgba(239, 68, 68, 0.7)'
              : 'rgba(34, 197, 94, 0.7)',
        }))

        barSeries.setData(chartData)
      })
      .catch(console.error)
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle>R:R Distribution</CardTitle>
      </CardHeader>
      <div ref={chartContainerRef} className="w-full" />
      {/* Legend */}
      <div className="flex flex-wrap gap-3 px-4 pb-3">
        {data.map((bucket) => (
          <div key={bucket.bucket} className="flex items-center gap-1.5 text-xs">
            <div
              className={`w-2 h-2 rounded-sm ${
                bucket.bucket.startsWith('0') || bucket.bucket.startsWith('-')
                  ? 'bg-danger'
                  : 'bg-success'
              }`}
            />
            <span className="text-muted">
              {bucket.bucket}: <span className="text-text font-mono">{bucket.count}</span>
            </span>
          </div>
        ))}
      </div>
    </Card>
  )
}
```

---

## Performance Page

### `frontend/src/pages/Performance.tsx`

```tsx
import EquityChart from '../components/charts/EquityChart'
import MonthlyReturnsChart from '../components/charts/MonthlyReturnsChart'
import RDistributionChart from '../components/charts/RDistributionChart'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { clsx } from 'clsx'
import { BarChart3 } from 'lucide-react'

interface Stats {
  total_trades: number
  win_rate: number
  avg_r: number
  best_trade: number
  worst_trade: number
}

export default function Performance() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    api
      .get<Stats>(ENDPOINTS.tradeStats)
      .then((res) => setStats(res.data))
      .catch(console.error)
  }, [])

  const statCards = [
    {
      label: 'Total Trades',
      value: stats?.total_trades?.toString() ?? '—',
    },
    {
      label: 'Win Rate',
      value: stats ? `${(stats.win_rate * 100).toFixed(1)}%` : '—',
    },
    {
      label: 'Avg R/Trade',
      value: stats ? `${stats.avg_r >= 0 ? '+' : ''}${stats.avg_r.toFixed(2)}R` : '—',
    },
    {
      label: 'Best Trade',
      value: stats ? `+${stats.best_trade.toFixed(2)}R` : '—',
      className: 'text-success',
    },
    {
      label: 'Worst Trade',
      value: stats ? `${stats.worst_trade.toFixed(2)}R` : '—',
      className: 'text-danger',
    },
  ]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-primary" />
          Performance
        </h1>
        <p className="text-sm text-muted mt-0.5">
          Equity curve, monthly returns, and trade statistics
        </p>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {statCards.map(({ label, value, className }) => (
          <Card key={label} className="p-3">
            <p className="text-xs text-muted uppercase tracking-wide">{label}</p>
            <p className={clsx('text-xl font-mono font-bold mt-0.5', className)}>{value}</p>
          </Card>
        ))}
      </div>

      {/* Charts */}
      <EquityChart />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <MonthlyReturnsChart />
        <RDistributionChart />
      </div>
    </div>
  )
}
```

---

## Deliverables

1. **`frontend/src/components/charts/EquityChart.tsx`** — TradingView line chart with 7d/30d/90d/365d selector
2. **`frontend/src/components/charts/MonthlyReturnsChart.tsx`** — TradingView histogram, green for positive months, red for negative
3. **`frontend/src/components/charts/RDistributionChart.tsx`** — TradingView bar chart for R:R bucket distribution
4. **`frontend/src/pages/Performance.tsx`** — Assembles all charts + stats summary row
5. **Charts resize** on window resize (responsive)
6. **Charts update** when data changes via API

---

## Validation

```bash
cd /home/vick/fubot/frontend && npm run dev
# Visit http://localhost:5173/performance
# - Equity chart renders with time range buttons
# - Monthly returns chart shows bars
# - R distribution chart shows buckets
# - Stats row shows correct values (or dashes if no data)
# - Charts resize when window is resized
```

---

## Next Step
Phase 5 Step 9: Settings Page — API Key Entry, Trading Parameters, Symbol Config

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
