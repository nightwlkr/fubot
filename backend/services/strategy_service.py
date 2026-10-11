"""
Strategy Service — evaluates incoming signals and routes them to the Order Service.

Owns the decision layer: bias filter, FVG / 3-Candle / CHoCH validation, and final order creation.
"""
import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


class StrategyService:
    """
    Decision layer that turns a Signal into an actionable order plan with
    position size, stop, and take-profit derived from the risk service.
    """

    def __init__(self, risk_svc, order_svc) -> None:
        self.risk_svc = risk_svc
        self.order_svc = order_svc

    def evaluate(
        self,
        symbol: str,
        side: str,
        entry_zone_min: Decimal,
        entry_zone_max: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        rr_ratio: Decimal,
        volume_confirmed: bool,
        balance_usdt: Decimal,
        risk_percent: Decimal,
        min_rr: Decimal,
    ) -> Optional[dict]:
        """
        Validate and plan the entry.  Returns an order plan dict or None when the
        setup fails filters.
        """
        if not volume_confirmed:
            logger.info("Skipping %s: volume not confirmed", symbol)
            return None

        if not self.risk_svc.validate_rr_ratio(
            entry_zone_min, stop_loss, take_profit, min_rr
        ):
            logger.info("Skipping %s: R:R below minimum", symbol)
            return None

        size = self.risk_svc.calc_position_size(
            balance_usdt, risk_percent, entry_zone_min, stop_loss
        )
        if size == Decimal("0"):
            return None

        return {
            "symbol": symbol,
            "side": side,
            "entry_zone_min": entry_zone_min,
            "entry_zone_max": entry_zone_max,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "size": size,
            "rr_ratio": rr_ratio,
        }


_strategy_service: Optional[StrategyService] = None


def get_strategy_service() -> Optional[StrategyService]:
    global _strategy_service
    return _strategy_service


def set_strategy_service(svc: StrategyService) -> None:
    global _strategy_service
    _strategy_service = svc
