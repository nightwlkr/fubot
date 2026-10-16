from datetime import datetime, timezone
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
    # Frozen entry-to-SL distance at open. pnl_r MUST use this, not the live
    # stop_loss — BE-trailing moves stop_loss toward entry, which would shrink
    # the denominator and inflate |R| on a later close.
    initial_risk_distance: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    strategy_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")  # FVG, 3CANDLE, CHOC, etc.
    # Best favourable price since entry — the runner-trail (DECISION.md D3)
    # trails TRAIL_R behind this peak, one-way. Persisted so a restart never
    # lowers a live stop below the exchange's resting order.
    peak_favorable_price: Mapped[float | None] = mapped_column(
        Numeric(30, 12), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    # timezone-aware UTC now — the old naive datetime.utcnow() default made
    # Postgres interpret the wall time as session-TZ (+02), skewing every
    # opened_at by -2h. That skew fed the reconcile age guard and the
    # income-API startTime (exchange PnL attribution).
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
