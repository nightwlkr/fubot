"""Seed default symbols and settings.

Revision ID: 002
Revises: 001_initial
Create Date: 2026-10-10

"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_seed_data"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Default trading symbols ──────────────────────────────────────────
DEFAULT_SYMBOLS: list[tuple[str, int, float]] = [
    ("BTCUSDT", 100, 100000.0),
    ("ETHUSDT", 50, 50000.0),
    ("SOLUSDT", 20, 10000.0),
    ("BNBUSDT", 20, 15000.0),
    ("AVAXUSDT", 20, 10000.0),
    ("LINKUSDT", 20, 10000.0),
    ("MATICUSDT", 20, 10000.0),
    ("DOTUSDT", 20, 10000.0),
    ("XRPUSDT", 20, 10000.0),
    ("APTUSDT", 20, 10000.0),
]


# ── Default bot settings ──────────────────────────────────────────────
DEFAULT_SETTINGS: list[tuple[str, object]] = [
    ("risk_percent", 1.0),
    ("max_positions", 3),
    ("daily_loss_limit_r", -3.0),
    ("rr_min", 3.0),
    ("check_interval_seconds", 60),
    ("leverage_btc", 10),
    ("leverage_alts", 5),
    ("strategy_fvg_enabled", True),
    ("strategy_3candle_enabled", True),
    ("strategy_choch_enabled", True),
]


def upgrade() -> None:
    connection = op.get_bind()

    # ── Insert symbols ──────────────────────────────────────────────
    with op.batch_alter_table("symbols", schema=None) as batch_op:
        # No schema changes needed; we just insert data.
        pass

    for symbol, max_lev, pos_limit in DEFAULT_SYMBOLS:
        connection.execute(
            sa.text(
                "INSERT INTO symbols (symbol, enabled, max_leverage, position_limit_usdt) "
                "VALUES (:symbol, true, :lev, :plimit)"
            ),
            {"symbol": symbol, "lev": max_lev, "plimit": pos_limit},
        )

    # ── Insert settings ─────────────────────────────────────────────
    # asyncpg requires JSON columns to receive pre-serialized JSON text.
    for key, value in DEFAULT_SETTINGS:
        connection.execute(
            sa.text("INSERT INTO settings (key, value) VALUES (:key, :val)"),
            {"key": key, "val": json.dumps(value)},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for symbol, *_ in DEFAULT_SYMBOLS:
        connection.execute(
            sa.text("DELETE FROM symbols WHERE symbol = :s"),
            {"s": symbol},
        )
    for key, *_ in DEFAULT_SETTINGS:
        connection.execute(
            sa.text("DELETE FROM settings WHERE key = :k"),
            {"k": key},
        )
