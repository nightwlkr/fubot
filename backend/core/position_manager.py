"""Position manager — track open positions, BE trailing, TP/SL monitoring."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.position import Position
from backend.models.trade import Trade
from backend.models.signal import Signal
from backend.services.order_service import OrderService
from backend.services.binance_service import BinanceService
from backend.indicators.structural import analyze_structure
from backend.indicators.atr import calculate_atr
from backend.core.daily_reset import DailyResetService
from backend.services.alert_service import AlertService

logger = logging.getLogger(__name__)


class PositionManager:
    """Tracks open positions, monitors price, trails stops to BE at new BOS, closes on TP/SL."""

    def __init__(
        self,
        db: AsyncSession,
        binance: BinanceService,
        order_service: OrderService,
        daily_reset: DailyResetService,
        alert_service: AlertService,
        settings: dict,
    ):
        self.db = db
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
            except Exception as e:
                logger.error(f"PositionManager error: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _check_all_positions(self):
        """Check all open positions: price, BE trailing, TP/SL hits."""
        open_positions = await self._get_open_positions_from_db()

        for position in open_positions:
            symbol = position.symbol
            try:
                current_price = await self.binance.get_ticker(symbol)
                ticker = current_price.get("price", 0) if isinstance(current_price, dict) else 0
                current_price = float(ticker) if ticker else 0.0
            except Exception:
                continue

            # Check BE trailing
            await self._check_be_trailing(position, current_price)

            # Check if TP or SL was hit
            tp_hit, sl_hit = self._check_tp_sl_hit(position, current_price)
            if tp_hit:
                await self._close_position(position, "TP_HIT", current_price)
            elif sl_hit:
                await self._close_position(position, "SL_HIT", current_price)

    async def _get_open_positions_from_db(self) -> list[Position]:
        """Get all open positions from DB."""
        result = await self.db.execute(
            select(Position).where(Position.status == "OPEN")
        )
        return list(result.scalars().all())

    async def _check_be_trailing(self, position: Position, current_price: float):
        """
        If price has moved in favor enough to create a new BOS, move SL to BE.

        Rules:
        - Only move SL to BE once (entry price)
        - Trigger: new BOS in trade's favor on 5m timeframe
        """
        if position.side == "LONG":
            be_price = position.entry_price
            if current_price <= be_price:
                return  # Not yet in profit

            try:
                candles = await self.binance.get_klines(position.symbol, "5m", 100)
                if not candles:
                    return
                struct = analyze_structure(candles, position.symbol, "5m", calculate_atr(candles))

                if struct.last_bos and struct.last_bos.direction == "BULLISH":
                    if position.stop_loss is not None and float(position.stop_loss) < be_price:
                        await self._trail_stop_to_be(position, be_price)
            except Exception as e:
                logger.debug(f"BE trailing check failed for {position.symbol}: {e}")

        elif position.side == "SHORT":
            be_price = position.entry_price
            if current_price >= be_price:
                return

            try:
                candles = await self.binance.get_klines(position.symbol, "5m", 100)
                if not candles:
                    return
                struct = analyze_structure(candles, position.symbol, "5m", calculate_atr(candles))

                if struct.last_bos and struct.last_bos.direction == "BEARISH":
                    if position.stop_loss is not None and float(position.stop_loss) > be_price:
                        await self._trail_stop_to_be(position, be_price)
            except Exception as e:
                logger.debug(f"BE trailing check failed for {position.symbol}: {e}")

    async def _trail_stop_to_be(self, position: Position, new_stop: float):
        """Move stop loss to break-even (entry price)."""
        logger.info(f"[{position.symbol}] Trailing stop to BE: {position.stop_loss} → {new_stop}")
        position.stop_loss = new_stop
        await self.db.commit()

        if self.alerts:
            await self.alerts.be_trailing(position.symbol)

    def _check_tp_sl_hit(self, position: Position, current_price: float) -> tuple[bool, bool]:
        """Check if TP or SL was hit."""
        if position.side == "LONG":
            tp_hit = current_price >= float(position.take_profit) if position.take_profit else False
            sl_hit = current_price <= float(position.stop_loss) if position.stop_loss else False
        else:  # SHORT
            tp_hit = current_price <= float(position.take_profit) if position.take_profit else False
            sl_hit = current_price >= float(position.stop_loss) if position.stop_loss else False

        return tp_hit, sl_hit

    async def _close_position(self, position: Position, reason: str, exit_price: float):
        """Close position: record trade, update DB, log P&L."""
        entry = float(position.entry_price)
        qty = float(position.quantity)
        side = position.side

        # Calculate realized P&L
        if side == "LONG":
            pnl_usdt = (exit_price - entry) * qty
        else:
            pnl_usdt = (entry - exit_price) * qty

        # Calculate P&L in R units
        risk_distance = abs(entry - float(position.stop_loss)) if position.stop_loss else 0.01
        risk_amount = risk_distance * qty
        pnl_r = pnl_usdt / risk_amount if risk_amount > 0 else 0.0

        # Get strategy type from signal
        strategy_type = "UNKNOWN"
        if position.signal_id:
            sig_result = await self.db.execute(
                select(Signal).where(Signal.id == position.signal_id)
            )
            sig = sig_result.scalar_one_or_none()
            if sig:
                strategy_type = sig.strategy_type

        # Create Trade record
        trade = Trade(
            position_id=position.id,
            symbol=position.symbol,
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
        self.db.add(trade)

        # Update position
        position.status = "CLOSED"
        position.closed_at = datetime.now(timezone.utc)
        await self.db.commit()

        # Update daily summary
        await self.daily_reset.update_daily_summary(pnl_r, pnl_usdt)

        # Alert
        if self.alerts:
            if reason == "TP_HIT":
                await self.alerts.tp_hit(position.symbol, pnl_r, pnl_usdt)
            elif reason == "SL_HIT":
                await self.alerts.sl_hit(position.symbol, pnl_r, pnl_usdt)

        logger.info(f"[{position.symbol}] Position closed: {reason}, PnL: {pnl_usdt:.2f} USDT ({pnl_r:.2f}R)")
