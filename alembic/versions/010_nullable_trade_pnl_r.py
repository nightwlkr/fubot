"""trades.pnl_r becomes nullable — "unknown R" is a real state, not zero.

1R is defined by `positions.initial_risk_distance`, frozen at open. It is
genuinely unavailable for some rows: the reconcile/adopt path
(`PositionManager._reconcile_exchange_positions`) creates positions with
`initial_risk_distance = NULL` because a position that predates the bot has no
recorded original stop. Live evidence that the NOT NULL constraint was forcing
a lie: an identical -$1.06 BTCUSDT stop-out was recorded as -1.46R, -1.91R and
-1.97R on trades 325/316/319, and trade 354 read -18.26R.

Consumers must therefore treat NULL as *unknown*, never as 0R -- a zero would
let an unmeasurable trade pass the -3R daily breaker untouched. The fail-closed
rule lives in `backend.core.risk_units.daily_r_contribution`.

Revision ID: 010_nullable_trade_pnl_r
Revises: 009_add_mode
"""

from alembic import op
import sqlalchemy as sa

revision = "010_nullable_trade_pnl_r"
down_revision = "009_add_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "trades", "pnl_r",
        existing_type=sa.Numeric(precision=10, scale=4),
        nullable=True,
    )


def downgrade() -> None:
    # Rows written while NULL was allowed have no defensible R. Backfill them at
    # the conservative worst-case single-trade loss (-1R) rather than 0, so the
    # re-imposed NOT NULL does not silently certify them as breakeven.
    op.execute("UPDATE trades SET pnl_r = -1.0 WHERE pnl_r IS NULL")
    op.alter_column(
        "trades", "pnl_r",
        existing_type=sa.Numeric(precision=10, scale=4),
        nullable=False,
    )
