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


class RBucket(BaseModel):
    """R-distribution bucket for the Performance page histogram.

    The frontend colors a bucket red when its label starts with '0' or '-'.
    """

    bucket: str
    count: int


class StrategyResponse(BaseModel):
    # despite the name, this endpoint feeds the R:R distribution chart —
    # the frontend reads {bucket, count} pairs (PHASE5_STEP8)
    data: list[RBucket]
