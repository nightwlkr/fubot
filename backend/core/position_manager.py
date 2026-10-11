"""Position manager — track open positions, BE trailing, TP/SL monitoring.

DB access: session factory + short-lived sessions. Positions are snapshotted
per monitoring pass; every mutation re-fetches the row in its own session so a
cancelled pass can never leave the shared state dirty.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.models.position import Position
from backend.models.trade import Trade
from backend.models.signal import Signal
from backend.services.binance_service import BinanceService
from backend.indicators.structural import analyze_structure
from backend.indicators.atr import calculate_atr
from backend.core.daily_reset import DailyResetService
from backend.core.websocket_manager import ws_manager
from backend.services.alert_service import AlertService

logger = logging.getLogger(__name__)


class PositionManager:
    """Tracks open positions, monitors price, trails stops to BE at new BOS, closes on TP/SL."""

    def __init__(
        self,
        binance: BinanceService,
        order_service,
        daily_reset: DailyResetService,
        alert_service: AlertService,
        settings: dict,
        session_factory: Optional[Callable] = None,
    ):
        self.session_factory = session_factory
        self.binance = binance
        self.orders = order_service
        self.daily_reset = daily_reset
        self.alerts = alert_service
        self.settings = settings
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the position manager loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        """Stop the position manager loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        """Main loop — runs every 30 seconds."""
        while self._running:
            try:
                await self._check_all_positions()
                await self._broadcast_pnl_update()
            except Exception as e:
                logger.error(f"PositionManager error: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _broadcast_pnl_update(self):
        """PHASE4_STEP2: pnl_update every pass (open positions + today's PnL)."""
        try:
            positions = await self._get_open_positions()
            stats = await self.daily_reset.get_daily_stats()
            await ws_manager.broadcast({"type": "pnl_update", "data": {
                "open_count": len(positions),
                "total_pnl_r": stats["total_pnl_r"],
                "total_pnl_usdt": stats["total_pnl_usdt"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }})
        except Exception as e:
            logger.debug(f"pnl_update broadcast failed: {e}")

    async def _get_open_positions(self) -> list[dict]:
        """Snapshot all open positions as plain dicts."""
        if self.session_factory is None:
            return []
        async with self.session_factory() as session:
            result = await session.execute(Position.__table__.select().where(Position.status == "OPEN"))
            rows = result.mappings().all()
            return [
                {
                    "id": r["id"],
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "entry_price": float(r["entry_price"]),
                    "quantity": float(r["quantity"]),
                    "stop_loss": float(r["stop_loss"]) if r["stop_loss"] is not None else None,
                    "take_profit": float(r["take_profit"]) if r["take_profit"] is not None else None,
                    "signal_id": r["signal_id"],
                }
                for r in rows
            ]

    async def _check_all_positions(self):
        """Check all open positions: price, BE trailing, TP/SL hits."""
        open_positions = await self._get_open_positions()

        for position in open_positions:
            symbol = position["symbol"]
            try:
                ticker = await self.binance.get_ticker(symbol)
                raw_price = ticker.get("price", 0) if isinstance(ticker, dict) else 0
                current_price = float(raw_price) if raw_price else 0.0
            except Exception:
                continue
            if current_price <= 0:
                continue

            # Check BE trailing
            await self._check_be_trailing(position, current_price)

            # Check if TP or SL was hit
            tp_hit, sl_hit = self._check_tp_sl_hit(position, current_price)
            if tp_hit:
                await self._close_position(position, "TP_HIT", current_price)
            elif sl_hit:
                await self._close_position(position, "SL_HIT", current_price)

    async def _check_be_trailing(self, position: dict, current_price: float):
        """
        If price has moved in favor enough to create a new BOS, move SL to BE.

        Rules:
        - Only move SL to BE once (to entry price, one-way)
        - Trigger: new BOS in trade's favor on 5m timeframe
        """
        be_price = position["entry_price"]

        if position["side"] == "LONG":
            if current_price <= be_price:
                return  # Not yet in profit
            if position["stop_loss"] is not None and position["stop_loss"] >= be_price:
                return  # Already at/beyond BE
            wanted_bos = "BULLISH"
        else:  # SHORT
            if current_price >= be_price:
                return
            if position["stop_loss"] is not None and position["stop_loss"] <= be_price:
                return
            wanted_bos = "BEARISH"

        try:
            candles = await self.binance.get_klines(position["symbol"], "5m", 100)
            if not candles:
                return
            struct = analyze_structure(candles, position["symbol"], "5m", calculate_atr(candles))

            if struct.last_bos and struct.last_bos.direction == wanted_bos:
                await self._trail_stop_to_be(position["id"], be_price)
        except Exception as e:
            logger.debug(f"BE trailing check failed for {position['symbol']}: {e}")

    async def _trail_stop_to_be(self, position_id: int, new_stop: float):
        """Move stop loss to break-even (entry price). One-way, idempotent."""
        if self.session_factory is None:
            return
        symbol = None
        async with self.session_factory() as session:
            position = await session.get(Position, position_id)
            if not position or position.status != "OPEN":
                return
            if position.side == "LONG" and position.stop_loss is not None and float(position.stop_loss) >= new_stop:
                return  # already at/beyond BE
            if position.side == "SHORT" and position.stop_loss is not None and float(position.stop_loss) <= new_stop:
                return
            old_stop = position.stop_loss
            position.stop_loss = new_stop
            symbol = position.symbol
            await session.commit()

        logger.info(f"[{symbol}] Trailing stop to BE: {old_stop} → {new_stop}")
        if self.alerts:
            await self.alerts.be_trailing(symbol)

    def _check_tp_sl_hit(self, position: dict, current_price: float) -> tuple[bool, bool]:
        """Check if TP or SL was hit."""
        if position["side"] == "LONG":
            tp_hit = current_price >= position["take_profit"] if position["take_profit"] else False
            sl_hit = current_price <= position["stop_loss"] if position["stop_loss"] else False
        else:  # SHORT
            tp_hit = current_price <= position["take_profit"] if position["take_profit"] else False
            sl_hit = current_price >= position["stop_loss"] if position["stop_loss"] else False

        return tp_hit, sl_hit

    async def _close_position(self, position: dict, reason: str, exit_price: float):
        """Close position: record trade, update DB, log P&L."""
        if self.session_factory is None:
            return

        async with self.session_factory() as session:
            row = await session.get(Position, position["id"])
            if not row or row.status != "OPEN":
                return  # already closed elsewhere

            entry = float(row.entry_price)
            qty = float(row.quantity)
            side = row.side

            # Calculate realized P&L
            if side == "LONG":
                pnl_usdt = (exit_price - entry) * qty
            else:
                pnl_usdt = (entry - exit_price) * qty

            # Calculate P&L in R units
            risk_distance = abs(entry - float(row.stop_loss)) if row.stop_loss else 0.01
            risk_amount = risk_distance * qty
            pnl_r = pnl_usdt / risk_amount if risk_amount > 0 else 0.0

            # Get strategy type from signal
            strategy_type = "UNKNOWN"
            if row.signal_id:
                sig = await session.get(Signal, row.signal_id)
                if sig:
                    strategy_type = sig.strategy_type

            trade = Trade(
                position_id=row.id,
                symbol=row.symbol,
                side=side,
                entry_price=entry,
                exit_price=exit_price,
                quantity=qty,
                pnl_usdt=pnl_usdt,
                pnl_r=pnl_r,
                exit_reason=reason,
                strategy_type=strategy_type,
                closed_at=datetime.now(timezone.utc),
            )
            session.add(trade)

            row.status = "CLOSED"
            row.closed_at = datetime.now(timezone.utc)
            await session.commit()
            symbol = row.symbol

        # Update daily summary + alert outside the session
        await self.daily_reset.update_daily_summary(pnl_r, pnl_usdt)
        await ws_manager.broadcast({"type": "position_closed", "data": {
            "position_id": position["id"], "symbol": position["symbol"],
            "pnl_r": pnl_r, "exit_reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat()}})

        if self.alerts:
            if reason == "TP_HIT":
                await self.alerts.tp_hit(symbol, pnl_r, pnl_usdt)
            elif reason == "SL_HIT":
                await self.alerts.sl_hit(symbol, pnl_r, pnl_usdt)

        logger.info(f"[{symbol}] Position closed: {reason}, PnL: {pnl_usdt:.2f} USDT ({pnl_r:.2f}R)")
