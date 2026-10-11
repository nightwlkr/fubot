import { useEffect, useState } from 'react'
import { useDashboardStore } from '../../store/dashboardStore'
import { Badge } from '../ui/Badge'
import { clsx } from 'clsx'

export default function Header() {
  const { botStatus, todayStats } = useDashboardStore()
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const isRunning = botStatus?.status === 'running'
  const mode = botStatus?.mode || 'unknown'

  const pnlR = todayStats?.pnl_r ?? 0
  const pnlUsdt = todayStats?.pnl_usdt ?? 0

  return (
    <header className="h-14 bg-surface border-b border-border flex items-center justify-between px-6 shrink-0">
      {/* Left: Bot Status */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div
            className={clsx(
              'w-2 h-2 rounded-full',
              isRunning ? 'bg-success animate-pulse' : 'bg-muted'
            )}
          />
          <span className="text-sm font-medium">
            {isRunning ? 'Bot Running' : 'Bot Stopped'}
          </span>
        </div>

        <Badge variant={mode === 'paper' ? 'warning' : 'danger'}>
          {mode.toUpperCase()}
        </Badge>

        {botStatus && (
          <span className="text-xs text-muted font-mono hidden sm:block">
            SCAN #{botStatus?.scan_count?.toLocaleString() ?? 0}
          </span>
        )}
      </div>

      {/* Right: P&L + Clock */}
      <div className="flex items-center gap-6">
        {/* Today's P&L */}
        <div className="text-right hidden sm:block">
          <p className="text-xs text-muted uppercase tracking-wide">Today P&L</p>
          <p
            className={clsx(
              'text-sm font-mono font-bold',
              pnlR >= 0 ? 'text-success' : 'text-danger'
            )}
          >
            {pnlR >= 0 ? '+' : ''}{pnlR.toFixed(2)}R
            <span className="text-muted font-normal text-xs ml-1">
              ({pnlUsdt >= 0 ? '+' : ''}{pnlUsdt.toFixed(2)} USDT)
            </span>
          </p>
        </div>

        {/* UTC Clock */}
        <div className="text-right">
          <p className="text-xs text-muted uppercase tracking-wide">UTC</p>
          <p className="text-sm font-mono text-text">
            {now.toISOString().slice(11, 19)}
          </p>
        </div>
      </div>
    </header>
  )
}
