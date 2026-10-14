"""Add position_id to orders.

Lets the position manager cancel a closed position's exchange SL/TP orders by
looking up their binance order ids. Without this, a live-mode close (TP hit,
manual close-all) left the exchange SL/TP resting — and in live mode an orphan
SL would fire and open a NEW (opposite) position once the bot is restarted.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005_order_position_id"
down_revision: Union[str, None] = "004_account_testnet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("position_id", sa.Integer(), nullable=True))
    op.create_index("ix_orders_position_id", "orders", ["position_id"])
    # order_type was VARCHAR(10) but the live code writes STOP_MARKET (11) and
    # TAKE_PROFIT_MARKET (18) — every live SL/TP order log failed with a
    # truncation error (caught, so it didn't crash, but the orders were never
    # recorded → couldn't be cancelled on close). Widen to fit.
    op.alter_column("orders", "order_type",
                    type_=sa.String(20),
                    existing_type=sa.String(10),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column("orders", "order_type",
                    type_=sa.String(10),
                    existing_type=sa.String(20),
                    existing_nullable=False)
    op.drop_index("ix_orders_position_id", table_name="orders")
    op.drop_column("orders", "position_id")
