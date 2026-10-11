"""Relative Strength Index indicator (Wilder's method)."""
from typing import Optional


def calculate_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Calculate RSI from close prices using Wilder's smoothed averages.
    Returns value 0-100. Returns None if not enough data.
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Wilder's smoothed average: first value is simple average, then smoothed
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        # Zero losses AND zero gains (flat series) → RS is 0/0, undefined.
        # Convention: RSI 50 (neutral). Only pure gains yield 100.
        if avg_gain == 0:
            return 50.0
        return 100.0

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def get_rsi_zone(rsi: float) -> str:
    """Classify RSI into zone. Returns: 'OVERBOUGHT', 'OVERSOLD', or 'NEUTRAL'."""
    if rsi > 70:
        return "OVERBOUGHT"
    if rsi < 30:
        return "OVERSOLD"
    return "NEUTRAL"
