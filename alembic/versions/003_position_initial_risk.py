"""Add initial_risk_distance to positions.

Freezes the entry-to-stop distance at open so pnl_r is computed against the
ORIGINAL risk, not the live stop_loss (which BE-trailing moves toward entry and
would otherwise inflate |R| on close).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003_initial_risk"
down_revision: Union[str, None] = "002_seed_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("initial_risk_distance", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "initial_risk_distance")
