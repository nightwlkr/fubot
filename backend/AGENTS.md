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

- **Live order path untested against real exchange fills** — `StrategyService.evaluate_signal` live mode (MARKET entry + reduce-only STOP_MARKET SL + TAKE_PROFIT_MARKET TP + `orders`-table logging) is unit-tested with mocks only; PHASE3_STEP7's 72h paper run and PHASE4_STEP4's go-live checklist are the gates.
- BE-trailing (`PositionManager._check_be_trailing`) has no automated test; it needs live 5m BOS detection.
- `routers/bot.py` (and each router) re-implements cookie auth instead of a shared FastAPI dependency — extract one `get_current_user` dependency.
- 11 models still use `class Config` (`config.py`, `schemas/auth.py`, `signal.py`, `trade.py`, `order.py`, `position.py`, `dashboard.py` ×6) — convert to `ConfigDict`.
- Login rate limiter locks the whole source IP (blocks valid logins from that IP too).
- Dev auth caveat: the cookie is `secure=True`, so over plain http the backend's own cookie is dropped — the frontend's JS cookie (from the response token) is what makes dev work. Consider `secure` only when `FUBOT_ENV == "production"`.

### Architecture notes

- **Session factory, not sessions**: `main.py init_bot` hands every component (`BotEngine`, `PositionManager`, `DailyResetService`, `StrategyService`, `SignalService`, `OrderService`) the `AsyncSessionLocal` FACTORY. AsyncSession is not task-safe — the scan loop runs concurrently with request handlers on the same loop, so each operation opens a short-lived session. Never reintroduce a long-lived shared session.
- **Paper mode records everything**: simulated fills + OPEN Position rows + signal TRIGGERED. This deliberately deviates from PHASE3_STEP3's "no records in paper" because PHASE3_STEP7's 72h paper run and the dashboard need the data.
- `AlertService` methods are async (they broadcast via `ws_manager`); the engine-side callers await them.
- 2026-10-11 second-pass fixes: `/fapi` paths + depth endpoint + reduce-only closing orders, inverted R:R math in FVG/CHoCH/3-candle evaluators, FVG fill semantics + zone bounds, BotEngine/PositionManager wiring, `/ws` route + double-accept + receive-loop disconnects, async AlertService, `get_status` un-awaited coroutine. All covered by `tests/backend/`.

### Coding Rules
- Use async/await for all I/O operations
- Never log or expose API keys/secrets
- All Pydantic models use `ConfigDict` (not `class Config`)
- Database sessions via dependency injection (`get_db`)
- Bot control commands (start/stop) require authenticated user

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 102 passing (indicators, encryption, auth, services, signal pipeline against the real DB, Binance paths via httpx MockTransport, WS manager, API smoke with a real app lifespan)
- `PYTHONPATH=. python -c "import backend.main"` — app imports without errors
- `python -m uvicorn backend.main:app --reload` — boots and serves; local dev currently on port 8001 (a stale process holds 8000)
- No hardcoded credentials — everything from env vars

## Child DOX Index

No child AGENTS.md — `routers/`, `schemas/`, `services/`, `core/`, `models/`, `indicators/`, `utils/` are owned by this doc.
