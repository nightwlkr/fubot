"""Pydantic schemas for FUBOT API."""
from backend.schemas.auth import LoginRequest, LoginResponse, UserResponse
from backend.schemas.dashboard import (
    BotStatus,
    TodayStats,
    PositionItem,
    EquityPoint,
    DashboardOverview,
    TradeItem,
    SignalItem,
    SettingUpdate,
    TradingSettings,
    OrderItem,
    MonthlyReturn,
    RDistribution,
    WinRateByStrategy,
)
from backend.schemas.signal import SignalResponse, SignalListResponse, SignalCreateRequest
from backend.schemas.position import PositionResponse, PositionListResponse, PositionUpdateRequest
from backend.schemas.trade import TradeResponse, TradeListResponse, TradeStatsResponse
from backend.schemas.order import OrderResponse, OrderListResponse, OrderCancelResponse
from backend.schemas.settings import (
    TradingSettingsResponse,
    TradingSettingsUpdate,
    SymbolConfigResponse,
    SymbolListResponse,
    SymbolUpdateRequest,
    APIKeyStatusResponse,
    APIKeySaveRequest,
)
from backend.schemas.performance import EquityPoint as PerfEquityPoint, EquityResponse, MonthlyReturn as PerfMonthlyReturn, MonthlyResponse, StrategyPerformance, StrategyResponse
from backend.schemas.bot import BotStatusResponse, BotStartRequest

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "UserResponse",
    "BotStatus",
    "TodayStats",
    "PositionItem",
    "EquityPoint",
    "DashboardOverview",
    "TradeItem",
    "SignalItem",
    "SettingUpdate",
    "TradingSettings",
    "OrderItem",
    "MonthlyReturn",
    "RDistribution",
    "WinRateByStrategy",
    "SignalResponse",
    "SignalListResponse",
    "SignalCreateRequest",
    "PositionResponse",
    "PositionListResponse",
    "PositionUpdateRequest",
    "TradeResponse",
    "TradeListResponse",
    "TradeStatsResponse",
    "OrderResponse",
    "OrderListResponse",
    "OrderCancelResponse",
    "TradingSettingsResponse",
    "TradingSettingsUpdate",
    "SymbolConfigResponse",
    "SymbolListResponse",
    "SymbolUpdateRequest",
    "APIKeyStatusResponse",
    "APIKeySaveRequest",
    "PerfEquityPoint",
    "EquityResponse",
    "PerfMonthlyReturn",
    "MonthlyResponse",
    "StrategyPerformance",
    "StrategyResponse",
    "BotStatusResponse",
    "BotStartRequest",
]
