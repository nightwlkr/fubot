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


class TradingSettingsUpdate(BaseModel):
    risk_percent: float | None = None
    max_positions: int | None = None
    daily_loss_limit_r: float | None = None
    rr_min: float | None = None
    check_interval_seconds: int | None = None


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
