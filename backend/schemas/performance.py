"""Performance-related schemas."""
from pydantic import BaseModel


class EquityPoint(BaseModel):
    date: str
    balance: float


class EquityResponse(BaseModel):
    data: list[EquityPoint]


class MonthlyReturn(BaseModel):
    month: str
    pnl_r: float
    trades: int
    win_rate: float


class MonthlyResponse(BaseModel):
    data: list[MonthlyReturn]


class StrategyPerformance(BaseModel):
    strategy: str
    trades: int
    win_rate: float
    avg_r: float


class StrategyResponse(BaseModel):
    data: list[StrategyPerformance]
