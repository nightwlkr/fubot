# alembic/ — Database Migrations

## Purpose

SQLAlchemy 2.0 async migrations for all FUBOT database tables. All schema changes must go through Alembic — no direct ALTER TABLE in production.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/alembic/`
Status: local and production head `011_daily_risk_state` (production upgrade verified 2026-10-23 after commit `1138142`); 10 revisions exist

## Local Contracts

- Migration files live in `alembic/versions/`, named `NNN_description.py` with `revision = "NNN_description"` (sequential, not timestamped)
- All models defined in `backend/models/` are the single source of truth for schema
- `alembic/env.py` overrides `sqlalchemy.url` from `backend/config.py` `settings.DATABASE_URL` — the value in `alembic.ini` is a placeholder; do not edit it
- `env.py` puts the repo root on `sys.path` and imports every model so autogenerate sees the full schema

## Work Guidance

### Current State
- Chain: `001_initial` → `002_seed_data` → `003_initial_risk` → `004_account_testnet` → `005_order_position_id` → `006_position_strategy_type` → `008_add_more_symbols` → `009_add_mode` → `010_nullable_trade_pnl_r` → `011_daily_risk_state` (mode-scoped daily breaker latch and manual-reset cutoff) — local head
- `002_seed_data` inserts into the JSON `settings.value` via raw `sa.text()` — values MUST be `json.dumps()`-ed because asyncpg's JSON codec takes pre-serialized text, not Python objects
- Fresh environment provisioning:
```bash
psql -c "CREATE ROLE fubot LOGIN PASSWORD '<password>'" -c "CREATE DATABASE fubot OWNER fubot"
alembic upgrade head
```

### Standard Commands
```bash
# Create a new migration (after changing a model)
alembic revision --autogenerate -m "add column X to table Y"

# Apply pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Check current migration status
alembic current
alembic history
```

### Key Tables
- `users` — bot operator accounts
- `symbols` — trading pairs (e.g. BTCUSDT)
- `accounts` — exchange API credentials (encrypted)
- `positions` — open futures positions
- `orders` — placed and filled orders
- `trades` — completed trade records
- `signals` — generated trading signals
- `settings` — bot configuration key-value store
- `daily_summary` — end-of-day equity snapshots (singular table name)
- `daily_risk_state` — UTC daily breaker latch/reset baseline, unique by date and trading mode
- `equity_log` — intraday equity curve points

## Verification

- `PYTHONPATH=. alembic history` — shows the full chain ending at `011_daily_risk_state (head)` (installed alembic 1.13.1 has no `--enumerate` flag)
- `PYTHONPATH=. alembic upgrade head --sql` — renders the migration SQL offline; apply only after tests and deployment review
- `PYTHONPATH=. alembic current` — checks the connected database; production reported `011_daily_risk_state (head)` after the 2026-10-23 deployment
- All 10 tables + seed rows verified present in PostgreSQL

## Child DOX Index

No child AGENTS.md — migration files in `versions/` are transient artifacts, not domains.
