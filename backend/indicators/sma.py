"""Simple Moving Average indicator."""
from typing import Optional


def calculate_sma(closes: list[float], period: int = 50) -> Optional[float]:
    """Calculate SMA from a list of close prices. Returns None if not enough data."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def get_sma_multi(closes: list[float], periods: list[int]) -> dict[int, Optional[float]]:
    """Calculate multiple SMAs at once. Returns {period: sma_value}."""
    return {p: calculate_sma(closes, p) for p in periods}


def get_trend_bias(sma_50: float, current_price: float, sma_200: Optional[float] = None) -> str:
    """
    Determine trend bias based on SMA.
    Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'
    - sma_50 > current_price → BEARISH (price below average)
    - sma_50 < current_price → BULLISH (price above average)
    - Within 0.1% → NEUTRAL
    """
    diff_pct = abs(sma_50 - current_price) / current_price
    if diff_pct <= 0.001:
        return "NEUTRAL"
    if sma_50 > current_price:
        return "BEARISH"
    return "BULLISH"
