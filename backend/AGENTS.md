# backend/ — FastAPI Trading Bot Backend

## Purpose

Core trading bot API and business logic. Serves the React frontend via REST and WebSocket. Manages all trading operations, market data, user auth, and the trading strategy engine.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/backend/`

## Local Contracts

- All routes are prefixed with `/api/v1`
- Auth via JWT bearer token (short-lived access + refresh tokens)
- API key/secret encrypted at rest using `backend/encryption.py`
- Binance Futures USDS-M only — no spot trading
- Database: PostgreSQL via SQLAlchemy 2.0 async
- All bot state changes logged to `equity_log` table

## Work Guidance

### Stack
- **Framework:** FastAPI (Python 3.11+)
- **ORM:** SQLAlchemy 2.0 async with `asyncpg`
- **Auth:** JWT (PyJWT) — access token 15min, refresh token 7 days
- **Binance client:** `python-binance` (official) + custom WebSocket handler in `services/binance_ws.py`
- **Validation:** Pydantic v2

### Key Modules

| Path | Purpose |
|------|---------|
| `main.py` | FastAPI app entry — includes CORS, lifespan, router aggregation |
| `config.py` | Settings from env vars (DATABASE_URL, JWT_SECRET, etc.) |
| `database.py` | Async session factory, engine, get_db dependency |
| `encryption.py` | encrypt_value / decrypt_value for API credentials |
| `models/` | SQLAlchemy models: account, daily_summary, equity_log, order, position, setting, signal, symbol, trade, user |
| `routers/` | API route modules: auth, bot, dashboard, orders, performance, settings, signals, trades |
| `schemas/` | Pydantic request/response models |
| `services/auth_service.py` | JWT creation, validation, refresh |
| `services/binance_service.py` | Binance Futures REST calls (balance, positions, orders) |
| `services/binance_ws.py` | Binance WebSocket streams (user data, market ticker) |
| `indicators/` | Technical analysis (RSI, SMA — pandas-ta based) |

### Coding Rules
- Use async/await for all I/O operations
- Never log or expose API keys/secrets
- All Pydantic models use `ConfigDict` (not `class Config`)
- Database sessions via dependency injection (`get_db`)
- Bot control commands (start/stop) require authenticated user

## Verification

- `python -m uvicorn backend.main:app --reload` starts the server without import errors
- All routers return valid Pydantic-serializable responses
- No hardcoded credentials — everything from env vars

## Child DOX Index

- **backend/routers/** — API route handlers (auth, bot, dashboard, orders, performance, settings, signals, trades)
- **backend/models/** — SQLAlchemy ORM models
- **backend/services/** — Binance integration and auth services
- **backend/indicators/** — Technical analysis indicators (RSI, SMA)
