"""Change of Character (CHoCH) detection."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from backend.indicators.pivots import PivotPoint, find_pivot_highs_lows


@dataclass
class CHoCH:
    """A Change of Character — structural trend shift signal."""
    symbol: str
    direction: str           # 'BULLISH' or 'BEARISH'
    interval: str
    trigger_price: float      # Price level that was broken
    pivot_price: float        # The pivot point that was broken
    formed_at: datetime
    index: int
    pullback_entry_zone: Tuple[float, float]
    stop_loss: float
    take_profit: float
    rr_ratio: Optional[float]
    is_valid: bool


def calculate_fib_entry(impulse_low: float, impulse_high: float) -> Tuple[float, float]:
    """Calculate 50% and 61.8% Fibonacci retracement levels."""
    impulse_range = abs(impulse_high - impulse_low)
    fib_50 = impulse_low + impulse_range * 0.5
    fib_618 = impulse_low + impulse_range * 0.618
    return fib_50, fib_618


def detect_choch(
    candles: list[dict],
    symbol: str = "",
    interval: str = "5m",
    atr: float = 0.0,
    rr_min: float = 3.0,
    left_bars: int = 5,
    right_bars: int = 5,
) -> list[CHoCH]:
    """
    Detect CHoCH patterns.

    Bullish CHoCH (downtrend → uptrend):
      - Last two pivot lows: L1 (older), L2 (recent)
      - Last two pivot highs: H1 (older), H2 (recent)
      - Market in bearish structure: H2 < H1 (lower highs)
      - L2 breaks above H1 → bullish CHoCH

    Bearish CHoCH (uptrend → downtrend):
      - Market in bullish structure: L2 > L1 (higher lows)
      - H2 breaks below L1 → bearish CHoCH
    """
    highs, lows = find_pivot_highs_lows(candles, left_bars, right_bars)
    patterns = []

    if len(highs) < 2 or len(lows) < 2:
        return patterns

    # Get recent pivots (last 4 of each)
    recent_highs = highs[-2:]
    recent_lows = lows[-2:]

    # Bullish CHoCH: recent low breaks above older high
    if len(recent_lows) >= 2 and len(recent_highs) >= 2:
        l_recent = recent_lows[-1]  # Most recent low
        l_prev = recent_lows[-2]    # Previous low
        h_recent = recent_highs[-1]  # Most recent high
        h_prev = recent_highs[-2]    # Previous high

        # Bullish: recent low > previous high (breaks bearish structure)
        if l_recent.price > h_prev.price and l_recent.index > h_prev.index:
            fib_low, fib_high = calculate_fib_entry(h_prev.price, l_recent.price)
            entry_zone = (fib_low, fib_high)
            stop = h_prev.price
            risk = abs(l_recent.price - h_prev.price)
            tp = l_recent.price + (risk * rr_min)
            rr = risk / abs(tp - l_recent.price) if (tp - l_recent.price) != 0 else 0.0

            patterns.append(CHoCH(
                symbol=symbol or "",
                direction="BULLISH",
                interval=interval,
                trigger_price=l_recent.price,
                pivot_price=h_prev.price,
                formed_at=l_recent.timestamp,
                index=l_recent.index,
                pullback_entry_zone=entry_zone,
                stop_loss=stop,
                take_profit=tp,
                rr_ratio=rr if rr > 0 else None,
                is_valid=rr >= rr_min if rr > 0 else False,
            ))

    # Bearish CHoCH: recent high breaks below older low
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        h_recent = recent_highs[-1]
        h_prev = recent_highs[-2]
        l_recent = recent_lows[-1]
        l_prev = recent_lows[-2]

        # Bearish: recent high < previous low (breaks bullish structure)
        if h_recent.price < l_prev.price and h_recent.index > l_prev.index:
            fib_low, fib_high = calculate_fib_entry(h_recent.price, l_prev.price)
            entry_zone = (fib_low, fib_high)
            stop = l_prev.price
            risk = abs(l_prev.price - h_recent.price)
            tp = h_recent.price - (risk * rr_min)
            rr = risk / abs(h_recent.price - tp) if (h_recent.price - tp) != 0 else 0.0

            patterns.append(CHoCH(
                symbol=symbol or "",
                direction="BEARISH",
                interval=interval,
                trigger_price=h_recent.price,
                pivot_price=l_prev.price,
                formed_at=h_recent.timestamp,
                index=h_recent.index,
                pullback_entry_zone=entry_zone,
                stop_loss=stop,
                take_profit=tp,
                rr_ratio=rr if rr > 0 else None,
                is_valid=rr >= rr_min if rr > 0 else False,
            ))

    return patterns
