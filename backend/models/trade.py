from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Numeric, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    exit_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    pnl_usdt: Mapped[float] = mapped_column(Numeric, nullable=False)
    pnl_r: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 'live' or 'paper'. Set from BotEngine._mode at the time the trade is recorded.
    # Existing trades default to 'live' via DB server_default.
    mode: Mapped[str] = mapped_column(String(10), default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
