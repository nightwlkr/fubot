"""
Order Service — placement, tracking, and lifecycle management.

Turns a validated signal into a working Binance order (or a simulated fill in
paper mode). Live orders are logged to the `orders` table; simulated fills stay
in memory so the paper trail never looks like real execution history.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, binance=None, session_factory=None) -> None:
        self.binance = binance
        self.session_factory = session_factory
        self.pending: dict[int, dict] = {}  # id -> order dict

    async def get_current_position(self, symbol: str) -> Optional[dict]:
        """Open position on `symbol` from Binance, or None when flat/unknown."""
        if self.binance is None:
            return None
        try:
            pos = await self.binance.get_position(symbol)
        except Exception as exc:
            logger.debug("get_current_position(%s) failed: %s", symbol, exc)
            return None
        return pos or None

    async def get_all_open_positions(self) -> dict[str, dict]:
        """Every open position on the account, keyed by symbol.

        Uses the single positionRisk call that returns ALL positions instead of
        one call per symbol — the scan loop runs this once per pass over all
        enabled symbols, which is the difference between ~1 and ~24 signed
        requests per scan. Binance rate-limits the IP (2400/min, -1003 → HTTP
        400/429) and the per-symbol loop was tripping it every pass.
        """
        if self.binance is None:
            return {}
        try:
            positions = await self.binance.get_all_positions()
        except Exception as exc:
            logger.debug("get_all_open_positions failed: %s", exc)
            return {}
        return {
            p["symbol"]: p for p in positions
            if p.get("symbol") and p.get("quantity", 0) > 0
        }

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity,
        price=None,
        stop_loss=None,
        take_profit=None,
        *,
        simulate: bool = False,
        binance_order_id: Optional[int] = None,
        position_id: Optional[int] = None,
    ) -> dict:
        """
        Create an order record. In simulation mode the order is immediately
        marked FILLED. Live orders are additionally logged to the `orders` table.
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
            "filled_quantity": 0 if not simulate else quantity,
            "simulate": simulate,
            "binance_order_id": binance_order_id,
            "position_id": position_id,
        }
        if simulate:
            order["status"] = "FILLED"
        self.pending[id(order)] = order
        logger.info("Order placed: %s %s %s %s @ %s simulate=%s",
                    symbol, side, order_type, quantity, price, simulate)
        if not simulate:
            await self._log_order(order)
        return order

    async def _log_order(self, order: dict) -> None:
        """Persist a live order to the `orders` table."""
        if self.session_factory is None:
            return
        try:
            from backend.models.order import Order as OrderModel
            async with self.session_factory() as session:
                session.add(OrderModel(
                    binance_order_id=order.get("binance_order_id"),
                    position_id=order.get("position_id"),
                    symbol=order["symbol"],
                    side=order["side"],
                    order_type=order["type"],
                    price=order["price"],
                    quantity=float(order["quantity"]),
                    filled_qty=float(order["filled_quantity"]),
                    status=order["status"],
                ))
                await session.commit()
        except Exception:
            logger.exception("Failed to log order for %s", order["symbol"])

    async def get_order(self, order_id: int) -> Optional[dict]:
        """Return a tracked order by its id, or None if not found."""
        return self.pending.get(order_id)


_order_service: Optional[OrderService] = None


def get_order_service() -> OrderService:
    """Bare service — the wired instance (binance + session_factory) comes from main.py."""
    global _order_service
    if _order_service is None:
        _order_service = OrderService()
    return _order_service
