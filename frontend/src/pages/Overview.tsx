import { useEffect, useState } from 'react'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Table, TableHead, TableRow, TableHeadCell, TableCell } from '../components/ui/Table'
import { useDashboardStore } from '../store/dashboardStore'
import type { Position } from '../store/dashboardStore'
import { clsx } from 'clsx'
import { Play, Square, Loader2, ExternalLink, XCircle } from 'lucide-react'
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'

function formatPrice(p: number) {
  return p >= 1000 ? p.toFixed(2) : p >= 1 ? p.toFixed(4) : p.toFixed(6)
}

export default function Overview() {
  const { botStatus, positions, equityCurve, todayStats, isLoading, fetchOverview, fetchToday, startBot, stopBot } = useDashboardStore()
  const [chart, setChart] = useState<IChartApi | null>(null)
  const [chartContainer, setChartContainer] = useState<HTMLDivElement | null>(null)
  // createChart()'s object has no getSeries() at runtime — hold the series
  // returned by addSeries() instead of calling chart.getSeries().
  const [series, setSeries] = useState<ISeriesApi<'Line'> | null>(null)
  const [closingPositionId, setClosingPositionId] = useState<number | null>(null)

  useEffect(() => {
    fetchOverview()
    fetchToday()
  }, [])

  const handleClosePosition = async (positionId: number) => {
    if (!window.confirm('Close this position at market price?')) return
    setClosingPositionId(positionId)
    try {
      await api.post(ENDPOINTS.botClose(positionId))
      await fetchOverview()
      await fetchToday()
    } catch (err) {
      console.error('Failed to close position:', err)
      window.alert('Failed to close position. Check console for details.')
    } finally {
      setClosingPositionId(null)
    }
  }

  useEffect(() => {
    if (!chartContainer) return
    const c = createChart(chartContainer, {
      layout: { background: { color: '#151920' }, textColor: '#6B7280' },
      grid: { vertLines: { color: '#1C2028' }, horzLines: { color: '#1C2028' } },
      width: chartContainer.clientWidth,
      height: 200,
      timeScale: { borderColor: '#2A2F3A', timeVisible: true },
      rightPriceScale: { borderColor: '#2A2F3A' },
    })
    const s = c.addSeries(LineSeries, { color: '#3B82F6', lineWidth: 2 })
    setChart(c)
    setSeries(s)
    const handleResize = () => {
      if (chartContainer) c.applyOptions({ width: chartContainer.clientWidth })
    }
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); c.remove() }
  }, [chartContainer])

  useEffect(() => {
    if (!chart || !series || equityCurve.length === 0) return
    // The API returns equity_curve DESCENDING (newest first); lightweight-charts
    // requires ASCENDING time order for setData — feeding it descending throws
    // "Value is null" (out-of-order time) and renders an empty chart.
    const ascending = [...equityCurve].sort(
      (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
    )
    series.setData(ascending.map(p => ({
      time: (new Date(p.date).getTime() / 1000) as Time,
      value: p.balance,
    })))
    chart.timeScale().fitContent()
  }, [equityCurve, chart, series])

  const isRunning = botStatus?.status === 'running'
  const mode = botStatus?.mode || 'paper'
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [isClosingAll, setIsClosingAll] = useState(false)
  const [closedCount, setClosedCount] = useState<number | null>(null)

  const handleToggle = async () => {
    setIsTransitioning(true)
    try {
      if (isRunning) await stopBot()
      else await startBot(mode as 'paper' | 'live')
    } finally {
      setIsTransitioning(false)
    }
  }

  const handleCloseAll = async () => {
    if (!window.confirm('Close ALL open positions at market price? This cannot be undone.')) {
      return
    }
    setIsClosingAll(true)
    try {
      const res = await api.post<{ closed: number }>(ENDPOINTS.botCloseAll)
      setClosedCount(res.data.closed)
      setTimeout(() => setClosedCount(null), 5000)
      await fetchOverview()  // refresh positions table
    } catch (err) {
      console.error('Failed to close all positions:', err)
    } finally {
      setIsClosingAll(false)
    }
  }

  const uptime = botStatus?.uptime_seconds ?? 0
  const hours = Math.floor(uptime / 3600)
  const minutes = Math.floor((uptime % 3600) / 60)

  const pnlR = todayStats?.pnl_r ?? 0
  const pnlUsdt = todayStats?.pnl_usdt ?? 0
  const wins = todayStats?.wins ?? 0
  const losses = todayStats?.losses ?? 0
  const trades = todayStats?.trades_count ?? 0
  const limit = (todayStats?.daily_limit_status?.limit_r ?? 3)
  const used = todayStats?.daily_limit_status?.used_r ?? 0
  const remaining = limit - used

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted mt-0.5">Real-time bot status and trading performance</p>
      </div>

      {/* Top row: Status + P&L */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* BotStatusCard */}
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
                      mode === 'paper' ? 'border-warning bg-warning/10 text-warning' : 'border-border text-muted hover:text-text',
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
                      mode === 'live' ? 'border-danger bg-danger/10 text-danger' : 'border-border text-muted hover:text-text',
                      isRunning && 'opacity-50 cursor-not-allowed'
                    )}
                  >
                    LIVE
                  </button>
                </div>
              </div>
              <div className="flex items-center justify-end gap-2 mt-1">
                <Button
                  variant={isRunning ? 'danger' : 'primary'}
                  size="md"
                  onClick={handleToggle}
                  isLoading={isTransitioning}
                  disabled={isTransitioning}
                >
                  {isTransitioning ? <Loader2 className="w-4 h-4 animate-spin" /> : isRunning ? <><Square className="w-4 h-4" /> Stop</> : <><Play className="w-4 h-4" /> Start</>}
                </Button>
                {positions.length > 0 && (
                  <Button
                    variant="danger"
                    size="md"
                    onClick={handleCloseAll}
                    isLoading={isClosingAll}
                    disabled={isClosingAll}
                  >
                    {isClosingAll ? <Loader2 className="w-4 h-4 animate-spin" /> : <><XCircle className="w-4 h-4" /> Close All ({positions.length})</>}
                  </Button>
                )}
              </div>
              {closedCount !== null && (
                <p className="text-xs text-success mt-1 text-right">Closed {closedCount} position(s)</p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border/50">
              <div>
                <p className="text-xs text-muted uppercase tracking-wide">Uptime</p>
                <p className="text-lg font-mono font-bold mt-0.5">{isRunning ? `${hours}h ${minutes}m` : '—'}</p>
              </div>
              <div>
                <p className="text-xs text-muted uppercase tracking-wide">Scans</p>
                <p className="text-lg font-mono font-bold mt-0.5">{botStatus?.scan_count?.toLocaleString() ?? '—'}</p>
              </div>
            </div>
            {botStatus?.last_scan && (
              <p className="text-xs text-muted font-mono">Last scan: {new Date(botStatus.last_scan).toLocaleTimeString()}</p>
            )}
          </div>
        </Card>

        {/* PnLCard */}
        <Card>
          <CardHeader>
            <CardTitle>Today&apos;s Performance</CardTitle>
          </CardHeader>
          <div className="flex items-end gap-4 mb-4">
            <div>
              <p className="text-xs text-muted uppercase tracking-wide">P&L</p>
              <p className={clsx('text-3xl font-mono font-bold mt-0.5', pnlR >= 0 ? 'text-success' : 'text-danger')}>
                {pnlR >= 0 ? '+' : ''}{pnlR.toFixed(2)}R
              </p>
              <p className={clsx('text-sm font-mono mt-0.5', pnlUsdt >= 0 ? 'text-success/70' : 'text-danger/70')}>
                {pnlUsdt >= 0 ? '+' : ''}{pnlUsdt.toFixed(2)} USDT
              </p>
            </div>
            <div className="flex-1" />
            <div className="text-right">
              {pnlR >= 0 ? <span className="text-success text-xl">↑</span> : <span className="text-danger text-xl">↓</span>}
            </div>
          </div>
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
          <div>
            <div className="flex justify-between text-xs mb-1.5">
              <span className="text-muted uppercase tracking-wide">Daily Limit</span>
              <span className={clsx('font-mono', remaining <= 0 ? 'text-danger' : 'text-muted')}>
                {used.toFixed(2)}R / {limit}R
                {remaining > 0 && <span className="text-success ml-1">({remaining.toFixed(2)}R left)</span>}
              </span>
            </div>
            <div className="h-2 bg-surface2 rounded-full overflow-hidden">
              <div
                className={clsx('h-full rounded-full transition-all duration-500',
                  (used / limit) < 0.66 ? 'bg-success' : (used / limit) < 0.9 ? 'bg-warning' : 'bg-danger')}
                style={{ width: `${Math.min(100, (used / limit) * 100)}%` }}
              />
            </div>
          </div>
        </Card>
      </div>

      {/* PositionsTable */}
      <Card>
        <CardHeader>
          <CardTitle>Open Positions</CardTitle>
          <Badge variant="info">{positions.length}</Badge>
        </CardHeader>
        {positions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted">
            <ExternalLink className="w-8 h-8 mb-3 opacity-30" />
            <p className="text-sm">No open positions</p>
            <p className="text-xs mt-1">Bot will open positions when signals fire</p>
          </div>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeadCell>Symbol</TableHeadCell>
                <TableHeadCell>Side</TableHeadCell>
                <TableHeadCell className="text-right">Size</TableHeadCell>
                <TableHeadCell className="text-right">Value (USD)</TableHeadCell>
                <TableHeadCell className="text-right">Entry</TableHeadCell>
                <TableHeadCell className="text-right">Mark</TableHeadCell>
                <TableHeadCell className="text-right">PnL (USD)</TableHeadCell>
                <TableHeadCell className="text-right">ROI</TableHeadCell>
                <TableHeadCell className="text-right">Lev</TableHeadCell>
                <TableHeadCell className="text-right">Action</TableHeadCell>
              </TableRow>
            </TableHead>
            <tbody>
              {positions.map((pos: Position) => (
                <TableRow key={pos.id}>
                  <TableCell className="font-mono font-bold">{pos.symbol}</TableCell>
                  <TableCell>
                    <Badge variant={pos.side === 'LONG' ? 'success' : 'danger'}>{pos.side}</Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">{pos.quantity.toFixed(4)}</TableCell>
                  {/* position notional in USD = qty × entry (what you control) */}
                  <TableCell className="text-right font-mono text-sm">
                    ${(pos.quantity * pos.entry_price).toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatPrice(pos.entry_price)}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatPrice(pos.mark_price)}</TableCell>
                  {/* unrealized_pnl is USD on the backend ((mark-entry)×qty) */}
                  <TableCell className="text-right">
                    <span className={clsx('font-mono font-bold text-sm', pos.unrealized_pnl >= 0 ? 'text-success' : 'text-danger')}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}{pos.unrealized_pnl.toFixed(2)}
                    </span>
                  </TableCell>
                  {/* ROI % vs initial margin — same definition as Binance's position ROI */}
                  <TableCell className="text-right">
                    <span className={clsx('font-mono text-sm', pos.unrealized_pnl_pct >= 0 ? 'text-success' : 'text-danger')}>
                      {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(2)}%
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">{pos.leverage}x</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => handleClosePosition(pos.id)}
                      disabled={closingPositionId === pos.id}
                    >
                      {closingPositionId === pos.id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        'Close'
                      )}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* EquityCurve */}
      <Card className="relative">
        <CardHeader>
          <CardTitle>Equity Curve (7D)</CardTitle>
        </CardHeader>
        <div ref={setChartContainer} className="w-full" />
        {equityCurve.length === 0 && (
          // pointer-events-none: this is a passive placeholder. Without it the
          // absolute overlay covered the whole content area (Card isn't
          // positioned, so inset-0 escaped to the nearest ancestor) and
          // intercepted every click on the page — nav links and the start
          // button included.
          <div className="absolute inset-0 flex items-center justify-center text-muted text-sm pointer-events-none">
            No equity data yet. The bot needs to run before data appears.
          </div>
        )}
      </Card>
    </div>
  )
}
