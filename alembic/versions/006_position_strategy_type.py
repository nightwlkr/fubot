"""Add strategy_type to positions

Revision ID: 006_position_strategy_type
Revises: 005_order_position_id
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '006_position_strategy_type'
down_revision = '005_order_position_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add strategy_type column to positions
    op.add_column('positions', sa.Column('strategy_type', sa.String(20), nullable=False, server_default='UNKNOWN'))


def downgrade() -> None:
    # Remove strategy_type column
    op.drop_column('positions', 'strategy_type')
