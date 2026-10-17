import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { RefreshCw, Radio } from 'lucide-react'
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
  volume_confirmed: boolean
  status: 'PENDING' | 'TRIGGERED' | 'EXPIRED' | 'SKIPPED'
  created_at: string
  skip_reason?: string
}

type Filter = 'ALL' | 'PENDING' | 'TRIGGERED' | 'SKIPPED'

function SignalCard({ signal }: { signal: Signal }) {
  const isLong = signal.direction === 'LONG'
  const rr = signal.rr_ratio

  return (
    <Card
      className="p-4"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className={clsx('w-8 h-8 rounded flex items-center justify-center', isLong ? 'bg-success/10' : 'bg-danger/10')}>
            {isLong ? <TrendingUp className="w-4 h-4 text-success" /> : <TrendingDown className="w-4 h-4 text-danger" />}
          </div>
          <div>
            <p className="font-mono font-bold text-base">{signal.symbol}</p>
            <div className="flex items-center gap-2 mt-0.5">
              <Badge variant={isLong ? 'success' : 'danger'}>{signal.direction}</Badge>
              <Badge variant="neutral" className="font-mono text-xs uppercase">{signal.strategy_type}</Badge>
            </div>
          </div>
        </div>
        <div className="text-right">
          <Badge
            variant={
              signal.status === 'TRIGGERED' ? 'success' :
              signal.status === 'PENDING' ? 'info' :
              signal.status === 'SKIPPED' ? 'warning' : 'neutral'
            }
          >
            {signal.status}
          </Badge>
          {signal.skip_reason && (
            <p className="text-xs text-muted mt-1 max-w-[160px] text-right">{signal.skip_reason}</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-3">
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Entry Zone</p>
          <p className="text-sm font-mono font-medium mt-0.5">
            {signal.entry_zone_min.toFixed(2)}
            <span className="text-muted text-xs"> – {signal.entry_zone_max.toFixed(2)}</span>
          </p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Stop Loss</p>
          <p className="text-sm font-mono text-danger font-medium mt-0.5">{signal.stop_loss.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">Take Profit</p>
          <p className="text-sm font-mono text-success font-medium mt-0.5">{signal.take_profit.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-muted uppercase tracking-wide">R:R</p>
          <p className="text-sm font-mono font-bold mt-0.5 flex items-center gap-1">
            <Target className="w-3 h-3 text-muted" />
            <span className={rr >= 3 ? 'text-success' : rr >= 2 ? 'text-warning' : 'text-danger'}>1:{rr.toFixed(1)}</span>
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4 text-xs text-muted border-t border-border/50 pt-2">
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
      setSignals(Array.isArray(res.data.signals) ? res.data.signals : [])
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

  useEffect(() => {
    const id = setInterval(() => fetchSignals(false), 30000)
    return () => clearInterval(id)
  }, [fetchSignals])

  const handleRefresh = () => {
    setIsRefreshing(true)
    fetchSignals(false)
  }

  const pendingCount = signals.filter(s => s.status === 'PENDING').length
  const triggeredCount = signals.filter(s => s.status === 'TRIGGERED').length
  const skippedCount = signals.filter(s => s.status === 'SKIPPED').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Radio className="w-5 h-5 text-primary" />
            Scanner
          </h1>
          <p className="text-sm text-muted mt-0.5">Live signal detection — auto-refreshes every 30s</p>
        </div>
        <div className="flex items-center gap-3">
          <p className="text-xs text-muted font-mono">Updated {lastRefresh.toLocaleTimeString()}</p>
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
          { label: 'Triggered', count: triggeredCount, variant: 'success' as const },
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
        {(['ALL', 'PENDING', 'TRIGGERED', 'SKIPPED'] as Filter[]).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              filter === f ? 'border-primary text-primary' : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {f.charAt(0) + f.slice(1).toLowerCase()}
            {f === 'ALL' && signals.length > 0 && (
              <span className="ml-2 text-xs bg-surface2 px-1.5 py-0.5 rounded font-mono">{signals.length}</span>
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
            {filter === 'ALL' ? 'Signals will appear when the bot detects setups' : `No ${filter.toLowerCase()} signals`}
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          {signals.map(signal => (
            <SignalCard key={signal.id} signal={signal} />
          ))}
        </div>
      )}
    </div>
  )
}
