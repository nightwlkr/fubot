# FUBOT

Automated Binance Futures (USDS-M) trading bot with a React dashboard, implementing the
Crypto Banter 6-pillar strategy (50-SMA trend filter, FVG / 3-candle / CHoCH entries,
volume confirmation, ≥3:1 R:R, hard -3R daily loss limit).

- **Production:** https://fubot.bedikian.cc (nginx + Let's Encrypt on the `contabo01` VPS)
- **Repo:** https://github.com/nightwlkr/fubot (private)
- **Docs:** `FUBOT_PRODUCT_PLAN.md` (product spec) · `FUBOT_STEPS/` (42-step build guide) ·
  `AGENTS.md` (current status + contracts)

## Architecture

```
backend/    FastAPI (Python 3.11+) — 9 routers, 8 services, 11 indicators, bot engine
  core/       bot_engine (scan loop) · position_manager (BE trailing, TP/SL) ·
              daily_reset (-3R hard stop) · websocket_manager
  services/   signal_service (scan pipeline) · strategy_service (decision layer) ·
              order_service · risk_service · binance_service (httpx REST) ·
              binance_ws (aiohttp) · auth_service · alert_service
  indicators/ sma · rsi · trend · fvg · three_candle · pivots · choch · bos ·
              structural · volume_profile · atr  (pure Python, no pandas)
frontend/   React 19 + Vite + TS + Tailwind v4 + Zustand 5 — 6 pages, WebSocket live data
alembic/    async migrations (001 tables, 002 seed)
nginx/      production reverse-proxy config (TLS, rate limit, WS upgrade)
scripts/    create_admin · init_db · clear_test_data · paper_test_report ·
            ws_load_test · setup_ssl · fubotctl
```

**API:** all routers mount under `/api` (`/api/auth`, `/api/bot`, `/api/dashboard`,
`/api/trades`, `/api/signals`, `/api/positions`, `/api/orders`, `/api/settings`,
`/api/performance`); `/health` at root; WebSocket at `/ws` (alias `/api/ws`).

## Strategy engine

1. **Scan loop** (`check_interval_seconds`, default 60s): for each enabled symbol —
   fetch 1h/5m/15m candles in parallel → ATR(14) + trend bias on 1h →
   detect FVG/3-candle on 5m, CHoCH on 15m → filters (trend, volume, R:R) →
   signals persisted as PENDING (or SKIPPED with reason).
2. **Decision layer**: open-position guard → max positions → signal age (60 min) →
   price-deviation gate (±1% of entry zone) → R:R validation → position sizing
   (1% risk default) → execution.
3. **Execution**: paper mode = simulated fills recorded in the DB; live mode = MARKET
   entry + reduce-only STOP_MARKET SL + TAKE_PROFIT_MARKET TP on Binance.
4. **Position manager** (every 30s): monitors prices, trails SL to break-even on new
   BOS, closes on TP/SL, records trades with PnL in R and USDT.
5. **-3R hard stop**: cumulative daily losses ≥ 3R → trading blocked, bot stopped,
   CRITICAL alert; resets at UTC midnight. Wins never count toward the limit.

## Local development

```bash
# prerequisites: Python 3.11+, PostgreSQL 14+, Node 20+
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # then fill in the values (see Configuration)

alembic upgrade head            # create tables + seeds
python scripts/create_admin.py  # admin user from ADMIN_USERNAME/PASSWORD

cd frontend && npm ci && npm run dev   # dashboard on :5173 (proxies /api → :8000)

# backend (separate terminal)
uvicorn backend.main:app --reload --port 8000
```

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://fubot:<pw>@host:5432/fubot` |
| `FUBOT_SECRET_KEY` | JWT signing (32+ chars) |
| `FUBOT_ENCRYPTION_KEY` | AES-256-GCM key for API credentials (32+ chars) |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Futures API keys (testnet for paper) |
| `BINANCE_TESTNET` | `true` → testnet endpoints |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | dashboard admin |
| `DOMAIN` | public domain (CORS allow-list) |

Trading defaults (also editable in the dashboard → Settings):
`RISK_PERCENT` 1.0 · `MAX_POSITIONS` 3 · `DAILY_LOSS_LIMIT_R` -3.0 · `RR_MIN` 3.0 ·
`CHECK_INTERVAL_SECONDS` 60 · `LEVERAGE_BTC` 10 · `LEVERAGE_ALTS` 5.

## Production (VPS)

Layout: `/opt/fubot` (system user `fubot`, Python venv in `.venv/`), nginx site
`/etc/nginx/sites-available/fubot`, certs via certbot (webroot `/var/www/certbot`),
service `fubot.service` (enabled, restarts automatically).

```bash
ssh contabo01                                # root access is configured

# update the deployment
cd /opt/fubot && git pull
cd frontend && npm ci && npm run build && cd ..
systemctl restart fubot

# paper-trading data
.venv/bin/python scripts/clear_test_data.py
.venv/bin/python scripts/paper_test_report.py --start "2026-10-11 23:00"

# service control
systemctl status fubot && journalctl -u fubot -f
```

The bot runs continuously in **paper mode** (testnet market data, simulated fills,
no real orders). To go live: set `BINANCE_TESTNET=false`, add restricted live API
keys, and `POST /api/bot/start {"mode":"live"}` — after the go-live checklist
(`FUBOT_STEPS/PHASE6_STEP4.md`) and the 72h paper validation.

## Testing

```bash
PYTHONPATH=. pytest tests/backend/ -q    # 113 tests
cd frontend && npm run build && npm run lint
```

Coverage: all 11 indicators, encryption, auth (JWT/bcrypt/rate limit), risk sizing,
strategy decisions, signal pipeline (real DB), Binance request building
(httpx MockTransport), WebSocket manager, security audit (cookie flags, encrypted
credentials, no secrets in logs, injection resistance), end-to-end trade flow,
-3R hard stop, API smoke over a real app lifespan.

## Troubleshooting

| Symptom | Check |
|---|---|
| `/api/bot/*` returns 503 | engine not wired — see `main.py init_bot` |
| Bot scans but never trades | signals all SKIPPED (`/api/signals?status=SKIPPED` shows reasons) — trend/volume/R:R filters |
| "Daily limit reached" | `-3R` hit today; resets at UTC midnight (`daily_summary.daily_loss_limit_hit`) |
| WS dashboard silent | backend must serve `/ws` or `/api/ws`; nginx needs the `Upgrade` headers on `/api/` |
| `PendingRollbackError` in logs | a shared session crossed tasks — engine code must use the session FACTORY, never a long-lived session |
| Equity curve empty | balance snapshots need valid Binance API keys (signed `/fapi/v2/balance`) |
