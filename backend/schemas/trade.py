"""Trade-related schemas."""
from datetime import datetime
from pydantic import BaseModel


class TradeResponse(BaseModel):
    id: int
    position_id: int | None
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usdt: float
    pnl_r: float
    exit_reason: str
    strategy_type: str
    created_at: datetime
    closed_at: datetime

    class Config:
        from_attributes = True


class TradeListResponse(BaseModel):
    trades: list[TradeResponse]
    total: int
    page: int
    pages: int


class TradeStatsResponse(BaseModel):
    total_trades: int
    win_rate: float
    avg_r: float
    best_trade: float
    worst_trade: float
