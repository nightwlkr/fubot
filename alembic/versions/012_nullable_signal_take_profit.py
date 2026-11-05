"""signals.take_profit becomes nullable — "no fixed target" is a real state.

The DONCHIAN paper strategy exits on a ratcheting trailing stop and has no
take-profit level at all. The NOT NULL constraint forced either a fabricated
target or skipping signal persistence; NULL now means "managed exit", and
consumers render it as absent (never 0). Same precedent as
`010_nullable_trade_pnl_r` for trades.pnl_r.

Revision ID: 012_nullable_signal_take_profit
Revises: 011_daily_risk_state
"""

from alembic import op
import sqlalchemy as sa


revision = "012_nullable_signal_take_profit"
down_revision = "011_daily_risk_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "signals", "take_profit",
        existing_type=sa.Numeric(), nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "signals", "take_profit",
        existing_type=sa.Numeric(), nullable=False,
    )
