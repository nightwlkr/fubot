"""Pydantic schemas for the FUBOT API."""
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, Any


# --- Auth ---
class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: int
    username: str
    role: str

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


# --- Dashboard ---
class BotStatus(BaseModel):
    # matches frontend BotStatus (store/dashboardStore.ts): status/scan_count/last_scan
    status: str = "stopped"  # "running" | "stopped"
    is_running: bool = False  # kept for backward compatibility
    mode: str = "paper"  # "paper" or "live"
    uptime_seconds: float = 0.0
    scan_count: int = 0
    last_scan: Optional[str] = None


class DailyLimitStatus(BaseModel):
    used_r: float
    limit_r: float
    remaining_r: float


class DailyTradesLimitStatus(BaseModel):
    """Status for max_daily_trades cap."""
    used_trades: int
    limit_trades: int
    remaining_trades: int


class WeeklyLimitStatus(BaseModel):
    """Status for weekly drawdown guard."""
    used_r: float
    limit_r: float
    remaining_r: float
    is_paused: bool  # True if weekly guard triggered


class TodayStats(BaseModel):
    # matches frontend PnLCard: pnl_r/pnl_usdt/wins/losses/trades_count/daily_limit_status
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    win_rate: float = 0.0
    trades_today: int = 0
    pnl_r: float = 0.0
    pnl_usdt: float = 0.0
    wins: int = 0
    losses: int = 0
    trades_count: int = 0
    available_balance_usdt: float = 0.0  # for USD approximations in UI
    daily_limit_status: Optional[DailyLimitStatus] = None
    daily_trades_limit_status: Optional[DailyTradesLimitStatus] = None
    weekly_limit_status: Optional[WeeklyLimitStatus] = None


class PositionItem(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: str
    opened_at: datetime
    mark_price: float = 0.0        # mark price (Binance premiumIndex in paper, positionRisk in live)
    unrealized_pnl: float = 0.0    # USDT — exchange-truth in live mode, computed in paper
    unrealized_pnl_pct: float = 0.0  # ROI % vs initial margin (Binance's definition)
    liquidation_price: float = 0.0  # price at which the position is liquidated
    margin_usd: float = 0.0         # initial margin (collateral at risk) in USD

    class Config:
        from_attributes = True


class EquityPoint(BaseModel):
    date: str
    balance: float          # frontend Overview equity chart reads `balance`
    balance_usdt: float     # kept for backward compatibility


class DashboardOverview(BaseModel):
    bot_status: BotStatus
    today_stats: TodayStats
    open_positions: list[PositionItem]
    equity_curve: list[EquityPoint]
    total_trades: int = 0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0


# --- Trade ---
class TradeItem(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usdt: float
    pnl_r: float | None = None   # None = unknown 1R, not breakeven
    exit_reason: str
    strategy_type: str
    closed_at: datetime

    class Config:
        from_attributes = True


# --- Signal ---
class SignalItem(BaseModel):
    id: int
    symbol: str
    strategy_type: str
    direction: str
    entry_zone_min: float
    entry_zone_max: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    volume_confirmed: bool
    status: str
    created_at: datetime
    skip_reason: Optional[str] = None

    class Config:
        from_attributes = True


# --- Settings ---
class SettingUpdate(BaseModel):
    key: str
    value: Any


class TradingSettings(BaseModel):
    risk_percent: float = 1.0
    max_positions: int = 3
    daily_loss_limit_r: float = -3.0
    rr_min: float = 3.0
    check_interval_seconds: int = 60
    leverage_btc: int = 10
    leverage_alts: int = 5
    strategy_fvg_enabled: bool = True
    strategy_3candle_enabled: bool = True
    strategy_choch_enabled: bool = True
    strategy_obfvg_enabled: bool = True
    strategy_obfvg_max_age_bars: int = 2


# --- Order ---
class OrderItem(BaseModel):
    id: int
    binance_order_id: Optional[int] = None
    symbol: str
    side: str
    order_type: str
    price: Optional[float] = None
    quantity: float
    filled_qty: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# --- Performance ---
class MonthlyReturn(BaseModel):
    month: str
    return_pct: float
    pnl_usdt: float


class RDistribution(BaseModel):
    bin_label: str
    count: int


class WinRateByStrategy(BaseModel):
    strategy: str
    win_rate: float
    total_trades: int
    avg_r: float
