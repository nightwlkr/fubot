"""Settings-related schemas."""
from pydantic import BaseModel, Field


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
    # OB+FVG: a 1h Fair Value Gap only trades when a confirmed, same-direction
    # Order Block on 5m overlaps its zone. Measured +0.315R/trade with the live
    # trend gate (20 symbols, R:R 3.0). max_age_bars bounds how old a gap may be
    # — only the last N closed 1h bars are eligible, matching how the backtest
    # entered each gap at formation.
    strategy_obfvg_enabled: bool = True
    strategy_obfvg_max_age_bars: int = 2
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
    # Profit lock: at this many R in profit, start trailing the stop to lock in gains.
    profit_lock_trigger_r: float = 1.0
    # How far behind price (in R) the profit-lock trail sits.
    profit_lock_trail_r: float = 0.5
    # Close stale profitable trades after this many hours (0 = disabled).
    stale_trade_hours: float = 24.0


class TradingSettingsUpdate(BaseModel):
    """Bounds mirror backend/config.py.

    These bounds MUST match config.py's Field(ge=/le=) values. Before this,
    every field was a bare `float | None` and PUT /settings/trading wrote them
    straight to the DB and hot-reloaded them into the running engine — so
    `daily_loss_limit_r=9999` switched OFF the -3R hard stop, and
    `check_interval_seconds=0` made the scan loop spin with no delay (a
    self-inflicted rate-limit ban). config.py's bounds were never consulted on
    that path.

    Validate on WRITE. Clamping the response instead only sanitises what the
    operator sees while the engine keeps running the unbounded value.
    """
    risk_percent: float | None = Field(default=None, ge=0.5, le=2.0)
    max_positions: int | None = Field(default=None, ge=1, le=10)
    daily_loss_limit_r: float | None = Field(default=None, ge=-50.0, le=-0.5)
    rr_min: float | None = Field(default=None, ge=2.0, le=5.0)
    check_interval_seconds: int | None = Field(default=None, ge=10, le=300)
    strategy_fvg_enabled: bool | None = None
    strategy_3candle_enabled: bool | None = None
    strategy_choch_enabled: bool | None = None
    strategy_obfvg_enabled: bool | None = None
    strategy_obfvg_max_age_bars: int | None = Field(default=None, ge=1, le=48)
    paper_use_real_balance: bool | None = None
    paper_balance_usdt: float | None = Field(default=None, gt=0, le=10_000_000)
    trade_neutral_markets: bool | None = None
    trade_min_trend_strength: str | None = None
    symbol_cooldown_minutes: int | None = Field(default=None, ge=0, le=10080)
    max_daily_trades: int | None = Field(default=None, ge=0, le=500)
    volume_filter_enabled: bool | None = None
    # Negative values here produced a negative cap -> negative qty -> a paper
    # Position row with quantity=-5 (the zero guard does not catch negatives).
    max_position_notional_pct: float | None = Field(default=None, ge=0, le=1000)
    be_trail_trigger_r: float | None = Field(default=None, ge=0, le=10)
    profit_lock_trigger_r: float | None = Field(default=None, ge=0, le=20)
    profit_lock_trail_r: float | None = Field(default=None, ge=0, le=20)
    stale_trade_hours: float | None = Field(default=None, ge=0, le=720)


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
