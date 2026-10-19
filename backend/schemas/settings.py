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
    strategy_fvg_enabled: bool = False
    strategy_3candle_enabled: bool = True
    strategy_choch_enabled: bool = False
    # OB+FVG: DISABLED — live data shows -0.34R/trade (15 trades, 33% WR).
    # Panel analysis (+0.315R) was based on 5-trade sample — insufficient.
    strategy_obfvg_enabled: bool = False
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
    max_position_notional_pct: float = 200.0
    # Move the stop to break-even once the trade is this many R in profit.
    be_trail_trigger_r: float = 0.5
    # Profit lock: at this many R in profit, start trailing the stop to lock in gains.
    profit_lock_trigger_r: float = 1.0
    # How far behind price (in R) the profit-lock trail sits.
    profit_lock_trail_r: float = 0.5
    # Close stale profitable trades after this many hours (0 = disabled).
    stale_trade_hours: float = 24.0
    # Funding rate filter: skip entries when funding rate drops below threshold.
    # Negative funding = shorts pay longs (bearish sentiment, possible bounce).
    # Skip if funding < threshold (e.g., -0.0001 = -0.01%).
    funding_rate_filter_enabled: bool = True
    funding_skip_threshold: float = -0.0001  # Skip if funding < -0.01%
    # ATR multiplier for stop distance (research: 1.5×ATR optimal for crypto).
    atr_stop_mult: float = 1.5
    # Weekly drawdown guard — pauses trading when this many R in losses
    # accumulate in a UTC trading day. Default 5.0R; raise to ride out volatile
    # weeks, lower to tighten the cap.
    weekly_drawdown_limit_r: float = 5.0


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

    NOTE on risk_percent: config.py still says le=2.0, which is STALE — the
    account has been traded at 5% risk/trade since 2026-10-15 (paired with a
    300% notional cap, because honouring a risk target needs
    cap >= risk_percent / stop_pct). Bounds copied verbatim from config.py
    would have 422'd the operator's own live setting on the next save.
    """
    risk_percent: float | None = Field(default=None, ge=0.1, le=10.0)
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
    # Funding rate filter settings
    funding_rate_filter_enabled: bool | None = None
    funding_skip_threshold: float | None = Field(default=None, ge=-0.001, le=0.001)
    # ATR multiplier for stop distance
    atr_stop_mult: float | None = Field(default=None, ge=0.5, le=5.0)
    # Weekly drawdown guard limit in R
    weekly_drawdown_limit_r: float | None = Field(default=None, ge=1.0, le=20.0)


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
