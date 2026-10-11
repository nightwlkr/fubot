"""
Backend services — business logic layer for FUBOT.

Each module owns a single responsibility:
  auth_service     : user authentication and JWT issuance
  binance_service  : REST API interaction with Binance
  binance_ws       : WebSocket client manager
  order_service    : order placement and tracking
  risk_service     : position sizing, R:R validation, daily-loss enforcement
  strategy_service : signal evaluation and order routing
  signal_service   : indicator orchestration and signal emission
  alert_service    : console and WebSocket notifications
"""
from backend.services.auth_service import (
    authenticate,
    create_access_token,
    get_current_user_from_cookie,
    RateLimitExceeded,
)
from backend.services.binance_service import BinanceService
from backend.services.binance_ws import BinanceWebSocketManager

from backend.services.order_service import OrderService, get_order_service
from backend.services.risk_service import RiskService, get_risk_service
from backend.services.strategy_service import (
    StrategyService,
    get_strategy_service,
    set_strategy_service,
)
from backend.services.signal_service import SignalService, get_signal_service
from backend.services.alert_service import AlertService, get_alert_service

__all__ = [
    "authenticate",
    "create_access_token",
    "get_current_user_from_cookie",
    "RateLimitExceeded",
    "BinanceService",
    "BinanceWebSocketManager",
    "OrderService",
    "get_order_service",
    "RiskService",
    "get_risk_service",
    "StrategyService",
    "get_strategy_service",
    "set_strategy_service",
    "SignalService",
    "get_signal_service",
    "AlertService",
    "get_alert_service",
]
