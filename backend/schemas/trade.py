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
    # Raw counts and the dollar total for the Trade Log's Total Wins / Total
    # Losses / Total P&L tiles. `win_rate` alone forces the operator to
    # multiply back to get the counts, and every R figure on the page is
    # normalised — with `max_position_notional_pct` capping most trades, 1R
    # varies from ~$2 to ~$11, so the dollar total is the only honest answer
    # to "how much did this make".
    wins: int
    losses: int
    # A breakeven is neither a win nor a loss. Reported separately because
    # wins + losses alone will not reconcile with total_trades whenever one
    # exists, and an unexplained shortfall reads as a broken page.
    breakevens: int
    total_pnl_usdt: float
