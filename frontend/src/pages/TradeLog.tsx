import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Table, TableHead, TableRow, TableHeadCell, TableCell } from '../components/ui/Table'
import { Download, Filter, ChevronLeft, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'

export interface Trade {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  entry_price: number
  exit_price: number
  quantity: number
  pnl_r: number
  pnl_usdt: number
  exit_reason: string
  strategy_type: string
  closed_at: string
}

interface TradeResponse {
  trades: Trade[]
  total: number
  page: number
  pages: number
}

interface TradeStats {
  total_trades: number
  win_rate: number
  avg_r: number
  best_trade: number
  worst_trade: number
}

const PAGE_SIZE = 50

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

function formatPrice(p: number) {
  return p >= 1000 ? p.toFixed(2) : p >= 1 ? p.toFixed(4) : p.toFixed(6)
}

export default function TradeLog() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [stats, setStats] = useState<TradeStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState('')
  const [outcome, setOutcome] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)

  const fetchTrades = useCallback(async () => {
    setIsLoading(true)
    try {
      const params: Record<string, string | number> = { page, limit: PAGE_SIZE }
      if (symbol) params.symbol = symbol
      if (side) params.side = side
      if (outcome) params.outcome = outcome
      if (fromDate) params.from_date = fromDate
      if (toDate) params.to_date = toDate

      const [tradesRes, statsRes] = await Promise.all([
        api.get<TradeResponse>(ENDPOINTS.trades, { params }),
        api.get<TradeStats>(ENDPOINTS.tradeStats),
      ])

      setTrades(tradesRes.data.trades)
      setTotal(tradesRes.data.total)
      setTotalPages(tradesRes.data.pages)
      setStats(statsRes.data)
    } catch (err) {
      console.error('Failed to fetch trades:', err)
    } finally {
      setIsLoading(false)
    }
  }, [page, symbol, side, outcome, fromDate, toDate])

  useEffect(() => {
    fetchTrades()
  }, [fetchTrades])

  const exportCSV = () => {
    const headers = ['ID', 'Symbol', 'Side', 'Entry Price', 'Exit Price', 'Quantity', 'Value (USD)', 'PnL (R)', 'PnL (USDT)', 'Exit Reason', 'Strategy', 'Closed At']
    const rows = trades.map(t => [t.id, t.symbol, t.side, t.entry_price, t.exit_price, t.quantity,
                                  (t.quantity * t.entry_price).toFixed(2), t.pnl_r, t.pnl_usdt,
                                  t.exit_reason, t.strategy_type, t.closed_at])

    const csv = [headers, ...rows]
      .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
      .join('\n')

    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `fubot-trades-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const clearFilters = () => {
    setSymbol('')
    setSide('')
    setOutcome('')
    setFromDate('')
    setToDate('')
    setPage(1)
  }

  const hasFilters = symbol || side || outcome || fromDate || toDate

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Trade Log</h1>
          <p className="text-sm text-muted mt-0.5">{total} trades recorded</p>
        </div>
        <Button variant="secondary" size="sm" onClick={exportCSV} disabled={trades.length === 0}>
          <Download className="w-4 h-4" />
          Export CSV
        </Button>
      </div>

      {/* Stats Summary */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[
            { label: 'Total Trades', value: stats.total_trades.toString() },
            { label: 'Win Rate', value: `${stats.win_rate.toFixed(1)}%` },  // backend sends percent
            { label: 'Avg R', value: `${stats.avg_r >= 0 ? '+' : ''}${stats.avg_r.toFixed(2)}R` },
            { label: 'Best Trade', value: `+${stats.best_trade.toFixed(2)}R`, className: 'text-success' },
            { label: 'Worst Trade', value: `${stats.worst_trade.toFixed(2)}R`, className: 'text-danger' },
          ].map(({ label, value, className }) => (
            <Card key={label} className="p-3">
              <p className="text-xs text-muted uppercase tracking-wide">{label}</p>
              <p className={clsx('text-lg font-mono font-bold mt-0.5', className)}>{value}</p>
            </Card>
          ))}
        </div>
      )}

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>
            <Filter className="w-4 h-4 inline mr-2" />
            Filters
          </CardTitle>
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              Clear
            </Button>
          )}
        </CardHeader>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">Symbol</label>
            <input
              type="text"
              placeholder="BTCUSDT"
              value={symbol}
              onChange={(e) => { setSymbol(e.target.value); setPage(1) }}
              className="w-full bg-surface2 border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Side</label>
            <select
              value={side}
              onChange={(e) => { setSide(e.target.value); setPage(1) }}
              className="w-full bg-surface2 border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            >
              <option value="">All</option>
              <option value="LONG">Long</option>
              <option value="SHORT">Short</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Outcome</label>
            <select
              value={outcome}
              onChange={(e) => { setOutcome(e.target.value); setPage(1) }}
              className="w-full bg-surface2 border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            >
              <option value="">All</option>
              <option value="win">Win</option>
              <option value="loss">Loss</option>
              <option value="breakeven">Breakeven</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">From</label>
            <input
              type="date"
              value={fromDate}
              onChange={(e) => { setFromDate(e.target.value); setPage(1) }}
              className="w-full bg-surface2 border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">To</label>
            <input
              type="date"
              value={toDate}
              onChange={(e) => { setToDate(e.target.value); setPage(1) }}
              className="w-full bg-surface2 border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            />
          </div>
        </div>
      </Card>

      {/* Table */}
      <Card className="p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-muted">
            <div className="animate-spin w-6 h-6 border-2 border-primary border-t-transparent rounded-full mr-3" />
            Loading trades...
          </div>
        ) : trades.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted">
            <p className="text-sm">No trades found</p>
            <p className="text-xs mt-1">Trades will appear here once the bot executes them</p>
          </div>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeadCell>Date</TableHeadCell>
                <TableHeadCell>Symbol</TableHeadCell>
                <TableHeadCell>Side</TableHeadCell>
                <TableHeadCell className="text-right">Entry</TableHeadCell>
                <TableHeadCell className="text-right">Exit</TableHeadCell>
                <TableHeadCell className="text-right">Qty</TableHeadCell>
                <TableHeadCell className="text-right">Value (USD)</TableHeadCell>
                <TableHeadCell className="text-right">R</TableHeadCell>
                <TableHeadCell className="text-right">USDT</TableHeadCell>
                <TableHeadCell>Result</TableHeadCell>
                <TableHeadCell>Strategy</TableHeadCell>
              </TableRow>
            </TableHead>
            <tbody>
              {trades.map((trade) => (
                <TableRow key={trade.id} className="border-b border-border/50 hover:bg-surface2/50 transition-colors text-sm">
                  <TableCell className="font-mono text-xs text-muted whitespace-nowrap">{formatDate(trade.closed_at)}</TableCell>
                  <TableCell className="font-mono font-bold">{trade.symbol}</TableCell>
                  <TableCell>
                    <Badge variant={trade.side === 'LONG' ? 'success' : 'danger'}>{trade.side}</Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">{formatPrice(trade.entry_price)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{formatPrice(trade.exit_price)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{trade.quantity.toFixed(4)}</TableCell>
                  {/* position notional at entry = qty × entry_price */}
                  <TableCell className="text-right font-mono text-xs">
                    ${(trade.quantity * trade.entry_price).toFixed(2)}
                  </TableCell>
                  <TableCell className={clsx('text-right font-mono font-bold', trade.pnl_r > 0 ? 'text-success' : trade.pnl_r < 0 ? 'text-danger' : 'text-muted')}>
                    {trade.pnl_r > 0 ? '+' : ''}{trade.pnl_r.toFixed(2)}
                  </TableCell>
                  <TableCell className={clsx('text-right font-mono text-xs', trade.pnl_usdt > 0 ? 'text-success' : trade.pnl_usdt < 0 ? 'text-danger' : 'text-muted')}>
                    {trade.pnl_usdt > 0 ? '+' : ''}{trade.pnl_usdt.toFixed(2)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={trade.exit_reason === 'TP_HIT' ? 'success' : trade.exit_reason === 'SL_HIT' ? 'danger' : 'neutral'}>
                      {trade.exit_reason}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted font-mono uppercase">{trade.strategy_type}</TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted">Page {page} of {totalPages} — {total} total trades</p>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1}
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <span className="text-sm font-mono">{page} / {totalPages}</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
