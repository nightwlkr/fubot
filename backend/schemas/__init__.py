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
]
