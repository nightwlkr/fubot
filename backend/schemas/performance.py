"""Performance-related schemas."""
from pydantic import BaseModel


class EquityPoint(BaseModel):
    date: str
    balance: float


class EquityResponse(BaseModel):
    data: list[EquityPoint]


class PnLPoint(BaseModel):
    """One point on the cumulative trading-P&L curve.

    Unlike the equity curve (total account value incl. collateral drift), this
    is ONLY realized PnL from closed trades — the strategy's actual performance.
    """
    date: str
    pnl_r: float | None   # cumulative R; None = a prior trade had unknown R
    pnl_usdt: float   # cumulative USD up to this trade


class PnLResponse(BaseModel):
    data: list[PnLPoint]


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


class StrategyBreakdown(BaseModel):
    """One strategy's realised performance over a range.

    Separate from `StrategyResponse` because that one feeds the R-distribution
    histogram and its {bucket, count} shape is a frontend contract.

    `sum_r` and `total_pnl_usdt` are BOTH reported because they can disagree,
    and when they do the disagreement is the finding. At risk_percent=5 the
    notional cap bound on tight stops, so winners carried 1.70x the risk of
    losers and the book read +$17.02 while reporting -10.3R. Reading one column
    alone would have blamed the wrong strategy — 3-candle is the best performer
    in dollars and roughly break-even in R.
    """
    strategy: str
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: float          # percent, consistent with /trades/stats
    sum_r: float
    avg_r: float             # sum_r / total_trades — expectancy per trade
    profit_factor: float | None
    total_pnl_usdt: float

    # profit_factor is gross win / gross loss. It is NULL, not 0.0 and not a
    # sentinel, when there are no losses: "no losing trade" is undefined
    # arithmetic, and reporting 0.0 would rank a flawless strategy last.


class StrategyBreakdownResponse(BaseModel):
    # worst avg_r first — the strategies that need attention lead the table
    data: list[StrategyBreakdown]
