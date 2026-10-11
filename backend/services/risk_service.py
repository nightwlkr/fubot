"""
Risk Service — position sizing, R:R validation, and daily loss enforcement.

Wired by: StrategyService (gate entry before order placement) and DailyReset (reset limits).
"""
import logging
from decimal import Decimal
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class RiskService:
    def __init__(self) -> None:
        self.daily_pnl_r_acc: Decimal = Decimal("0")
        self.daily_reset_time: Optional[datetime] = None

    def calc_position_size(
        self,
        balance_usdt: Decimal,
        risk_percent: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
    ) -> Decimal:
        """
        Size a position so that the $ loss at SL equals `risk_percent` of balance.

        position_value = (balance * risk%) / (entry - SL) * entry
        """
        risk_usd = balance_usdt * risk_percent / Decimal("100")
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff == Decimal("0"):
            logger.warning("stop_loss equals entry; using minimum size")
            return Decimal("0")
        qty = risk_usd / price_diff * entry_price
        return qty.quantize(Decimal("0.000001"))

    def validate_rr_ratio(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        min_rr: Decimal,
    ) -> bool:
        """True when the setup meets or exceeds the minimum R:R."""
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        if risk == Decimal("0"):
            return False
        return (reward / risk) >= min_rr

    def hit_daily_loss_limit(self, limit_r: Decimal) -> bool:
        """Return True once the rolling daily PnL in R crosses the limit."""
        return self.daily_pnl_r_acc <= limit_r

    def record_trade_r(self, pnl_r: Decimal) -> None:
        self.daily_pnl_r_acc += pnl_r

    def reset_daily(self) -> None:
        self.daily_pnl_r_acc = Decimal("0")


_risk_service: Optional[RiskService] = None


def get_risk_service() -> RiskService:
    global _risk_service
    if _risk_service is None:
        _risk_service = RiskService()
    return _risk_service
