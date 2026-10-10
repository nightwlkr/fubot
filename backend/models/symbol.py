from sqlalchemy import String, Integer, Boolean, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_leverage: Mapped[int] = mapped_column(Integer, default=10)
    position_limit_usdt: Mapped[float] = mapped_column(Numeric, default=10000)
