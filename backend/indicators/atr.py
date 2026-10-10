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
