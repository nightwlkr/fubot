"""Persist daily breaker latches independently for live and paper modes.

Revision ID: 011_daily_risk_state
Revises: 010_nullable_trade_pnl_r
"""
from alembic import op
import sqlalchemy as sa

revision = "011_daily_risk_state"
down_revision = "010_nullable_trade_pnl_r"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_risk_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("limit_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("date", "mode", name="uq_daily_risk_state_date_mode"),
    )
    # The legacy latch was mixed-mode and could represent realized or
    # unrealized losses. During a same-day upgrade, preserve it for live only:
    # losing the latch could resume live entries after an unrealized-loss trip
    # whose position has since closed. A false positive lasts at most until the
    # next UTC day boundary; paper mode starts with its own independent latch.
    op.execute(
        """INSERT INTO daily_risk_state (date, mode, limit_hit)
           SELECT (now() AT TIME ZONE 'UTC')::date, 'live', TRUE
           FROM daily_summary
           WHERE date = (now() AT TIME ZONE 'UTC')::date
             AND daily_loss_limit_hit IS TRUE
           ON CONFLICT (date, mode) DO NOTHING"""
    )


def downgrade() -> None:
    op.drop_table("daily_risk_state")
