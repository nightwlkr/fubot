"""Donchian channel breakout detection — daily trend-following, PAPER ONLY.

Validated spec (scripts/donchian_backtest.py, 1d ensemble replay
2026-11): long-only is the deployable variant — shorts were negative in all
three windows. The live mapping is deliberately the SIMPLE spec, not the
ensemble: a close above the max high of the prior `lookback` bars is the
entry, the min low of the same window is the initial stop (widened to the
`min_risk_pct` floor at signal-build time, whichever is wider), and the
exit is a ratcheting trail at the min low of the prior `trail_lookback`
bars recomputed on each closed daily bar.

Everything here is lookahead-free: only CLOSED bars may be passed in (use
`closed_bars` to drop Binance's still-forming last kline), and every
channel is computed from bars STRICTLY BEFORE the evaluated bar.
"""
import time

# The 15 liquid USDT-M perps from scripts/donchian_backtest.py DEFAULT_SYMBOLS.
# The `donchian_symbols` setting overrides this list (comma-separated).
DEFAULT_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "LTCUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT",
    "SUIUSDT", "OPUSDT", "UNIUSDT",
)


def closed_bars(candles: list[dict], now_ms: int | None = None) -> list[dict]:
    """Drop the still-forming bar(s): Binance klines include the open bar,
    whose `close_time` is in the future. `close_time` is kept RAW (ms) by
    `BinanceService._parse_klines` — it is the anti-lookahead boundary.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return [c for c in candles if c.get("close_time", 0) < now]


def detect_donchian_breakout(candles: list[dict], lookback: int = 20,
                             trail_lookback: int = 10) -> dict | None:
    """Evaluate ONLY the most recent bar of `candles` (which must be closed).

    Long:  last close > max high of the `lookback` bars strictly before it.
    Short: last close < min low of the same window (detected for completeness;
           the deployable spec is long-only — see module docstring).

    Returns a pattern dict or None. `stop_loss` is the opposite channel
    extreme frozen at the signal bar (min low prior `lookback` for long,
    max high for short) — the min-risk floor is applied by the caller, not
    here, so this stays a pure price-level detector.
    """
    if len(candles) < lookback + 1:
        return None
    signal_bar = candles[-1]
    prior = candles[-lookback - 1:-1]  # `lookback` bars strictly before it
    channel_top = max(c["high"] for c in prior)
    channel_bottom = min(c["low"] for c in prior)
    close = signal_bar["close"]

    if close > channel_top:
        direction = "LONG"
        breakout_level = channel_top
        stop_loss = channel_bottom
    elif close < channel_bottom:
        direction = "SHORT"
        breakout_level = channel_bottom
        stop_loss = max(c["high"] for c in prior)
    else:
        return None

    return {
        "direction": direction,
        "entry_price": close,
        "breakout_level": breakout_level,
        "channel_top": channel_top,
        "channel_bottom": channel_bottom,
        "stop_loss": stop_loss,
        "lookback": lookback,
        "trail_lookback": trail_lookback,
        "formed_at": signal_bar.get("close_time"),
    }


def donchian_trailing_stop(candles: list[dict], side: str,
                           trail_lookback: int = 10) -> float | None:
    """Trailing stop level implied by the most recent CLOSED bar.

    The `trail_lookback` bars strictly before the last bar set the extreme:
    min low for LONG, max high for SHORT (mirrors the backtest's ratchet,
    which only ever tightens). Returns None when history is too short.
    """
    if len(candles) < trail_lookback + 1:
        return None
    prior = candles[-trail_lookback - 1:-1]
    if side == "LONG":
        return min(c["low"] for c in prior)
    return max(c["high"] for c in prior)
