import { useEffect, useRef, useState } from 'react'
import { createChart, LineSeries, HistogramSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { BarChart3 } from 'lucide-react'
import { clsx } from 'clsx'

interface Stats {
  total_trades: number
  win_rate: number
  avg_r: number
  best_trade: number
  worst_trade: number
}

interface EquityData {
  date: string
  balance: number
}

interface PnLData {
  date: string
  pnl_r: number
  pnl_usdt: number
}

interface MonthlyData {
  month: string
  pnl_r: number
  trades: number
  win_rate: number
}

interface RDatum {
  bucket: string
  count: number
}

// Only NEGATIVE buckets are losses. The backend labels the winning buckets
// "0 to 0.5R" and "0.5 to 1R", so the old startsWith('0') test painted both of
// them red in the histogram and the legend.
function isLossBucket(bucket: string): boolean {
  return bucket.trim().startsWith('-')
}

type Range = '7d' | '30d' | '90d' | '365d'
const RANGE_DAYS: Record<Range, number> = { '7d': 7, '30d': 30, '90d': 90, '365d': 365 }

function EquityChart({ range, onRangeChange }: { range: Range; onRangeChange: (r: Range) => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  // Hold the series returned by addSeries() — createChart's chart object has
  // NO getSeries() method at runtime (that's on a pane API), so calling
  // chart.getSeries() threw "not a function" and crashed the page.
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const c = createChart(containerRef.current, {
      layout: { background: { color: '#151920' }, textColor: '#6B7280' },
      grid: { vertLines: { color: '#1C2028' }, horzLines: { color: '#1C2028' } },
      width: containerRef.current.clientWidth,
      height: 280,
      timeScale: { borderColor: '#2A2F3A', timeVisible: true },
      rightPriceScale: { borderColor: '#2A2F3A' },
      crosshair: { vertLine: { color: '#3B82F6', labelBackgroundColor: '#3B82F6' }, horzLine: { color: '#3B82F6', labelBackgroundColor: '#3B82F6' } },
    })
    seriesRef.current = c.addSeries(LineSeries, { color: '#3B82F6', lineWidth: 2 })
    chartRef.current = c
    const handleResize = () => {
      if (containerRef.current) c.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [])

  useEffect(() => {
    api.get<{ data: EquityData[] }>(ENDPOINTS.performanceEquity, { params: { days: RANGE_DAYS[range] } })
      .then(res => {
        const series = seriesRef.current
        if (!series || !chartRef.current) return
        series.setData(res.data.data.map(p => ({ time: (new Date(p.date).getTime() / 1000) as Time, value: p.balance })))
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [range])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Equity Curve ({range.toUpperCase()})</CardTitle>
        <div className="flex gap-1">
          {(['7d', '30d', '90d', '365d'] as Range[]).map(r => (
            <button
              key={r}
              onClick={() => onRangeChange(r)}
              className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
                range === r ? 'bg-primary text-white' : 'bg-surface2 text-muted hover:text-text'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </CardHeader>
      <div ref={containerRef} className="w-full" />
    </Card>
  )
}

function PnLChart({ range, onRangeChange }: { range: Range; onRangeChange: (r: Range) => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const [metric, setMetric] = useState<'r' | 'usd'>('usd')

  useEffect(() => {
    if (!containerRef.current) return
    const c = createChart(containerRef.current, {
      layout: { background: { color: '#151920' }, textColor: '#6B7280' },
      grid: { vertLines: { color: '#1C2028' }, horzLines: { color: '#1C2028' } },
      width: containerRef.current.clientWidth,
      height: 240,
      timeScale: { borderColor: '#2A2F3A', timeVisible: true },
      rightPriceScale: { borderColor: '#2A2F3A' },
      crosshair: { vertLine: { color: '#22C55E', labelBackgroundColor: '#22C55E' }, horzLine: { color: '#22C55E', labelBackgroundColor: '#22C55E' } },
    })
    // green line — trading P&L (vs blue account-equity above)
    seriesRef.current = c.addSeries(LineSeries, { color: '#22C55E', lineWidth: 2 })
    chartRef.current = c
    const handleResize = () => {
      if (containerRef.current) c.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [])

  useEffect(() => {
    api.get<{ data: PnLData[] }>(ENDPOINTS.performancePnL, { params: { days: RANGE_DAYS[range] } })
      .then(res => {
        const series = seriesRef.current
        if (!series || !chartRef.current) return
        // endpoint already returns ascending; guard anyway (setData needs order)
        const data = [...res.data.data].sort(
          (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
        )
        series.setData(data.map(p => ({
          time: (new Date(p.date).getTime() / 1000) as Time,
          value: metric === 'r' ? p.pnl_r : p.pnl_usdt,
        })))
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [range, metric])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trading P&amp;L ({metric === 'r' ? 'R' : 'USD'}, {range.toUpperCase()})</CardTitle>
        <div className="flex gap-1">
          <button
            onClick={() => setMetric('usd')}
            className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
              metric === 'usd' ? 'bg-success text-white' : 'bg-surface2 text-muted hover:text-text'
            }`}
          >
            USD
          </button>
          <button
            onClick={() => setMetric('r')}
            className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
              metric === 'r' ? 'bg-success text-white' : 'bg-surface2 text-muted hover:text-text'
            }`}
          >
            R
          </button>
          <span className="w-2" />
          {(['7d', '30d', '90d', '365d'] as Range[]).map(r => (
            <button
              key={r}
              onClick={() => onRangeChange(r)}
              className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
                range === r ? 'bg-primary text-white' : 'bg-surface2 text-muted hover:text-text'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </CardHeader>
      <p className="text-xs text-muted px-1 mb-1">
        Cumulative realized P&amp;L from closed <span className="text-text">live</span> trades only — unlike the equity curve, this excludes collateral price drift.
      </p>
      <div ref={containerRef} className="w-full" />
    </Card>
  )
}

function MonthlyReturnsChart({ range }: { range: Range }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const c = createChart(containerRef.current, {
      layout: { background: { color: '#151920' }, textColor: '#6B7280' },
      grid: { vertLines: { color: '#1C2028' }, horzLines: { color: '#1C2028' } },
      width: containerRef.current.clientWidth,
      height: 200,
      timeScale: { borderColor: '#2A2F3A' },
      rightPriceScale: { borderColor: '#2A2F3A' },
    })
    seriesRef.current = c.addSeries(HistogramSeries, { priceFormat: { type: 'price', precision: 2 } })
    chartRef.current = c
    const handleResize = () => {
      if (containerRef.current) c.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [])

  useEffect(() => {
    api.get<{ data: MonthlyData[] }>(ENDPOINTS.performanceMonthly)
      .then(res => {
        const series = seriesRef.current
        if (!series || !chartRef.current) return
        const rows = Array.isArray(res.data.data) ? res.data.data : []
        // Months are a coarse axis — never cut them at the range boundary, or
        // the current (partial) month vanishes. Keep every month that OVERLAPS
        // the window so the bar is present, just with the range noted below.
        series.setData(rows.map(p => ({
          time: (new Date(p.month + '-01').getTime() / 1000) as Time,
          value: p.pnl_r,
          color: p.pnl_r >= 0 ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)',
        })))
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
    // `range` is accepted but deliberately not a filter: months are too coarse
    // to slice (the current month is always partial), and dropping it would
    // hide the month in progress. Changing range re-renders this chart only if
    // the dependency is present — kept for correctness if a filter is added.
  }, [range])

  return <div ref={containerRef} className="w-full" />
}

function RDistributionChart({ range }: { range: Range }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const [data, setData] = useState<RDatum[]>([])

  useEffect(() => {
    if (!containerRef.current) return
    const c = createChart(containerRef.current, {
      layout: { background: { color: '#151920' }, textColor: '#6B7280' },
      grid: { vertLines: { color: '#1C2028' }, horzLines: { color: '#1C2028' } },
      width: containerRef.current.clientWidth,
      height: 180,
      timeScale: { visible: false },
      rightPriceScale: { visible: false },
    })
    seriesRef.current = c.addSeries(HistogramSeries, { priceFormat: { type: 'volume' } })
    chartRef.current = c
    const handleResize = () => {
      if (containerRef.current) c.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [])

  useEffect(() => {
    // The endpoint had no `days` param, so this chart ignored the range buttons
    // and always showed all-time while the charts beside it respected 7d/30d/…
    api.get<{ data: RDatum[] }>(ENDPOINTS.performanceStrategies, {
      params: { days: RANGE_DAYS[range] },
    })
      .then(res => {
        const rows = Array.isArray(res.data.data) ? res.data.data : []
        setData(rows)
        const series = seriesRef.current
        if (!series) return
        series.setData(rows.map((b, i) => ({
          time: (i + 1) as Time,
          value: b.count,
          color: isLossBucket(b.bucket) ? 'rgba(239, 68, 68, 0.7)' : 'rgba(34, 197, 94, 0.7)',
        })))
      })
      .catch(console.error)
  }, [range])

  return (
    <Card>
      <CardHeader>
        <CardTitle>R:R Distribution ({range.toUpperCase()})</CardTitle>
      </CardHeader>
      <div ref={containerRef} className="w-full" />
      <div className="flex flex-wrap gap-3 px-4 pb-3">
        {data.map(bucket => (
          <div key={bucket.bucket} className="flex items-center gap-1.5 text-xs">
            <div className={`w-2 h-2 rounded-sm ${isLossBucket(bucket.bucket) ? 'bg-danger' : 'bg-success'}`} />
            <span className="text-muted">{bucket.bucket}: <span className="text-text font-mono">{bucket.count}</span></span>
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function Performance() {
  const [stats, setStats] = useState<Stats | null>(null)
  // One range for the whole page. Previously each chart owned its own state, so
  // the R-distribution had no range at all and silently showed all-time next to
  // charts that respected the buttons.
  const [range, setRange] = useState<Range>('30d')

  useEffect(() => {
    // Stats are live-trading numbers — poll so they don't go stale while the
    // operator watches the charts update. They also honour the range: without
    // `days` the cards were all-time while every chart beside them showed the
    // selected window, so the headline numbers described a different sample.
    let cancelled = false
    const fetchStats = () => {
      api.get<Stats>(ENDPOINTS.tradeStats, { params: { days: RANGE_DAYS[range] } })
        .then(res => { if (!cancelled) setStats(res.data) })
        .catch(console.error)
    }
    fetchStats()
    const id = setInterval(fetchStats, 15000)
    return () => { cancelled = true; clearInterval(id) }
  }, [range])

  const statCards = [
    { label: 'Total Trades', value: stats?.total_trades?.toString() ?? '—' },
    { label: 'Win Rate', value: stats ? `${stats.win_rate.toFixed(1)}%` : '—' },  // backend sends percent
    { label: 'Avg R/Trade', value: stats ? `${stats.avg_r >= 0 ? '+' : ''}${stats.avg_r.toFixed(2)}R` : '—' },
    { label: 'Best Trade', value: stats ? `${stats.best_trade >= 0 ? '+' : ''}${stats.best_trade.toFixed(2)}R` : '—', className: stats && stats.best_trade < 0 ? 'text-danger' : 'text-success' },
    { label: 'Worst Trade', value: stats ? `${stats.worst_trade.toFixed(2)}R` : '—', className: 'text-danger' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-primary" />
          Performance
        </h1>
        <p className="text-sm text-muted mt-0.5">
          Equity curve, monthly returns, and trade statistics
          <span className="text-muted"> — live trades only</span>
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {statCards.map(({ label, value, className }) => (
          <Card key={label} className="p-3">
            <p className="text-xs text-muted uppercase tracking-wide">{label}</p>
            <p className={clsx('text-xl font-mono font-bold mt-0.5', className)}>{value}</p>
          </Card>
        ))}
      </div>

      <EquityChart range={range} onRangeChange={setRange} />
      <PnLChart range={range} onRangeChange={setRange} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Monthly Returns (R)</CardTitle>
          </CardHeader>
          <p className="text-xs text-muted px-4 -mt-1 mb-2">
            All-time by month — months are too coarse to slice, and the current
            month is always partial. Use the range buttons for the selected window.
          </p>
          <MonthlyReturnsChart range={range} />
        </Card>
        <RDistributionChart range={range} />
      </div>
    </div>
  )
}
