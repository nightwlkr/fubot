"""
Order Service — placement, tracking, and lifecycle management.

Wired by: StrategyService (route signals into orders), BotEngine (monitor fills).
"""
import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


class OrderService:
    """
    Responsible for turning a validated signal into a working Binance order
    (or a mocked order when MODE=paper).
    """

    def __init__(self) -> None:
        self.pending: dict[int, dict] = {}  # id -> order dict

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        *,
        simulate: bool = False,
    ) -> dict:
        """
        Create an order record.  In simulation mode the order is immediately
        marked as NEW and lives in self.pending so fill callbacks can resolve it.
        """
        order = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "status": "NEW",
            "filled_quantity": Decimal("0"),
            "simulate": simulate,
        }
        if simulate:
            order["status"] = "FILLED"
            order["filled_quantity"] = quantity
        self.pending[id(order)] = order
        logger.info("Order placed: %s %s %s %.8f @ %s simulate=%s",
                    symbol, side, order_type, quantity, price, simulate)
        return order

    async def get_order(self, order_id: int) -> Optional[dict]:
        """Return a tracked order by its id, or None if not found."""
        return self.pending.get(order_id)


_order_service: Optional[OrderService] = None


def get_order_service() -> OrderService:
    global _order_service
    if _order_service is None:
        _order_service = OrderService()
    return _order_service
