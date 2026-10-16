"""Settings-related schemas."""
from pydantic import BaseModel


class TradingSettingsResponse(BaseModel):
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
    paper_use_real_balance: bool = True
    paper_balance_usdt: float = 10000.0
    trade_neutral_markets: bool = True
    trade_min_trend_strength: str = "MODERATE"
    symbol_cooldown_minutes: int = 240
    max_daily_trades: int = 12
    volume_filter_enabled: bool = False
    # Max position notional as a % of balance. This is a CAP, not a target: when
    # it binds it reduces the effective risk below risk_percent (with ~1% stops,
    # a 50% cap turns a 5% risk target into ~$1-2 per trade).
    max_position_notional_pct: float = 50.0
    # Move the stop to break-even once the trade is this many R in profit.
    be_trail_trigger_r: float = 0.5


class TradingSettingsUpdate(BaseModel):
    risk_percent: float | None = None
    max_positions: int | None = None
    daily_loss_limit_r: float | None = None
    rr_min: float | None = None
    check_interval_seconds: int | None = None
    strategy_fvg_enabled: bool | None = None
    strategy_3candle_enabled: bool | None = None
    strategy_choch_enabled: bool | None = None
    paper_use_real_balance: bool | None = None
    paper_balance_usdt: float | None = None
    trade_neutral_markets: bool | None = None
    trade_min_trend_strength: str | None = None
    symbol_cooldown_minutes: int | None = None
    max_daily_trades: int | None = None
    volume_filter_enabled: bool | None = None
    max_position_notional_pct: float | None = None
    be_trail_trigger_r: float | None = None


class SymbolConfigResponse(BaseModel):
    symbol: str
    enabled: bool
    max_leverage: int


class SymbolListResponse(BaseModel):
    symbols: list[SymbolConfigResponse]


class SymbolUpdateRequest(BaseModel):
    enabled: bool | None = None
    max_leverage: int | None = None


class APIKeyStatusResponse(BaseModel):
    has_key: bool
    masked_key: str | None = None


class APIKeySaveRequest(BaseModel):
    api_key: str
    api_secret: str
    # store as the testnet credential (default False = mainnet). Binance keys
    # are not interchangeable across environments.
    testnet: bool = False
