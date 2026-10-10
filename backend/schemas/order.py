"""Order-related schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class OrderResponse(BaseModel):
    id: int
    binance_order_id: Optional[int] = None
    symbol: str
    side: str
    order_type: str
    price: Optional[float] = None
    quantity: float
    filled_qty: float
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]


class OrderCancelResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
