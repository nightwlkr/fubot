"""Alert service — console logging + WebSocket push to dashboard."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class AlertLevel(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    level: AlertLevel
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: Optional[str] = None
    metadata: Optional[dict] = None


class AlertService:
    """Sends alerts via console and (optionally) WebSocket broadcast."""

    def __init__(self, ws_broadcast_fn=None):
        """
        ws_broadcast_fn: Optional callable that takes a dict message.
        If provided, alerts will be pushed to connected WebSocket clients.
        """
        self._ws = ws_broadcast_fn

    async def send(
        self,
        level: str,
        message: str,
        symbol: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Alert:
        """Send an alert via all configured channels."""
        alert = Alert(
            level=AlertLevel(level),
            message=message,
            symbol=symbol,
            metadata=metadata,
        )

        # 1. Log to console with level-based formatting
        self._log_to_console(alert)

        # 2. Push to WebSocket dashboard
        if self._ws:
            await self._ws({
                "type": "alert",
                "data": {
                    "level": alert.level.value,
                    "message": alert.message,
                    "symbol": alert.symbol,
                    "timestamp": alert.timestamp.isoformat(),
                    "metadata": alert.metadata,
                },
            })

        return alert

    def _log_to_console(self, alert: Alert) -> None:
        """Format and print alert to console."""
        symbols = f"[{alert.symbol}] " if alert.symbol else ""
        level_tags = {
            "info": "",
            "warn": "⚠️ ",
            "error": "❌ ",
            "critical": "🚨 ",
        }
        tag = level_tags.get(alert.level.value, "")
        ts = alert.timestamp.strftime("%H:%M:%S")
        print(f"{tag}{ts} {symbols}{alert.message}")

    async def trade_executed(self, symbol: str, direction: str, entry: float, sl: float, tp: float, rr: float):
        await self.send(
            "info",
            f"🚀 {direction} {symbol} @ {entry:.2f}, SL: {sl:.2f}, TP: {tp:.2f}, R:R: {rr:.1f}",
            symbol=symbol,
        )

    async def sl_hit(self, symbol: str, pnl_r: float, pnl_usdt: float):
        await self.send(
            "warn",
            f"❌ SL hit on {symbol}: PnL {pnl_r:.2f}R (${pnl_usdt:.2f})",
            symbol=symbol,
        )

    async def tp_hit(self, symbol: str, pnl_r: float, pnl_usdt: float):
        await self.send(
            "info",
            f"✅ TP hit on {symbol}: PnL {pnl_r:.2f}R (${pnl_usdt:.2f})",
            symbol=symbol,
        )

    async def be_trailing(self, symbol: str):
        await self.send(
            "info",
            f"💰 {symbol} SL moved to BE",
            symbol=symbol,
        )

    async def daily_limit_warning(self, used_r: float, limit_r: float):
        remaining = limit_r - used_r
        await self.send(
            "warn",
            f"⚠️ Daily limit warning: {used_r:.1f}R of {limit_r}R used ({remaining:.1f}R remaining)",
        )

    async def daily_limit_hit(self, used_r: float, limit_r: float):
        await self.send(
            "critical",
            f"🚨 DAILY LOSS LIMIT HIT: {used_r:.1f}R / {limit_r:.1f}R. Bot stopped for the day.",
        )

    async def bot_started(self, mode: str):
        await self.send("info", f"▶️ FUBOT started in {mode.upper()} mode")

    async def bot_stopped(self):
        await self.send("info", "⏹️ FUBOT stopped")

    async def signal_detected(self, symbol: str, direction: str, strategy_type: str, rr: float):
        await self.send(
            "info",
            f"📡 Signal: {direction} {symbol} ({strategy_type}), R:R: {rr:.1f}",
            symbol=symbol,
        )

    async def error(self, message: str):
        await self.send("error", f"❌ {message}")
