"""Position-related schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PositionResponse(BaseModel):
    id: int
    signal_id: Optional[int] = None
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    mark_price: float = 0.0        # mark price (Binance premiumIndex in paper, positionRisk in live)
    unrealized_pnl: float = 0.0    # USDT — exchange-truth in live mode, computed in paper
    unrealized_pnl_pct: float = 0.0  # ROI % vs initial margin (Binance's definition)
    liquidation_price: float = 0.0  # approximate liq price (cross margin)
    margin_usd: float = 0.0         # initial margin = collateral at risk, USD

    class Config:
        from_attributes = True


class PositionListResponse(BaseModel):
    positions: list[PositionResponse]


class PositionUpdateRequest(BaseModel):
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
