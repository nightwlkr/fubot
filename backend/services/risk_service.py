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
        Size a position so the $ loss at SL equals `risk_percent` of balance.

        qty = risk_usd / price_diff

        Realized loss at SL = qty * price_diff = risk_usd exactly. The previous
        formula multiplied by entry_price, which made the realized loss
        risk_usd * entry_price — silently oversizing low-priced coins by many
        times (e.g. LINKUSDT at ~13 → ~13x, producing -16.5R single losses).
        """
        risk_usd = balance_usdt * risk_percent / Decimal("100")
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff == Decimal("0"):
            logger.warning("stop_loss equals entry; using minimum size")
            return Decimal("0")
        qty = risk_usd / price_diff
        return qty.quantize(Decimal("0.000001"))

    def cap_position_notional(
        self,
        qty: Decimal,
        entry_price: Decimal,
        balance_usdt: Decimal,
        max_notional_fraction: Decimal = Decimal("0.5"),
    ) -> Decimal:
        """Cap position notional at a fraction of balance (safety backstop).

        Prevents any single position — however it was sized — from exceeding
        `max_notional_fraction` of the account. Notional = qty * entry.
        """
        if entry_price <= 0:
            return qty
        max_notional = balance_usdt * max_notional_fraction
        max_qty = (max_notional / entry_price).quantize(Decimal("0.000001"))
        if qty > max_qty:
            # StrategyService logs the symbol + resulting effective risk; keep
            # this generic note at debug so a capped position isn't announced
            # twice.
            logger.debug(
                f"position notional capped: qty {qty} -> {max_qty} "
                f"(notional {qty * entry_price} > {max_notional})"
            )
            return max_qty
        return qty

    def validate_rr_ratio(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        min_rr: Decimal,
        direction: str | None = None,
    ) -> bool:
        """True when the setup meets or exceeds the minimum R:R.

        With `direction` ("LONG"/"SHORT") the check is SIDE-AWARE: the stop must
        sit on the losing side and the target on the winning side of `entry`.
        The abs()-based fallback cannot see an inverted stop — a LONG whose stop
        is ABOVE the entry scored 93:1 (risk 30.5 on the wrong side, reward
        2859) and got executed, then Binance rejected the SL with 400 "would
        immediately trigger", leaving the filled entry unprotected (live
        2026-10-15, BTCUSDT/XRPUSDT).
        """
        if direction is None:
            risk = abs(entry - stop_loss)
            reward = abs(take_profit - entry)
            if risk == Decimal("0"):
                return False
            return (reward / risk) >= min_rr

        long_side = direction.upper() == "LONG"
        risk = (entry - stop_loss) if long_side else (stop_loss - entry)
        reward = (take_profit - entry) if long_side else (entry - take_profit)
        if risk <= Decimal("0") or reward <= Decimal("0"):
            return False  # stop and/or target on the wrong side of entry
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
