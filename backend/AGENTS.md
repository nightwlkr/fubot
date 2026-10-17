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
| `indicators/` | sma, rsi, trend, fvg, three_candle, pivots, choch, bos, structural, volume_profile, atr, order_block — plus **vwap, orb** (prototype-only, NOT wired into `scan_symbol`; used by `scripts/strategy_compare.py` for the 3-strategy comparison backtest) |
| `indicators/atr.py` | `calculate_atr` (scalar, full-series — what the LIVE scanner uses) + `calculate_atr_series` (per-index, lookahead-free; backtests only). `series[0] == 0.0` and indices `1..period` are a running average, so callers must skip non-positive values. Never swap the series into `signal_service` without that guard. |
| `utils/` | Empty placeholder — no shared helpers yet |

### Confirmed defects (2026-10-17 review, `analysis/REVIEW_2026-10-17.md`)

**All Tier-1 items below are FIXED** (uncommitted, `test_review_2026_10_17.py` locks each one in). Verify each is still open before re-fixing — several were fixed here and earlier in the same day (`f3810be`, `f8838cb`).

- **Trail stops stack** (`position_manager.py:527,558-584`): `modify_position_stop`'s returned `algoId` is discarded, so `_cancel_active_sl` re-cancels the already-dead entry SL and every trail after the first leaves the previous stop armed. Profit-lock books at the first trail level.
- **Double-close race** (`position_manager.py:280-299` vs `301-353`, `85-133`): only `_on_tick` takes `_check_locks[symbol]`; the `status=="OPEN"` guard at 606-609 is a non-atomic read-then-write spanning `get_position_income` + `session.get(Signal)`. Duplicated Trade rows and doubled daily R can trip the -3R stop on phantom R. Needs a conditional `UPDATE ... WHERE status='OPEN'` + rowcount check.
- **Naked on live close failure** (`position_manager.py:731-733`): protective orders are cancelled BEFORE `_market_close`, which swallows all exceptions.
- **`PATCH /positions/{id}` is DB-only** (`routers/positions.py:107-113`): never calls `modify_position_stop`, so the dashboard can show a stop the exchange doesn't have. `PositionUpdateRequest` also has no bounds or side check.
- **Orphan GTC entry limits** (`strategy_service.py:491,500-524`): `asyncio.CancelledError` is a `BaseException`, so `except Exception` does not cancel the resting limit on stop/restart. `get_open_orders` has zero callers — nothing sweeps them.
- **Failed-flow contradiction** (`strategy_service.py:322-334` vs `549-553`): `_flatten_if_unprotected` deliberately leaves the position open when a stop rests, then `_close_position_row` closes the row anyway.
- **`PUT /settings/trading` has no bounds** (`schemas/settings.py:44-67`): every field is a bare `float | None`. The `ge/le` bounds in `config.py` are never consulted on this path — `daily_loss_limit_r=9999` disables the -3R stop, `check_interval_seconds=0` spins the loop. Also: `strategy_obfvg_max_age_bars` is clamped on READ only (`routers/settings.py:124`); `load_bot_settings` feeds the engine the raw row, so response clamping is not enforcement.
- **Rate-limit reads as "flat"** (`binance_service.py:104-134`): 3× 429 falls out of the retry loop to `return {}`, and `get_position` treats that as "no position" — defeating `_ensure_flat` and letting the scan open a second position on an exposed symbol.
- **Per-tick DB hammer** (`position_manager.py:291`): the bookTicker handler opens a session and SELECTs all open positions per message (10-100/sec). The per-symbol lock doesn't throttle it — a local SELECT is ~1ms.
- **`GET /bot/status` unauthenticated** (`routers/bot.py:140-148`): the `_bot_engine is None` early return precedes the auth call.
- **`DELETE /orders/{id}` is DB-only** (`routers/orders.py:71-91`), and no auth router reads `user.role`; there are no `user_id` columns, so any authenticated account is a full admin. No token revocation — logout only clears the cookie.
- **`/docs` + `/openapi.json` unauthenticated** (`main.py`): `FastAPI()` registers them by default.
- **Rate-limiter weights are understated** (`binance_service.py`): klines limit=200 → weight 2 (declared 1), positionRisk → 5 (declared 1), balance → 5 (declared 1). The 1200/min local budget is consumed at ~half the true rate, so its margin is illusory. `get_lot_size` also re-downloads full `exchangeInfo` on every order placement with no cache. **FIXED in `f8838cb`** (real weights) and now (per-symbol `_lot_cache`).

