# backend/ — FastAPI Trading Bot Backend

## Purpose

Core trading bot API and business logic. Serves the React frontend via REST and WebSocket. Manages all trading operations, market data, user auth, and the trading strategy engine.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/backend/`

## Local Contracts

- Routers mount under `/api` with no version segment: `/api/auth`, `/api/bot`, `/api/dashboard`, `/api/trades`, `/api/signals`, `/api/positions`, `/api/orders`, `/api/settings`, `/api/performance`. `/health` and `/` are mounted at root.
- Auth: one HS256 JWT (24h expiry) issued as the `fubot_token` cookie (`httpOnly`, `secure`, `samesite=lax`). No refresh tokens. Login is rate limited to 5 failed attempts per IP per 15 minutes.
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
| `routers/` | 9 route modules (28 endpoints): auth, bot, dashboard, trades, signals, positions, orders, settings, performance |
| `schemas/` | Pydantic request/response models per router |
| `services/` | auth_service, binance_service, binance_ws, order_service, risk_service, strategy_service, signal_service, alert_service |
| `core/` | bot_engine (scan/evaluate/execute loop), position_manager, daily_reset (-3R enforcement), websocket_manager (`ConnectionManager`, `WebSocketRouter`, `ws_manager`) |
| `indicators/` | sma, rsi, trend, fvg, three_candle, pivots, choch, bos, structural, volume_profile, atr |
| `utils/` | Empty placeholder — no shared helpers yet |

### Open Gaps (block PHASE6 validation)

- `main.py` never builds a `BotEngine` and never calls `routers/bot.set_bot_engine()`; `/api/bot/start|stop|status` return 503 or a stub status.
- No WebSocket route is registered in `main.py`. `WebSocketRouter.websocket_endpoint` is unused, so nothing serves `/ws` or `/api/ws`. `AlertService.signal()` also calls the async `ws_manager.broadcast()` without `await` — real broadcasts are never delivered.
- `services/signal_service.py` `scan_symbol` is a stub returning `None` — the candle-fetch → trend-bias → FVG/3-candle/CHoCH → volume/R:R filter pipeline from FUBOT_STEPS PHASE2_STEP7 is not implemented.
- `services/order_service.py` never calls Binance — `place_order(simulate=False)` still only records a dict; the entry/SL/TP flow from PHASE3_STEP1 does not exist.
- `services/binance_service.py` REST paths miss the `/fapi` prefix (e.g. `{base}/v1/klines` instead of `{base}/fapi/v1/klines`) and `get_order_book` targets the nonexistent `deepBook` endpoint — every REST call 404s.
- Inverted reward/risk math in `fvg.evaluate_fvg_entry`, `choch.detect_choch`, and `three_candle.evaluate_three_candle_entry` makes every entry invalid (`rr = risk/reward = 1/rr_min`); `fvg.check_fvg_filled` reports bullish FVGs filled at formation — root cause is the step spec itself (PHASE2_STEP3 says "bullish filled when high ≥ zone_min" while storing `zone_min` as the gap *top*), so fix spec and code together. All pinned by strict-xfail tests in `tests/backend/test_indicators_more.py`.
- `routers/bot.py` (and each router) re-implements cookie auth instead of using a shared FastAPI dependency — extract one `get_current_user` dependency.
- 11 models still use `class Config` (`config.py`, `schemas/auth.py`, `signal.py`, `trade.py`, `order.py`, `position.py`, `dashboard.py` ×6) — convert to `ConfigDict`.
- `WebSocketRouter` keeps a `while True: sleep(1)` keep-alive that never observes `WebSocketDisconnect`; disconnect handling must come from the receive loop.
- Audit fixes landed 2026-10-11 (route double-prefix, `auth_service.UserResponse`, dashboard `/today`, encryption nonce, apikey masking, `daily_reset` logger, RSI flat series) — covered by `tests/backend/` (see root AGENTS.md Audit results).

### Coding Rules
- Use async/await for all I/O operations
- Never log or expose API keys/secrets
- All Pydantic models use `ConfigDict` (not `class Config`)
- Database sessions via dependency injection (`get_db`)
- Bot control commands (start/stop) require authenticated user

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 78 passing + 4 strict-xfail (indicators, encryption, auth, services, WS manager, API smoke against the real DB)
- `PYTHONPATH=. python -c "import backend.main"` — app imports without errors
- `python -m uvicorn backend.main:app --reload` — boots and serves; local dev currently on port 8001 (a stale process holds 8000)
- No hardcoded credentials — everything from env vars

## Child DOX Index

No child AGENTS.md — `routers/`, `schemas/`, `services/`, `core/`, `models/`, `indicators/`, `utils/` are owned by this doc.
