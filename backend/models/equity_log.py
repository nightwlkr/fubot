from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class EquityLog(Base):
    __tablename__ = "equity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance_usdt: Mapped[float] = mapped_column(Numeric, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
