from datetime import datetime, date
from sqlalchemy import String, Integer, DateTime, Numeric, Date, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class DailySummary(Base):
    __tablename__ = "daily_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    breakeven: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl_r: Mapped[float] = mapped_column(Numeric, default=0)
    total_pnl_usdt: Mapped[float] = mapped_column(Numeric, default=0)
    worst_trade_r: Mapped[float] = mapped_column(Numeric, default=0)
    best_trade_r: Mapped[float] = mapped_column(Numeric, default=0)
    daily_loss_limit_hit: Mapped[bool] = mapped_column(Boolean, default=False)
