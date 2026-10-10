"""Signal-related schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SignalResponse(BaseModel):
    id: int
    symbol: str
    strategy_type: str
    direction: str
    entry_zone_min: float
    entry_zone_max: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    volume_confirmed: bool
    status: str
    created_at: datetime
    skip_reason: Optional[str] = None

    class Config:
        from_attributes = True


class SignalListResponse(BaseModel):
    signals: list[SignalResponse]


class SignalCreateRequest(BaseModel):
    symbol: str = Field(min_length=1)
    strategy_type: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    entry_zone_min: float
    entry_zone_max: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    volume_confirmed: bool = False
