"""Average True Range (ATR) indicator — Wilder's smoothed method."""
from typing import Optional


def calculate_atr(candles: list[dict], period: int = 14) -> Optional[float]:
    """
    Calculate Average True Range.

    True Range = max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )

    ATR = Wilder's smoothed True Range over `period` candles.

    Returns None if not enough data.
    """
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)

    # Wilder's smoothed ATR: first value is simple average, then smoothed
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period

    return float(atr)


def calculate_atr_series(candles: list[dict], period: int = 14) -> list[float]:
    """Return a per-index ATR series (lookahead-free).

    atr_series[i] is the ATR computed from candles[0..i] only — it never uses
    future bars. Index 0 is 0.0 (no true range for the first bar). Indices
    1..period are a running simple average (Wilder's seed value lands at
    `period`); indices after that are true Wilder smoothing. Callers should
    treat the first `period` values as warmup and skip indices where the
    value is not positive.
    """
    if not candles:
        return []
    if len(candles) < 2:
        return [0.0]

    trs = [0.0]  # no true range for the first bar
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    series = [0.0] * len(candles)
    atr = 0.0
    for i in range(1, len(candles)):
        if i <= period:
            # warmup: running simple average of true ranges up to i
            atr = sum(trs[1:i + 1]) / i
        else:
            atr = (atr * (period - 1) + trs[i]) / period
        series[i] = float(atr)
    return series
