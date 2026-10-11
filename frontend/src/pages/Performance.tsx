import { useEffect, useRef, useState } from 'react'
import { createChart, LineSeries, HistogramSeries, type IChartApi, type Time } from 'lightweight-charts'
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

type Range = '7d' | '30d' | '90d' | '365d'
const RANGE_DAYS: Record<Range, number> = { '7d': 7, '30d': 30, '90d': 90, '365d': 365 }

function EquityChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const [range, setRange] = useState<Range>('30d')

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
    c.addSeries(LineSeries, { color: '#3B82F6', lineWidth: 2 })
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
        if (!chartRef.current) return
        const series = chartRef.current?.getSeries()?.[0] ?? null
        if (!series) return
        series.setData(res.data.data.map(p => ({ time: (new Date(p.date).getTime() / 1000) as Time, value: p.balance })))
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [range])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Equity Curve</CardTitle>
        <div className="flex gap-1">
          {(['7d', '30d', '90d', '365d'] as Range[]).map(r => (
            <button
              key={r}
              onClick={() => setRange(r)}
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

function MonthlyReturnsChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

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
    c.addSeries(HistogramSeries, { priceFormat: { type: 'price', precision: 2 } })
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
        if (!chartRef.current) return
        const series = chartRef.current?.getSeries()?.[0] ?? null
        if (!series) return
        series.setData(res.data.data.map(p => ({
          time: (new Date(p.month + '-01').getTime() / 1000) as Time,
          value: p.pnl_r,
          color: p.pnl_r >= 0 ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)',
        })))
        chartRef.current.timeScale().fitContent()
      })
      .catch(console.error)
  }, [])

  return <div ref={containerRef} className="w-full" />
}

function RDistributionChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
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
    c.addSeries(HistogramSeries, { priceFormat: { type: 'volume' } })
    chartRef.current = c
    const handleResize = () => {
      if (containerRef.current) c.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [])

  useEffect(() => {
    api.get<{ data: RDatum[] }>(ENDPOINTS.performanceStrategies)
      .then(res => {
        setData(res.data.data)
        if (!chartRef.current) return
        const series = chartRef.current?.getSeries()?.[0] ?? null
        if (!series) return
        series.setData(res.data.data.map((b, i) => ({
          time: (i + 1) as Time,
          value: b.count,
          color: b.bucket.startsWith('0') || b.bucket.startsWith('-') ? 'rgba(239, 68, 68, 0.7)' : 'rgba(34, 197, 94, 0.7)',
        })))
      })
      .catch(console.error)
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle>R:R Distribution</CardTitle>
      </CardHeader>
      <div ref={containerRef} className="w-full" />
      <div className="flex flex-wrap gap-3 px-4 pb-3">
        {data.map(bucket => (
          <div key={bucket.bucket} className="flex items-center gap-1.5 text-xs">
            <div className={`w-2 h-2 rounded-sm ${bucket.bucket.startsWith('0') || bucket.bucket.startsWith('-') ? 'bg-danger' : 'bg-success'}`} />
            <span className="text-muted">{bucket.bucket}: <span className="text-text font-mono">{bucket.count}</span></span>
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function Performance() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    api.get<Stats>(ENDPOINTS.tradeStats).then(res => setStats(res.data)).catch(console.error)
  }, [])

  const statCards = [
    { label: 'Total Trades', value: stats?.total_trades?.toString() ?? '—' },
    { label: 'Win Rate', value: stats ? `${stats.win_rate.toFixed(1)}%` : '—' },  // backend sends percent
    { label: 'Avg R/Trade', value: stats ? `${stats.avg_r >= 0 ? '+' : ''}${stats.avg_r.toFixed(2)}R` : '—' },
    { label: 'Best Trade', value: stats ? `+${stats.best_trade.toFixed(2)}R` : '—', className: 'text-success' },
    { label: 'Worst Trade', value: stats ? `${stats.worst_trade.toFixed(2)}R` : '—', className: 'text-danger' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-primary" />
          Performance
        </h1>
        <p className="text-sm text-muted mt-0.5">Equity curve, monthly returns, and trade statistics</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {statCards.map(({ label, value, className }) => (
          <Card key={label} className="p-3">
            <p className="text-xs text-muted uppercase tracking-wide">{label}</p>
            <p className={clsx('text-xl font-mono font-bold mt-0.5', className)}>{value}</p>
          </Card>
        ))}
      </div>

      <EquityChart />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Monthly Returns (R)</CardTitle></CardHeader>
          <MonthlyReturnsChart />
        </Card>
        <RDistributionChart />
      </div>
    </div>
  )
}
