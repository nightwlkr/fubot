# alembic/ — Database Migrations

## Purpose

SQLAlchemy 2.0 async migrations for all FUBOT database tables. All schema changes must go through Alembic — no direct ALTER TABLE in production.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/alembic/`

## Local Contracts

- Migration files live in `alembic/versions/`
- Each migration filename: `{timestamp}_{description}.py`
- All models defined in `backend/models/` are the single source of truth for schema
- `alembic.ini` uses `sqlalchemy.url` from env var or `backend/config.py`

## Work Guidance

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
- `daily_summaries` — end-of-day equity snapshots
- `equity_log` — intraday equity curve points

## Verification

- `alembic current` shows the latest applied revision
- `alembic history --enumerate` shows the full migration chain
- All tables exist in PostgreSQL with expected columns

## Child DOX Index

No child AGENTS.md — migration files in `versions/` are transient artifacts, not domains.
