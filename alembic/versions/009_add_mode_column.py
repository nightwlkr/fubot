"""Add mode column to positions and trades.

Tracks whether a position/trade was in 'live' or 'paper' mode.
Without this column a service restart defaults to paper and mixes history.

Revision ID: 009_add_mode
Revises: 008_add_more_symbols
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "009_add_mode"
down_revision: Union[str, None] = "008_add_more_symbols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("mode", sa.String(length=10), nullable=False, server_default="live"))
    op.add_column("trades",    sa.Column("mode", sa.String(length=10), nullable=False, server_default="live"))


def downgrade() -> None:
    op.drop_column("trades",    "mode")
    op.drop_column("positions", "mode")
