"""Add testnet flag to accounts.

Binance keys are NOT interchangeable — a mainnet key 401s on testnet and vice
versa. Storing them as separate rows (testnet=True/False) lets the bot run a
testnet dry-run with a testnet key without ever sending the mainnet key to
testnet (or the reverse).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004_account_testnet"
down_revision: Union[str, None] = "003_initial_risk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("testnet", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("accounts", "testnet")
