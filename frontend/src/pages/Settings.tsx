import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { Card, CardHeader, CardTitle } from '../components/ui/Card'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { clsx } from 'clsx'
import { Save, Eye, EyeOff, ShieldCheck, AlertTriangle } from 'lucide-react'

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

        <div className="flex justify-end">
          <Button onClick={handleSave} isLoading={isSaving} disabled={isSaving || !apiKey || !apiSecret}>
            <Save className="w-4 h-4" />
            Save API Key
          </Button>
        </div>
      </div>
    </Card>
  )
}

function TradingParamsSection() {
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const [riskPercent, setRiskPercent] = useState(2)
  const [maxPositions, setMaxPositions] = useState(5)
  const [dailyLossLimit, setDailyLossLimit] = useState(3)
  const [rrMin, setRrMin] = useState(3.0)
  const [checkInterval, setCheckInterval] = useState(60)

  useEffect(() => {
    api.get<TradingSettings>(ENDPOINTS.settingsTrading)
      .then(res => {
        const s = res.data
        setRiskPercent(s.risk_percent)
        setMaxPositions(s.max_positions)
        setDailyLossLimit(s.daily_loss_limit_r)
        setRrMin(s.rr_min)
        setCheckInterval(s.check_interval_seconds)
        setIsLoading(false)
      })
      .catch(() => setIsLoading(false))
  }, [])

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await api.put(ENDPOINTS.settingsTrading, {
        risk_percent: riskPercent,
        max_positions: maxPositions,
        daily_loss_limit_r: dailyLossLimit,
        rr_min: rrMin,
        check_interval_seconds: checkInterval,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      console.error('Failed to save trading settings:', err)
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
          { label: 'Risk Per Trade', value: riskPercent, setValue: setRiskPercent, min: 0.5, max: 5, step: 0.5, suffix: '%', desc: 'Percentage of balance risked per trade' },
          { label: 'Max Open Positions', value: maxPositions, setValue: setMaxPositions, min: 1, max: 10, step: 1, suffix: '', desc: 'Maximum concurrent positions' },
          { label: 'Daily Loss Limit', value: dailyLossLimit, setValue: setDailyLossLimit, min: 1, max: 10, step: 0.5, suffix: 'R', desc: 'Bot stops trading after this loss (in R)' },
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

      <div className="flex justify-end mt-6 pt-4 border-t border-border">
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

  useEffect(() => {
    api.get<{ symbols: SymbolConfig[] }>(ENDPOINTS.settingsSymbols)
      .then(res => setSymbols(res.data.symbols))
      .catch(console.error)
      .finally(() => setIsLoading(false))
  }, [])

  const toggleSymbol = async (symbol: string, enabled: boolean) => {
    try {
      await api.put(ENDPOINTS.settingsSymbol(symbol), { enabled })
      setSymbols(prev => prev.map(s => s.symbol === symbol ? { ...s, enabled } : s))
    } catch (err) {
      console.error('Failed to toggle symbol:', err)
    }
  }

  const updateLeverage = async (symbol: string, max_leverage: number) => {
    try {
      await api.put(ENDPOINTS.settingsSymbol(symbol), { max_leverage })
      setSymbols(prev => prev.map(s => s.symbol === symbol ? { ...s, max_leverage } : s))
    } catch (err) {
      console.error('Failed to update leverage:', err)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Symbol Configuration</CardTitle>
      </CardHeader>

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
