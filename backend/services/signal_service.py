"""
Signal Service — orchestrates indicator modules and emits structured trading signals.

Wired by: BotEngine (scan loop) and the /signals API route.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    strategy_type: str
    direction: str
    entry_zone_min: Decimal
    entry_zone_max: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    rr_ratio: Decimal
    volume_confirmed: bool
    created_at: datetime = field(default_factory=datetime.utcnow)
    skip_reason: Optional[str] = None
    status: str = "PENDING"


class SignalService:
    def __init__(self) -> None:
        self.pending: list[Signal] = []

    async def scan_symbol(self, symbol: str, cents_only: bool = False) -> Optional[Signal]:
        """
        Run indicator modules against the latest market data for `symbol`.
        Returns a Signal when a setup is detected, or None.
        """
        logger.debug("Scanning %s for setups", symbol)
        return None

    def get_pending(self) -> list[Signal]:
        return list(self.pending)


_signal_service: Optional[SignalService] = None


def get_signal_service() -> SignalService:
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service