Still open from the review (not money-path, deliberately deferred):
- **`GET /bot/status` unauthenticated** (`routers/bot.py`): the `_bot_engine is None` early return precedes the auth call. Move `get_current_user_from_cookie` to the first statement in all five bot handlers.
- **`DELETE /orders/{id}` is DB-only** (`routers/orders.py`): sets status CANCELLED while the order stays armed on the exchange.
- **No authorization model**: no router reads `user.role`, and there are no `user_id` columns, so any authenticated account is a full admin. No token revocation — logout only clears the cookie; the JWT stays valid 24h. There is no registration endpoint today, so this is an operational invariant, not an enforced control.
- **`/docs` + `/openapi.json` unauthenticated** (`main.py`): `FastAPI()` registers them by default; pass `docs_url=None, redoc_url=None, openapi_url=None` when `FUBOT_ENV == "production"`.

### Open Gaps (block PHASE6 validation)

- **Live order path**: calls are exercised with mocks and the mainnet flow has been running since 2026-10-14, but the LIMIT-entry path (added 2026-10-15) has not yet produced a real fill in production — watch the first few trades.
- BE-trailing has automated coverage (trigger R, one-way behaviour, configurable value, DB-stop revert on failed placement) but is still only exercised with stubs, never against a live position.
- `routers/bot.py` (and each router) re-implements cookie auth instead of a shared FastAPI dependency — extract one `get_current_user` dependency.
- 11 models still use `class Config` (`config.py`, `schemas/auth.py`, `signal.py`, `trade.py`, `order.py`, `position.py`, `dashboard.py` ×6) — convert to `ConfigDict`.
- Login rate limiter locks the whole source IP (blocks valid logins from that IP too).
- Dev auth caveat: the cookie is `secure=True`, so over plain http the backend's own cookie is dropped — the frontend's JS cookie (from the response token) is what makes dev work. Consider `secure` only when `FUBOT_ENV == "production"`.

### Architecture notes

