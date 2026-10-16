"""Add more Binance USDS-M perpetual symbols.

Revision ID: 008_add_more_symbols
Revises: 006_position_strategy_type
Create Date: 2026-10-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "008_add_more_symbols"
down_revision: Union[str, None] = "006_position_strategy_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Symbols added in this migration (not present in 002_seed_data.py).
# Tuples: (symbol, max_leverage, position_limit_usdt)
NEW_SYMBOLS: list[tuple[str, int, float]] = [
    # High-cap established alts
    ("DOGEUSDT",   20, 10000.0),
    ("NEARUSDT",   20, 10000.0),
    ("SUIUSDT",    20, 10000.0),
    ("ADAUSDT",    20, 10000.0),
    ("ALGOUSDT",   20, 10000.0),
    # DeFi / infrastructure
    ("UNIUSDT",    20, 10000.0),
    ("AAVEUSDT",   10, 10000.0),
    ("GRTUSDT",    20, 10000.0),
    # L2 / modular
    ("ARBUSDT",    20, 10000.0),
    ("OPUSDT",     20, 10000.0),
    # Other notable
    ("FILUSDT",    20, 10000.0),
    ("INJUSDT",    20, 10000.0),
    ("STXUSDT",    20, 10000.0),
    ("TIAUSDT",    20, 10000.0),
    ("APEUSDT",    20, 10000.0),
]


def upgrade() -> None:
    connection = op.get_bind()
    for symbol, max_lev, pos_limit in NEW_SYMBOLS:
        connection.execute(
            sa.text(
                "INSERT INTO symbols (symbol, enabled, max_leverage, position_limit_usdt) "
                "VALUES (:symbol, true, :lev, :plimit)"
            ),
            {"symbol": symbol, "lev": max_lev, "plimit": pos_limit},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for symbol, *_ in NEW_SYMBOLS:
        connection.execute(
            sa.text("DELETE FROM symbols WHERE symbol = :s"),
            {"s": symbol},
        )
