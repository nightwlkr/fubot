from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Numeric, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG, SHORT
    entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, default=10)
    stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