- **Scan loop must not make a signed request per symbol** (rate-limit guard, 2026-10-17): `_scan_and_evaluate` used to call `orders.get_current_position(symbol)` (a signed `positionRisk`) once per enabled symbol — ~24 calls per pass — which tripped Binance's IP rate limit (`-1003` → HTTP 400/**429**) and blanked EVERY signed call for ~60s (the limit self-clears), making the bot look dead. It now fetches ALL open positions **once per pass** via `OrderService.get_all_open_positions()` (one `positionRisk` returns every position) and re-uses the dict for the "already have a position" check — ~1 signed request/scan instead of ~24. Regression test asserts 1 bulk fetch + 0 per-symbol calls over a 24-symbol pass. Rule: **any per-symbol loop over `symbols` must not issue a signed Binance call per iteration** — batch it (one endpoint returns all) or move it behind the cheap local filters. NOTE: `evaluate_signal` still issues one signed `positionRisk` PER PENDING signal (~7 weight each, measured ~307/2400 per min total) — it is the remaining signed source that scales with signal count, not symbol count.
- **The high-frequency paths must not touch the database** (2026-10-17): the bookTicker handler fired 10-100+/sec and ran a fresh session + `SELECT * FROM positions` per message — hundreds of queries/sec on the pool the scan loop and the 30s pass need. The per-symbol lock does NOT throttle it (a local SELECT is ~1ms, so the lock is free >99% of the time). This is the same event-loop starvation class as the old WS task leak and, unlike a rate limit, produces no diagnostic signal. `_get_open_positions` now refreshes `_open_positions_cache` and the tick path reads `_cached_open_positions()`. **Rule: anything called per message, per tick or per row must read a snapshot, not the database.**
- **Closing a position must CLAIM it atomically** (2026-10-17): `_close_position` is reached from the WS tick path, the 30s REST pass and the 5-min reconcile. Its old `status == "OPEN"` check was a read-then-write spanning `get_position_income` + `session.get(Signal)`, so two paths could both observe OPEN and both commit a Trade — duplicating the trade and DOUBLING the daily R total, which can trip the -3R hard stop on phantom R and halt trading for the day. It now claims the close with `UPDATE positions SET status='CLOSED' WHERE id=:id AND status='OPEN'` and bails on `rowcount == 0`, rolling back its own Trade. The in-memory check remains only as a cheap fast path.
- **Fail closed when the exchange state is unknown** (2026-10-17): three call sites read "I couldn't reach the exchange" as "there is no position". A rate-limited `_signed_request` returned `{}` (now raises `BinanceAPIError(-1003)`); `get_lot_size` returned `{}`, silently disabling qty/price rounding so an unrounded SL was rejected and the filled position had no stop (now raises, and caches filters per symbol); `_flatten_if_unprotected` returned `None`/`[]` on error, so a blip could close a protected position or orphan an open one (now returns `bool` — `False` means "state unknown or deliberately left open", and the caller keeps the DB row OPEN for reconcile adoption). **Rule: in this codebase an empty/falsy exchange response means UNKNOWN, never flat — and a sweep that cannot identify the order to keep must do nothing rather than cancel blindly.**
- **Manual stop changes go through the exchange** (2026-10-17): `PATCH /api/positions/{id}` wrote SL/TP to the DB only, so Binance's resting algo stop was unchanged while the dashboard showed the new one — a naked position by another route. It now places `modify_position_stop` first and commits the row only on success (502 otherwise), sweeps superseded stops, and validates the stop/target are on the correct sides for the position's direction.
- **Sweep the exchange for stops, not the orders table** (`f3810be`, 2026-10-17): the trail path posts a new STOP_MARKET but never writes its `algoId` back to `orders`, so a table-scoped cancel only ever found the ORIGINAL entry SL and every trailed stop stayed armed (observed: 7 stacked stops on OPUSDT; the widest one closed the position before any profit-lock stop could). `_cancel_active_sl` now sweeps `openAlgoOrders` for the symbol, keeping `keep_algo_id`. **When `keep_algo_id` is unknown the sweep is skipped entirely** — cancelling blind would remove the stop just placed and leave the position naked, which is strictly worse than extra reduce-only stops.
- **Session factory, not sessions**: `main.py init_bot` hands every component (`BotEngine`, `PositionManager`, `DailyResetService`, `StrategyService`, `SignalService`, `OrderService`) the `AsyncSessionLocal` FACTORY. AsyncSession is not task-safe — the scan loop runs concurrently with request handlers on the same loop, so each operation opens a short-lived session. Never reintroduce a long-lived shared session.
- **Paper mode records everything**: simulated fills + OPEN Position rows + signal TRIGGERED. This deliberately deviates from PHASE3_STEP3's "no records in paper" because PHASE3_STEP7's 72h paper run and the dashboard need the data.
- `AlertService` methods are async (they broadcast via `ws_manager`); the engine-side callers await them.
- 2026-10-11 second-pass fixes: `/fapi` paths + depth endpoint + reduce-only closing orders, inverted R:R math in FVG/CHoCH/3-candle evaluators, FVG fill semantics + zone bounds, BotEngine/PositionManager wiring, `/ws` route + double-accept + receive-loop disconnects, async AlertService, `get_status` un-awaited coroutine. All covered by `tests/backend/`.
- **Open-position pricing parity (2026-10-15)**: `routers/position_pricing.py` serves both position endpoints. LIVE mode uses the engine's authenticated Binance client (a fresh `BinanceService()` has no creds — it 401s signed endpoints) and reads `positionRisk` per symbol: exchange entryPrice/positionAmt/markPrice/unRealizedProfit/leverage replace the DB recomputation, plus `unrealized_pnl_pct` = PnL ÷ initial margin (Binance's ROI definition). PAPER mode computes from the **mark price** (`fapi/v1/premiumIndex` — the Binance app default; `fapi/v1/ticker/markPrice` never existed). Live entries record the exchange's executedQty + avgPrice on the position row (`get_order` fallback, strictly best-effort — a failed fill fetch must never abort an already-filled entry).
- **Reconciliation runs BOTH directions** (`PositionManager._reconcile_exchange_positions`, every 5 min in live mode): exchange-only positions → recorded OPEN **with the exchange's own resting SL/TP adopted** (reads the TRANSFORMED `get_all_positions()` keys — `quantity`, not raw `positionAmt`; the raw-key read silently skipped every position, so this direction never worked until 2026-10-15); DB-OPEN rows with no exchange position → closed as `EXTERNAL_CLOSE` (resting SL/TP cancelled, NO redundant market close). Rows younger than 120s are skipped (positionRisk race). `get_position_income` is scoped to the position's open time so a close never sums an earlier same-symbol position's PnL.
- **Never assume an engine-detected exit filled on the exchange**: `_close_position` treats TP_HIT/SL_HIT as exchange-side fills, so it calls `_ensure_flat()` — re-reads positionRisk and market-closes any leftover (a rejected BE-stop placement once left XRPUSDT open and naked for 1.5h while the DB showed it closed). `_trail_stop_to_be` places the new stop BEFORE cancelling the old and reverts the DB stop if placement fails: the DB stop must never be tighter than the exchange's.
- **BE-trailing is price-based** (user-approved 2026-10-15 after the audit): `_check_be_trailing` trails to break-even once the trade is `be_trail_trigger_r` R in profit (setting, default **+0.5R**, slider in Settings) — one-way, and only when `initial_risk_distance` is known. The earlier "new BOS after entry" rule was rarely satisfied, so ~80% of trades rode to the full structural stop; at that stop-out rate full -1R losses make expectancy negative (`0.8×(-1R) + 0.2×3R = -0.2R` vs `+0.6R` with the BE stop).
- **Live entries are LIMIT orders at the entry-zone midpoint** (user-approved 2026-10-15): "the planned price or no trade". MARKET fills landed up to 1% outside the zone (degrading the planned R:R) and, when the market had already passed the stop, produced an invalid SL that Binance rejected with 400 — aborting the flow after the entry filled and leaving the position NAKED (live XRPUSDT/BTCUSDT, unprotected for hours). `_enter_with_limit` polls the order briefly, cancels + skips when unfilled, and the Position row is created only AFTER the fill (exchange-truth entry/qty from the start).
- **The backend serves the built SPA** (2026-10-16, NPM cut-over): FastAPI mounts `frontend/dist/assets` and registers a `/{full_path:path}` catch-all returning `index.html` (client-side routing) — Nginx Proxy Manager is a dumb reverse proxy that cannot serve files or do try_files. API/WS routes are registered BEFORE the catch-all and win by order; the handler also hard-guards `api/|ws|health|docs|openapi` prefixes so a future route reorder cannot swallow the API.
- **Strategy toggles are now consumed by the scan** (2026-10-16, DECISION.md D1): `strategy_fvg_enabled` / `strategy_3candle_enabled` / `strategy_choch_enabled` existed in the Settings API but were never read — a 'disabled' strategy kept firing (2,804 wasted FVG signals). `scan_symbol` now gates each detection block on its flag (default True). FVG is disabled in production (stop geometry produced 15.6% targets, -0.397R/trade live, 0/5).
- **OB+FVG is wired into the scan** (user-approved 2026-10-17, `signal_service.detect_obfvg_candidates`): a 1h Fair Value Gap only fires when a confirmed same-direction Order Block on the 5m series overlaps its zone — `fvg_within_order_block(..., current_idx=len(k5m)-1)`. Plain FVG is disabled at -0.397R/trade; the OB confluence is the differentiator (+0.432R ungated / **+0.315R with the live trend gate**, 20 symbols, R:R 3.0, 84 trades / 3.5 days). It then passes through the SAME filter chain as every other strategy (trend bias, trend strength, RSI, volume, R:R) — no bypass.
  - **Freshness is the load-bearing constraint.** The backtest entered each gap at the first 5m bar after its forming hour closed. Live has no historical index to enter at, so eligibility is bounded by RECENCY instead: only FVGs on the last `strategy_obfvg_max_age_bars` CLOSED 1h bars (default 2). Raising this is NOT free — it widens the trade away from what was measured, and lowering it toward 0 starts missing gaps at the 60s scan cadence.
  - It is a 1h pattern, so the volume filter must read `k1h`, not `k5m` — the routing check is `strategy_type in ("FVG", "OBFVG")` (a bare `== "FVG"` silently fed it 5m volume).
  - Evidence is thin (3.5 days; `--days 45` yields ~3.5 days because Binance caps unpaginated klines at 1000 bars). Default is ON per user decision — treat early OBFVG results as unvalidated.
  - The backtest harness had a **lookahead bug** in strategy C, now fixed via `fvg_entry_idx_5m`: an FVG at 1h bar `i` is only knowable at that hour's CLOSE (5m `i*12+11`), but the exit scan started at `+1`. It inflated expectancy from +0.315R to +0.402R.
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

- `PYTHONPATH=. pytest tests/backend/ -q` — 266 passing (e2e trade flow, -3R hard stop, security audit, payload contracts, orphan-sweep, reconcile-adopts-SL/TP, close-flattens-leftover, BE-trail trigger/revert, limit-entry fill+cancel+partial+on-cancel, breached-stop skip, side-aware R:R, notional-cap-setting, settings-hot-reload+bounds, OB+FVG scan gating + freshness, scan rate-limit guard, the 2026-10-17 review regressions in `test_review_2026_10_17.py`, prototype-indicator + backtest-harness regressions included)
- `PYTHONPATH=. python -c "import backend.main"` — app imports without errors
- `python -m uvicorn backend.main:app --reload` — boots and serves; local dev currently on port 8001 (a stale process holds 8000)
- No hardcoded credentials — everything from env vars

## Child DOX Index

No child AGENTS.md — `routers/`, `schemas/`, `services/`, `core/`, `models/`, `indicators/`, `utils/` are owned by this doc.
