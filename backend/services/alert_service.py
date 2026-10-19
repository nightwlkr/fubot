"""
Alert Service — central notification hub.

Pushes signals, trade events, and error states to both the console log and the
active WebSocket clients via the WS manager.

All methods are async: BotEngine, PositionManager, and DailyResetService await
them, and ws_manager.broadcast is a coroutine.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self) -> None:
        self._ws_manager: Optional[object] = None

    def register_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    async def _broadcast(self, payload: dict) -> None:
        """Deliver to the WS manager, tolerating sync or async broadcast()."""
        if self._ws_manager is None:
            return
        try:
            result = self._ws_manager.broadcast(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.exception("Failed to broadcast alert via WebSocket: %s", exc)

    async def signal(self, message: str, level: str = "INFO", symbol: Optional[str] = None) -> None:
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn("[ALERT] %s", message)
        await self._broadcast({"type": "alert", "level": level, "message": message, "symbol": symbol})

    # ---- Bot lifecycle -------------------------------------------------

    async def bot_started(self, mode: str) -> None:
        await self.signal(f"▶️ FUBOT started in {mode.upper()} mode", level="INFO")

    async def bot_stopped(self) -> None:
        await self.signal("⏹️ FUBOT stopped", level="INFO")

    async def error(self, message: str) -> None:
        await self.signal(message, level="ERROR")

    async def critical(self, message: str) -> None:
        await self.signal(message, level="CRITICAL")

    # ---- Trading events ------------------------------------------------

    async def trade_executed(self, symbol: str, direction: str, entry: float,
                             sl: float, tp: float, rr: float) -> None:
        await self.signal(
            f"🚀 Entry: {symbol} {direction} @ {entry:.4f} | SL {sl:.4f} | TP {tp:.4f} | R:R 1:{rr:.1f}",
            level="INFO", symbol=symbol,
        )

    async def trade(self, symbol: str, side: str, r: float, pnl_usdt: float) -> None:
        msg = f"{symbol} {side} | R: {r:+.2f} | PnL: ${pnl_usdt:,.2f}"
        await self.signal(msg, level="INFO" if r >= 0 else "WARNING", symbol=symbol)

    async def tp_hit(self, symbol: str, pnl_r: float, pnl_usdt: float) -> None:
        await self.signal(
            f"✅ TP hit {symbol} | R: {pnl_r:+.2f} | PnL: ${pnl_usdt:,.2f}",
            level="INFO", symbol=symbol,
        )

    async def sl_hit(self, symbol: str, pnl_r: float, pnl_usdt: float) -> None:
        await self.signal(
            f"❌ SL hit {symbol} | R: {pnl_r:+.2f} | PnL: ${pnl_usdt:,.2f}",
            level="WARNING", symbol=symbol,
        )

    async def be_trailing(self, symbol: str) -> None:
        await self.signal(f"💰 {symbol}: SL moved to break-even", level="INFO", symbol=symbol)

    # ---- Daily loss limit ----------------------------------------------

    async def daily_limit_warning(self, used_r: float, limit_r: float) -> None:
        await self.signal(
            f"⚠️ Daily limit warning: {used_r:.1f}R / {limit_r:.1f}R used", level="WARNING"
        )

    async def daily_limit_hit(self, used_r: float, limit_r: float) -> None:
        await self.signal(
            f"🚨 DAILY LOSS LIMIT HIT: {used_r:.1f}R / {limit_r:.1f}R. Bot stopped for the day.",
            level="CRITICAL",
        )

    # ---- Signals ---------------------------------------------------------

    async def new_signal(self, symbol: str, strategy_type: str, direction: str) -> None:
        await self.signal(
            f"📡 Signal: {symbol} {strategy_type} {direction}", level="INFO", symbol=symbol
        )


_alert_service: Optional[AlertService] = None


def get_alert_service() -> AlertService:
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
