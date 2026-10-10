"""Order service — Binance order placement, modification, cancellation."""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.order import Order
from backend.services.binance_service import BinanceService, BinanceAPIError


@dataclass
class OrderResult:
    success: bool
    binance_order_id: Optional[int] = None
    status: Optional[str] = None
    filled_qty: Optional[float] = None
    avg_fill_price: Optional[float] = None
    error: Optional[str] = None


class OrderService:
    """Service for placing and managing Binance Futures orders."""

    def __init__(self, db: AsyncSession, binance: BinanceService):
        self.db = db
        self.binance = binance

    async def place_entry_order(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        signal_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a LIMIT entry order. LONG → BUY, SHORT → SELL."""
        try:
            binance_side = "BUY" if side == "LONG" else "SELL"
            result = await self.binance.place_limit_order(symbol, binance_side, entry_price, quantity)

            order = Order(
                binance_order_id=result.get("orderId"),
                symbol=symbol,
                side=side,
                order_type="LIMIT",
                price=entry_price,
                quantity=quantity,
                filled_qty=0,
                status="NEW",
            )
            self.db.add(order)
            await self.db.commit()
            await self.db.refresh(order)

            return OrderResult(
                success=True,
                binance_order_id=result.get("orderId"),
                status="NEW",
                filled_qty=0,
            )
        except BinanceAPIError as e:
            return OrderResult(success=False, error=f"Binance error {e.code}: {e.message}")
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float,
    ) -> OrderResult:
        """Place a STOP_MARKET order as stop loss."""
        try:
            close_side = "SELL" if side == "LONG" else "BUY"
            result = await self.binance.modify_position_stop(symbol, side, stop_price, quantity)

            order = Order(
                binance_order_id=result.get("orderId"),
                symbol=symbol,
                side=side,
                order_type="STOP_MARKET",
                price=stop_price,
                quantity=quantity,
                filled_qty=0,
                status="NEW",
            )
            self.db.add(order)
            await self.db.commit()
            await self.db.refresh(order)

            return OrderResult(
                success=True,
                binance_order_id=result.get("orderId"),
                status="NEW",
            )
        except BinanceAPIError as e:
            return OrderResult(success=False, error=f"Binance error {e.code}: {e.message}")
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        tp_price: float,
        quantity: float,
    ) -> OrderResult:
        """Place a TAKE_PROFIT_MARKET order."""
        # Same mechanics as stop loss but with tp_price
        return await self.place_stop_loss(symbol, side, tp_price, quantity)

    async def cancel_order(self, symbol: str, order_id: int) -> OrderResult:
        """Cancel an open (unfilled) order."""
        try:
            result = await self.binance.cancel_order(symbol, order_id)

            # Update local order status
            result_db = await self.db.execute(
                __import__("sqlalchemy").select(Order).where(Order.binance_order_id == order_id)
            )
            order = result_db.scalar_one_or_none()
            if order:
                order.status = "CANCELLED"
                await self.db.commit()

            return OrderResult(
                success=True,
                binance_order_id=order_id,
                status="CANCELLED",
            )
        except BinanceAPIError as e:
            return OrderResult(success=False, error=f"Binance error {e.code}: {e.message}")
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def get_order_status(self, symbol: str, order_id: int) -> OrderResult:
        """Query Binance for current order status."""
        try:
            result = await self.binance.get_order(symbol, order_id)
            return OrderResult(
                success=True,
                binance_order_id=order_id,
                status=result.get("status", "UNKNOWN"),
                filled_qty=float(result.get("executedQty", 0)),
                avg_fill_price=float(result.get("price", 0)) if result.get("price") else None,
            )
        except BinanceAPIError as e:
            return OrderResult(success=False, error=f"Binance error {e.code}: {e.message}")
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """Get all open orders for symbol from Binance."""
        try:
            return await self.binance.get_open_orders(symbol)
        except BinanceAPIError as e:
            return []
        except Exception:
            return []

    async def get_current_position(self, symbol: str) -> dict | None:
        """Get open position from Binance for symbol."""
        try:
            pos = await self.binance.get_position(symbol)
            if not pos:
                return None
            return pos
        except BinanceAPIError:
            return None
        except Exception:
            return None

    async def get_all_positions(self) -> list[dict]:
        """Get all open positions from Binance."""
        try:
            return await self.binance.get_all_positions()
        except BinanceAPIError:
            return []
        except Exception:
            return []

    async def set_symbol_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for symbol on Binance."""
        try:
            await self.binance.set_leverage(symbol, leverage)
            return True
        except BinanceAPIError:
            return False
        except Exception:
            return False
