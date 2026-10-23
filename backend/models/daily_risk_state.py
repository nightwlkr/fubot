"""Mode-scoped daily entry-breaker state.

Daily PnL and trade counts are derived from mode-tagged Trade rows. This table
persists the manual-reset baseline and latch, including trips caused by
unrealized PnL.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class DailyRiskState(Base):
    __tablename__ = "daily_risk_state"
    __table_args__ = (UniqueConstraint("date", "mode", name="uq_daily_risk_state_date_mode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    limit_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
