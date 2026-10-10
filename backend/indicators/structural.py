"""Combined structural analysis — CHoCH + BOS + trend detection."""
from dataclasses import dataclass
from datetime import datetime

from backend.indicators.choch import detect_choch
from backend.indicators.bos import detect_bos
from backend.indicators.pivots import find_pivot_highs_lows


@dataclass
class StructuralAnalysis:
    """Full structural analysis of market structure."""
    trend: str               # 'UPTREND', 'DOWNTREND', 'RANGING'
    last_choch: object | None  # CHoCH | None
    last_bos: object | None    # BOS | None
    current_swing_high: float
    current_swing_low: float
    pivot_highs_count: int
    pivot_lows_count: int
    timestamp: datetime


def analyze_structure(
    candles: list[dict],
    symbol: str = "",
    interval: str = "5m",
    atr: float = 0.0,
    rr_min: float = 3.0,
    left_bars: int = 5,
    right_bars: int = 5,
) -> StructuralAnalysis:
    """
    Full structural analysis combining CHoCH + BOS + trend detection.

    Trend determination:
    - More recent highs > older highs + more recent lows > older lows → UPTREND
    - More recent highs < older highs + more recent lows < older lows → DOWNTREND
    - Otherwise → RANGING
    """
    highs, lows = find_pivot_highs_lows(candles, left_bars, right_bars)
    choch_patterns = detect_choch(candles, symbol, interval, atr, rr_min, left_bars, right_bars)
    bos_patterns = detect_bos(candles, symbol, interval, left_bars, right_bars)

    last_choch = choch_patterns[-1] if choch_patterns else None
    last_bos = bos_patterns[-1] if bos_patterns else None

    # Determine trend from pivot structure
    if len(highs) >= 2 and len(lows) >= 2:
        recent_high = highs[-1]
        prev_high = highs[-2]
        recent_low = lows[-1]
        prev_low = lows[-2]

        higher_highs = recent_high.price > prev_high.price
        higher_lows = recent_low.price > prev_low.price

        if higher_highs and higher_lows:
            trend = "UPTREND"
        elif not higher_highs and not higher_lows:
            trend = "DOWNTREND"
        else:
            trend = "RANGING"
    else:
        trend = "RANGING"

    # Current swing extremes
    current_swing_high = highs[-1].price if highs else 0.0
    current_swing_low = lows[-1].price if lows else 0.0

    return StructuralAnalysis(
        trend=trend,
        last_choch=last_choch,
        last_bos=last_bos,
        current_swing_high=current_swing_high,
        current_swing_low=current_swing_low,
        pivot_highs_count=len(highs),
        pivot_lows_count=len(lows),
        timestamp=datetime.now(),
    )
