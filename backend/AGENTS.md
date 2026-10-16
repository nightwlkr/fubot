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

- **Live order path**: calls are exercised with mocks and the mainnet flow has been running since 2026-10-14, but the LIMIT-entry path (added 2026-10-15) has not yet produced a real fill in production — watch the first few trades.
- BE-trailing has automated coverage (trigger R, one-way behaviour, configurable value, DB-stop revert on failed placement) but is still only exercised with stubs, never against a live position.
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
- **BE-trailing is price-based** (user-approved 2026-10-15 after the audit): `_check_be_trailing` trails to break-even once the trade is `be_trail_trigger_r` R in profit (setting, default **+0.5R**, slider in Settings) — one-way, and only when `initial_risk_distance` is known. The earlier "new BOS after entry" rule was rarely satisfied, so ~80% of trades rode to the full structural stop; at that stop-out rate full -1R losses make expectancy negative (`0.8×(-1R) + 0.2×3R = -0.2R` vs `+0.6R` with the BE stop).
- **Live entries are LIMIT orders at the entry-zone midpoint** (user-approved 2026-10-15): "the planned price or no trade". MARKET fills landed up to 1% outside the zone (degrading the planned R:R) and, when the market had already passed the stop, produced an invalid SL that Binance rejected with 400 — aborting the flow after the entry filled and leaving the position NAKED (live XRPUSDT/BTCUSDT, unprotected for hours). `_enter_with_limit` polls the order briefly, cancels + skips when unfilled, and the Position row is created only AFTER the fill (exchange-truth entry/qty from the start).
- **Strategy toggles are now consumed by the scan** (2026-10-16, DECISION.md D1): `strategy_fvg_enabled` / `strategy_3candle_enabled` / `strategy_choch_enabled` existed in the Settings API but were never read — a 'disabled' strategy kept firing (2,804 wasted FVG signals). `scan_symbol` now gates each detection block on its flag (default True). FVG is disabled in production (stop geometry produced 15.6% targets, -0.397R/trade live, 0/5).
- **Setup validity + side awareness** (audit fixes): `evaluate_signal` gate 4b skips a setup whose stop has already been breached by the market (or whose target is already reached) — for LONG the stop must sit below and the target above the market, reverse for SHORT. `RiskService.validate_rr_ratio(..., direction=)` is side-aware; the old `abs()` version scored a LONG whose stop was ABOVE its entry at **93:1** and executed it.
- **`_flatten_if_unprotected`**: after ANY live-flow failure (or an unconfirmed limit fill) the exchange position is checked — a position with no resting STOP_MARKET is market-closed immediately; one WITH a stop is left for the reconcile to adopt (with its SL/TP). Before this a failed flow dropped the DB row and left the position naked.
- **R:R is validated at the price we will actually get** (user-approved 2026-10-15): the gate runs against the zone midpoint (paper fill) and, for live, against the current market price before the limit is placed — XRP planned 3.25:1 at the midpoint but the market was 1% away (~1.1:1). `_update_position_fill` recomputes the frozen `initial_risk_distance` from the actual fill, so `pnl_r` reflects the risk actually taken.
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

- `PYTHONPATH=. pytest tests/backend/ -q` — 172 passing (e2e trade flow, -3R hard stop, security audit, payload contracts, orphan-sweep, reconcile-adopts-SL/TP, close-flattens-leftover, BE-trail trigger/revert, limit-entry fill+cancel, breached-stop skip, side-aware R:R, notional-cap-setting, settings-hot-reload regressions included)
- `PYTHONPATH=. python -c "import backend.main"` — app imports without errors
- `python -m uvicorn backend.main:app --reload` — boots and serves; local dev currently on port 8001 (a stale process holds 8000)
- No hardcoded credentials — everything from env vars

## Child DOX Index

No child AGENTS.md — `routers/`, `schemas/`, `services/`, `core/`, `models/`, `indicators/`, `utils/` are owned by this doc.
