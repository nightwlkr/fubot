# backend/ — FastAPI Trading Bot Backend

## Purpose

Core trading bot API and business logic. Serves the React frontend via REST and WebSocket. Manages all trading operations, market data, user auth, and the trading strategy engine.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/backend/`

## Local Contracts

- Routers mount under `/api` with no version segment: `/api/auth`, `/api/bot`, `/api/dashboard`, `/api/trades`, `/api/signals`, `/api/positions`, `/api/orders`, `/api/settings`, `/api/performance`. `/health` and `/` are mounted at root.
- Auth: one HS256 JWT (24h expiry) issued as the `fubot_token` cookie (`httpOnly`, `secure`, `samesite=lax`). No refresh tokens. Login is rate limited to 5 failed attempts per IP per 15 minutes. All authenticated endpoints accept the JWT via the httpOnly cookie *or* `Authorization: Bearer <token>` header (header fallback added for localStorage-durable token flow).

- API key/secret encrypted at rest using `backend/encryption.py`
- Binance Futures USDS-M only — no spot trading
- Database: PostgreSQL via SQLAlchemy 2.0 async
- All bot state changes logged to `equity_log` table
- Trading defaults come from `backend/config.py`: `RISK_PERCENT`, `MAX_POSITIONS`, `DAILY_LOSS_LIMIT_R` (-3R), `RR_MIN` (3.0), `CHECK_INTERVAL_SECONDS`, `LEVERAGE_BTC`, `LEVERAGE_ALTS`

## Work Guidance

### Stack
- **Framework:** FastAPI (Python 3.11+)
- **ORM:** SQLAlchemy 2.0 async with `asyncpg`
- **Auth:** `python-jose` JWT (HS256, 24h) + `passlib[bcrypt]` password hashing
- **Binance client:** hand-rolled — `httpx` REST in `services/binance_service.py`, `aiohttp` market-data WebSocket in `services/binance_ws.py`. `python-binance` is declared in `requirements.txt`/`pyproject.toml` but is not imported anywhere.
- **Validation:** Pydantic v2 + `pydantic-settings`
- **Indicators:** pure Python over lists of candles. `pandas`, `numpy`, and `pandas-ta` are declared but unused.

### Key Modules

| Path | Purpose |
|------|---------|
| `main.py` | FastAPI app entry — CORS, lifespan (`init_db`/`close_db`), 9 routers, `/health` |
| `config.py` | `Settings` from env vars (DATABASE_URL, FUBOT_SECRET_KEY, FUBOT_ENCRYPTION_KEY, Binance keys, trading defaults) |
| `database.py` | Async engine, `AsyncSessionLocal`, `Base`, `get_db` dependency |
| `encryption.py` | encrypt_value / decrypt_value for API credentials |
| `models/` | 10 SQLAlchemy models: user, account, symbol, setting, signal, position, trade, order, equity_log, daily_summary |
| `routers/` | 9 route modules (28 endpoints): auth, bot, dashboard, signals, positions, orders, settings, performance — plus `routers/position_pricing.py`, the shared open-position pricing helper (exchange-truth in live mode, mark price in paper) |
| `schemas/` | Pydantic request/response models per router |
| `services/` | auth_service, binance_service, binance_ws, order_service, risk_service, strategy_service, signal_service, alert_service |
| `backtest.py` | Backtest harness — replays the scan pipeline over historical klines (no lookahead); CLI in `scripts/backtest.py` |
| `core/` | bot_engine (scan/evaluate/execute loop), position_manager, daily_reset (-3R enforcement), websocket_manager (`ConnectionManager`, `WebSocketRouter`, `ws_manager`) |
| `indicators/` | sma, rsi, trend, fvg, three_candle, pivots, choch, bos, structural, volume_profile, atr |
| `utils/` | Empty placeholder — no shared helpers yet |

### Open Gaps (block PHASE6 validation)

- **Live order path untested against real exchange fills** — `StrategyService.evaluate_signal` live mode (MARKET entry + reduce-only STOP_MARKET SL + TAKE_PROFIT_MARKET TP + `orders`-table logging) is unit-tested with mocks only; PHASE3_STEP7's 72h paper run and PHASE4_STEP4's go-live checklist are the gates.
- BE-trailing now has automated coverage (new-BOS gate, pre-existing-BOS block, DB-stop revert on failed placement) — but the live 5m BOS path itself is still only exercised with synthetic candles, never against a real position.
- `routers/bot.py` (and each router) re-implements cookie auth instead of a shared FastAPI dependency — extract one `get_current_user` dependency.
- 11 models still use `class Config` (`config.py`, `schemas/auth.py`, `signal.py`, `trade.py`, `order.py`, `position.py`, `dashboard.py` ×6) — convert to `ConfigDict`.
- Login rate limiter locks the whole source IP (blocks valid logins from that IP too).
- Dev auth caveat: the cookie is `secure=True`, so over plain http the backend's own cookie is dropped — the frontend's JS cookie (from the response token) is what makes dev work. Consider `secure` only when `FUBOT_ENV == "production"`.

### Architecture notes

- **Session factory, not sessions**: `main.py init_bot` hands every component (`BotEngine`, `PositionManager`, `DailyResetService`, `StrategyService`, `SignalService`, `OrderService`) the `AsyncSessionLocal` FACTORY. AsyncSession is not task-safe — the scan loop runs concurrently with request handlers on the same loop, so each operation opens a short-lived session. Never reintroduce a long-lived shared session.
- **Paper mode records everything**: simulated fills + OPEN Position rows + signal TRIGGERED. This deliberately deviates from PHASE3_STEP3's "no records in paper" because PHASE3_STEP7's 72h paper run and the dashboard need the data.
- `AlertService` methods are async (they broadcast via `ws_manager`); the engine-side callers await them.
- 2026-10-11 second-pass fixes: `/fapi` paths + depth endpoint + reduce-only closing orders, inverted R:R math in FVG/CHoCH/3-candle evaluators, FVG fill semantics + zone bounds, BotEngine/PositionManager wiring, `/ws` route + double-accept + receive-loop disconnects, async AlertService, `get_status` un-awaited coroutine. All covered by `tests/backend/`.
- **Open-position pricing parity (2026-10-15)**: `routers/position_pricing.py` serves both position endpoints. LIVE mode uses the engine's authenticated Binance client (a fresh `BinanceService()` has no creds — it 401s signed endpoints) and reads `positionRisk` per symbol: exchange entryPrice/positionAmt/markPrice/unRealizedProfit/leverage replace the DB recomputation, plus `unrealized_pnl_pct` = PnL ÷ initial margin (Binance's ROI definition). PAPER mode computes from the **mark price** (`fapi/v1/premiumIndex` — the Binance app default; `fapi/v1/ticker/markPrice` never existed). Live entries record the exchange's executedQty + avgPrice on the position row (`get_order` fallback, strictly best-effort — a failed fill fetch must never abort an already-filled entry).
- **Reconciliation runs BOTH directions** (`PositionManager._reconcile_exchange_positions`, every 5 min in live mode): exchange-only positions → recorded OPEN **with the exchange's own resting SL/TP adopted** (reads the TRANSFORMED `get_all_positions()` keys — `quantity`, not raw `positionAmt`; the raw-key read silently skipped every position, so this direction never worked until 2026-10-15); DB-OPEN rows with no exchange position → closed as `EXTERNAL_CLOSE` (resting SL/TP cancelled, NO redundant market close). Rows younger than 120s are skipped (positionRisk race). `get_position_income` is scoped to the position's open time so a close never sums an earlier same-symbol position's PnL.
- **Never assume an engine-detected exit filled on the exchange**: `_close_position` treats TP_HIT/SL_HIT as exchange-side fills, so it calls `_ensure_flat()` — re-reads positionRisk and market-closes any leftover (a rejected BE-stop placement once left XRPUSDT open and naked for 1.5h while the DB showed it closed). `_trail_stop_to_be` places the new stop BEFORE cancelling the old and reverts the DB stop if placement fails: the DB stop must never be tighter than the exchange's.
- **BE-trailing requires a NEW BOS after entry** (user-approved 2026-10-15): `_new_bos_after_entry()` picks the newest same-direction BOS whose `formed_at` (Binance `close_time` epoch ms) is ≥ the position's `opened_at`. Previously `_check_be_trailing` used `structural.last_bos`, which for the setup that produced the entry is a PRE-EXISTING break — so the stop moved to breakeven on the first REST pass (~30s) and ~80% of trades died as scratches.
- **Live entries re-validate R:R at the market price** (user-approved 2026-10-15): live fills are MARKET, not the zone midpoint, so `evaluate_signal` skips when `validate_rr_ratio(market_price, stop, tp, min_rr)` fails even if the midpoint R:R passed (XRP: 3.25:1 planned at the midpoint → ~1.1:1 at the 1%-away fill). Paper keeps the midpoint fill. `_update_position_fill` also recomputes the frozen `initial_risk_distance` from the real entry, so `pnl_r` reflects the risk actually taken.
- **Position sizing has TWO constraints**: `calc_position_size` (risk-based: qty = balance × risk_percent / stop-distance) is then capped by `cap_position_notional` at `max_position_notional_pct` of balance (Settings UI, default 50). The cap is a hard CEILING, so whenever it binds the effective risk falls below `Risk Per Trade` — with ~1% stops a 50% cap turns a 5% target into ~$1-2 per trade (the live reality on 2026-10-15). `StrategyService` logs a WARNING with the symbol and the resulting effective risk % whenever the cap binds; to honor the risk target the cap must be ≥ risk_percent / stop% (e.g. 5% risk with ≥1% stops needs a 500% cap).
- **Settings hot-reload**: `PUT /settings/trading` rewrites the shared settings dict in place (`live.clear(); live.update(await load_bot_settings(db))`) — every engine component is handed that same dict by `main.init_bot`, so slider changes (risk, notional cap, filters, guards) apply to the RUNNING engine. Before 2026-10-15 the engine kept its startup snapshot until a service restart.
- **`_signed_request` code semantics**: algo endpoints return `{"code": "200", "msg": "success"}` on SUCCESS — `code` arrives as a STRING, so compare numerically (`int(data["code"]) != 200`); the naive `data["code"] != 200` raised on every successful `cancel_algo_order`. The raise was client-side only (the DELETE reached Binance), but callers logged success as failure and misjudged outcomes.
- **Orphan sweep on close**: `_cancel_exchange_orders(position_id, symbol)` cancels the orders-table records, then sweeps `openAlgoOrders` for the symbol (new `get_open_algo_orders`) — orders placed outside the orders-table logging (BE-trail stops, manual re-placements) are invisible to the position-linked pass and would otherwise outlive the position and close a FUTURE position prematurely (observed live: a BE-trail stop survived a TP_HIT close). The engine holds one position per symbol, so at close time every resting algo order on the symbol is an orphan.
- `_round_qty`/`_round_price` are Decimal-based: `math.floor(qty/step)` mis-floors exact float multiples (89.3 → 89.2), leaving a 0.1-lot slice uncovered by reduce-only SL/TP.
- `Position.opened_at` defaults to aware UTC now (was naive `utcnow()` → −2h skew that corrupted the reconcile age guard and income windows).

### Coding Rules
- Use async/await for all I/O operations
- Never log or expose API keys/secrets
- All Pydantic models use `ConfigDict` (not `class Config`)
- Database sessions via dependency injection (`get_db`)
- Bot control commands (start/stop) require authenticated user

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 167 passing (e2e trade flow, -3R hard stop, security audit, payload contracts, live-entry fill-record, orphan-sweep, reconcile-adopts-SL/TP, close-flattens-leftover, BE-trail-revert, new-BOS-gate, live-R:R-gate, notional-cap-setting, settings-hot-reload regressions included)
- `PYTHONPATH=. python -c "import backend.main"` — app imports without errors
- `python -m uvicorn backend.main:app --reload` — boots and serves; local dev currently on port 8001 (a stale process holds 8000)
- No hardcoded credentials — everything from env vars

## Child DOX Index

No child AGENTS.md — `routers/`, `schemas/`, `services/`, `core/`, `models/`, `indicators/`, `utils/` are owned by this doc.
