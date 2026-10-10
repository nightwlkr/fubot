# Phase 1 — Step 2: PostgreSQL Tables with Alembic Migrations
## FUBOT: Database Schema & Migrations

---

## Objective
Create all 10 database tables using SQLAlchemy ORM models + Alembic migrations. All tables must exist in PostgreSQL before any other backend work begins.

---

## Context
- **Database**: PostgreSQL at `localhost:5432`, database name `fubot`
- **ORM**: SQLAlchemy 2.0 with asyncpg (async)
- **Migrations**: Alembic
- **Depends on**: Phase 1 Step 1 (config.py, database.py)

---

## Tables to Create

All tables below. Use the exact column names, types, and constraints listed.

### `users`
```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'admin',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `accounts`
```sql
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    api_key_encrypted TEXT NOT NULL,
    api_secret_encrypted TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `symbols`
```sql
CREATE TABLE symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    max_leverage INT NOT NULL DEFAULT 10,
    position_limit_usdt NUMERIC NOT NULL DEFAULT 10000
);
```

### `settings`
```sql
CREATE TABLE settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `signals`
```sql
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    strategy_type VARCHAR(20) NOT NULL,  -- FVG, 3CANDLE, CHOC
    direction VARCHAR(5) NOT NULL,        -- LONG, SHORT
    entry_zone_min NUMERIC NOT NULL,
    entry_zone_max NUMERIC NOT NULL,
    stop_loss NUMERIC NOT NULL,
    take_profit NUMERIC NOT NULL,
    rr_ratio NUMERIC NOT NULL,
    volume_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING, TRIGGERED, EXPIRED, SKIPPED
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_at TIMESTAMPTZ,
    skip_reason TEXT
);
CREATE INDEX idx_signals_symbol ON signals(symbol);
CREATE INDEX idx_signals_status ON signals(status);
```

### `positions`
```sql
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    signal_id INT REFERENCES signals(id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(5) NOT NULL,  -- LONG, SHORT
    entry_price NUMERIC NOT NULL,
    quantity NUMERIC NOT NULL,
    leverage INT NOT NULL DEFAULT 10,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN', -- OPEN, CLOSED, LIQUIDATED
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_status ON positions(status);
```

### `trades`
```sql
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    position_id INT REFERENCES positions(id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(5) NOT NULL,
    entry_price NUMERIC NOT NULL,
    exit_price NUMERIC NOT NULL,
    quantity NUMERIC NOT NULL,
    pnl_usdt NUMERIC NOT NULL,
    pnl_r NUMERIC NOT NULL,
    exit_reason VARCHAR(30) NOT NULL, -- TP_HIT, SL_HIT, BE_TRAIL, MANUAL, LIQUIDATION
    strategy_type VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_closed_at ON trades(closed_at);
```

### `orders`
```sql
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    binance_order_id BIGINT,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(5) NOT NULL,
    order_type VARCHAR(10) NOT NULL, -- LIMIT, MARKET, STOP, TRAILING
    price NUMERIC,
    quantity NUMERIC NOT NULL,
    filled_qty NUMERIC NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'NEW', -- NEW, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_status ON orders(status);
```

### `equity_log`
```sql
CREATE TABLE equity_log (
    id SERIAL PRIMARY KEY,
    balance_usdt NUMERIC NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_equity_log_recorded_at ON equity_log(recorded_at);
```

### `daily_summary`
```sql
CREATE TABLE daily_summary (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    total_trades INT NOT NULL DEFAULT 0,
    wins INT NOT NULL DEFAULT 0,
    losses INT NOT NULL DEFAULT 0,
    breakeven INT NOT NULL DEFAULT 0,
    total_pnl_r NUMERIC NOT NULL DEFAULT 0,
    total_pnl_usdt NUMERIC NOT NULL DEFAULT 0,
    worst_trade_r NUMERIC NOT NULL DEFAULT 0,
    best_trade_r NUMERIC NOT NULL DEFAULT 0,
    daily_loss_limit_hit BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_daily_summary_date ON daily_summary(date);
```

---

## Deliverables

### 1. SQLAlchemy Models
Create one model file per table under `backend/models/`:
- `backend/models/__init__.py` — imports all models
- `backend/models/user.py`
- `backend/models/account.py`
- `backend/models/symbol.py`
- `backend/models/setting.py`
- `backend/models/signal.py`
- `backend/models/position.py`
- `backend/models/trade.py`
- `backend/models/order.py`
- `backend/models/equity_log.py`
- `backend/models/daily_summary.py`

Use SQLAlchemy 2.0 style (async, `Mapped` declarative style).

### 2. Alembic Setup
- `alembic.ini` pointing to `postgresql+asyncpg://...` URL
- `alembic/env.py` configured for async SQLAlchemy 2.0
- `alembic/versions/` directory with an initial migration that creates all 10 tables

### 3. Default Data Seeding
In `alembic/seed_data.py` or a separate Alembic migration:
- Insert default symbols: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `ADAUSDT`, `DOGEUSDT`
- Insert default settings:
  - `risk_percent`: `1.0`
  - `max_positions`: `3`
  - `daily_loss_limit_r`: `-3.0`
  - `rr_min`: `3.0`
  - `check_interval_seconds`: `60`
  - `leverage_btc`: `10`
  - `leverage_alts`: `5`
  - `strategy_fvg_enabled`: `true`
  - `strategy_3candle_enabled`: `true`
  - `strategy_choch_enabled`: `true`

### 4. Validation
- Run `alembic upgrade head` — succeeds with no errors
- Connect to DB and verify all 10 tables exist
- Verify default symbols and settings are inserted

---

## Dependencies
- **Requires**: Phase 1 Step 1 complete (config.py, database.py must work)

## Files Modified/Created
All files under `/home/vick/fubot/backend/models/`, `/home/vick/fubot/alembic/`, `/home/vick/fubot/scripts/`

## Next Step
Phase 1 Step 3: AES-256 Encryption + Binance Service

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
