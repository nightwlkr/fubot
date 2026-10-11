"""
Alert Service — central notification hub.

Pushes signals, trade events, and error states to both the console log and the
active WebSocket clients via the WS manager.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self) -> None:
        self._ws_manager: Optional[object] = None

    def register_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    def signal(self, message: str, level: str = "INFO") -> None:
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn("[ALERT] %s", message)
        if self._ws_manager is not None:
            try:
                self._ws_manager.broadcast({"type": "alert", "level": level, "message": message})
            except Exception as exc:
                logger.exception("Failed to broadcast alert via WebSocket: %s", exc)

    def trade(self, symbol: str, side: str, r: float, pnl_usdt: float) -> None:
        msg = f"{symbol} {side} | R: {r:+.2f} | PnL: ${pnl_usdt:,.2f}"
        self.signal(msg, level="INFO" if r >= 0 else "WARNING")


_alert_service: Optional[AlertService] = None


def get_alert_service() -> AlertService:
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
