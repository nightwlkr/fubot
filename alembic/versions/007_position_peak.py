"""Add position peak tracking for runner-trailing

Revision ID: 007_position_peak
Revises: 006_position_strategy_type
Create Date: 2026-10-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007_position_peak"
down_revision: Union[str, None] = "006_position_strategy_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Runner-trail (DECISION.md D3) needs the best favourable price seen since
    # entry: the stop trails TRAIL_R behind that peak, one-way. Kept on the row
    # (not in memory) so a service restart never ratchets the stop DOWN — a
    # restart that forgot the peak would re-arm from the current price and
    # could place a stop tighter than the exchange's, which is exactly the
    # failure class the BE-trail revert logic already guards against.
    op.add_column(
        "positions",
        sa.Column("peak_favorable_price", sa.Numeric(30, 12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("positions", "peak_favorable_price")
