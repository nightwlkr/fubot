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

    class Config:
        from_attributes = True


class PositionListResponse(BaseModel):
    positions: list[PositionResponse]


class PositionUpdateRequest(BaseModel):
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
