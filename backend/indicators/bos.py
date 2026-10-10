"""Break of Structure (BOS) detection."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from backend.indicators.pivots import PivotPoint, find_pivot_highs_lows


@dataclass
class BOS:
    """A Break of Structure — trend confirmation."""
    symbol: str
    direction: str           # 'BULLISH' or 'BEARISH'
    interval: str
    broken_level: float       # The high/low that was broken
    confirmation_price: float # Price that confirmed the BOS
    formed_at: datetime
    index: int


def detect_bos(
    candles: list[dict],
    symbol: str = "",
    interval: str = "5m",
    left_bars: int = 5,
    right_bars: int = 5,
) -> List[BOS]:
    """
    Detect Break of Structure.

    Bullish BOS: Price breaks above a previous pivot high,
                 confirming the uptrend is still intact.
    Bearish BOS: Price breaks below a previous pivot low,
                 confirming the downtrend is still intact.
    """
    highs, lows = find_pivot_highs_lows(candles, left_bars, right_bars)
    patterns = []

    # Bullish BOS: price breaks above previous pivot highs
    for i, high in enumerate(highs[:-1]):
        next_high = highs[i + 1]
        # Check if any candle after the pivot high broke above it
        for j in range(next_high.index, len(candles)):
            if candles[j]["high"] > high.price:
                patterns.append(BOS(
                    symbol=symbol or "",
                    direction="BULLISH",
                    interval=interval,
                    broken_level=high.price,
                    confirmation_price=candles[j]["high"],
                    formed_at=candles[j].get("close_time", datetime.now()),
                    index=j,
                ))
                break

    # Bearish BOS: price breaks below previous pivot lows
    for i, low in enumerate(lows[:-1]):
        next_low = lows[i + 1]
        for j in range(next_low.index, len(candles)):
            if candles[j]["low"] < low.price:
                patterns.append(BOS(
                    symbol=symbol or "",
                    direction="BEARISH",
                    interval=interval,
                    broken_level=low.price,
                    confirmation_price=candles[j]["low"],
                    formed_at=candles[j].get("close_time", datetime.now()),
                    index=j,
                ))
                break

    return patterns
