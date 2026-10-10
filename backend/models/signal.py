from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Numeric, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)  # FVG, 3CANDLE, CHOC
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # LONG, SHORT
    entry_zone_min: Mapped[float] = mapped_column(Numeric, nullable=False)
    entry_zone_max: Mapped[float] = mapped_column(Numeric, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric, nullable=False)
    take_profit: Mapped[float] = mapped_column(Numeric, nullable=False)
    rr_ratio: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
