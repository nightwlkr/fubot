from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Numeric, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    binance_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Link to the Position that spawned this order (live mode). Lets the
    # position manager cancel the exchange SL/TP when a position closes.
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    filled_qty: Mapped[float] = mapped_column(Numeric, default=0)
    status: Mapped[str] = mapped_column(String(20), default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
