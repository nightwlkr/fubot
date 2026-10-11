"""Initial tables — create all 10 FUBOT database tables.

Revision ID: 001
Revises:
Create Date: 2026-10-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="admin", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    # ── accounts ───────────────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.sql.expression.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── symbols ────────────────────────────────────────────────────
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.sql.expression.true(), nullable=False),
        sa.Column("max_leverage", sa.Integer(), server_default="10", nullable=False),
        sa.Column("position_limit_usdt", sa.Numeric(precision=18, scale=2), server_default="10000", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol"),
    )

    # ── settings ───────────────────────────────────────────────────
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    # ── signals ────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("strategy_type", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column("entry_zone_min", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("entry_zone_max", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("take_profit", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("rr_ratio", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("volume_confirmed", sa.Boolean(), server_default=sa.sql.expression.false(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signals_symbol"), "signals", ["symbol"], unique=False)
    op.create_index(op.f("ix_signals_status"), "signals", ["status"], unique=False)

    # ── positions ──────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("leverage", sa.Integer(), server_default="10", nullable=False),
        sa.Column("stop_loss", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("take_profit", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="OPEN", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_positions_symbol"), "positions", ["symbol"], unique=False)
    op.create_index(op.f("ix_positions_status"), "positions", ["status"], unique=False)

    # ── trades ─────────────────────────────────────────────────────
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("pnl_usdt", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("pnl_r", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("exit_reason", sa.String(length=30), nullable=False),
        sa.Column("strategy_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_trades_symbol"), "trades", ["symbol"], unique=False)
    op.create_index(op.f("ix_trades_closed_at"), "trades", ["closed_at"], unique=False)

    # ── orders ─────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("binance_order_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False),
        sa.Column("order_type", sa.String(length=10), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("filled_qty", sa.Numeric(precision=18, scale=8), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="NEW", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orders_symbol"), "orders", ["symbol"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)

    # ── equity_log ─────────────────────────────────────────────────
    op.create_table(
        "equity_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("balance_usdt", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_equity_log_recorded_at"), "equity_log", ["recorded_at"], unique=False)

    # ── daily_summary ──────────────────────────────────────────────
    op.create_table(
        "daily_summary",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_trades", sa.Integer(), server_default="0", nullable=False),
        sa.Column("wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("breakeven", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_pnl_r", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("total_pnl_usdt", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False),
        sa.Column("worst_trade_r", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("best_trade_r", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("daily_loss_limit_hit", sa.Boolean(), server_default=sa.sql.expression.false(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date"),
    )
    op.create_index(op.f("ix_daily_summary_date"), "daily_summary", ["date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_summary_date"), table_name="daily_summary")
    op.drop_table("daily_summary")
    op.drop_index(op.f("ix_equity_log_recorded_at"), table_name="equity_log")
    op.drop_table("equity_log")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_symbol"), table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_trades_closed_at"), table_name="trades")
    op.drop_index(op.f("ix_trades_symbol"), table_name="trades")
    op.drop_table("trades")
    op.drop_index(op.f("ix_positions_status"), table_name="positions")
    op.drop_index(op.f("ix_positions_symbol"), table_name="positions")
    op.drop_table("positions")
    op.drop_index(op.f("ix_signals_status"), table_name="signals")
    op.drop_index(op.f("ix_signals_symbol"), table_name="signals")
    op.drop_table("signals")
    op.drop_table("settings")
    op.drop_table("symbols")
    op.drop_table("accounts")
    op.drop_table("users")
