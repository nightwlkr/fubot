"""Trend bias analysis — combines SMA + RSI for composite trend assessment."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from backend.indicators.sma import calculate_sma, get_trend_bias
from backend.indicators.rsi import calculate_rsi, get_rsi_zone


@dataclass
class TrendBias:
    """Composite trend bias assessment."""
    direction: str          # 'BULLISH', 'BEARISH', 'NEUTRAL'
    strength: str            # 'STRONG', 'MODERATE', 'WEAK'
    sma_50: float
    sma_200: Optional[float]
    rsi: float
    rsi_zone: str           # 'OVERBOUGHT', 'OVERSOLD', 'NEUTRAL'
    bias_score: int          # -100 to +100 (composite score)
    timestamp: datetime


def analyze_trend_bias(closes_1h: list[float], closes_4h: Optional[list[float]] = None) -> TrendBias:
    """
    Calculate composite trend bias from 1H close prices.

    Bias Score:
    - SMA bullish (price > SMA50): +40
    - SMA bearish (price < SMA50): -40
    - RSI oversold: +20
    - RSI overbought: -20
    - Strength: abs(score) >= 50 → STRONG, 25-49 → MODERATE, < 25 → WEAK
    """
    sma_50 = calculate_sma(closes_1h, 50)
    sma_200 = calculate_sma(closes_1h, 200) if len(closes_1h) >= 200 else None

    current_price = closes_1h[-1] if closes_1h else 0.0

    rsi = calculate_rsi(closes_1h, 14)
    rsi_zone = get_rsi_zone(rsi) if rsi is not None else "NEUTRAL"

    # SMA bias
    sma_bias = get_trend_bias(sma_50, current_price) if sma_50 else "NEUTRAL"
    if sma_bias == "BULLISH":
        score = 40
    elif sma_bias == "BEARISH":
        score = -40
    else:
        score = 0

    # RSI adjustment
    if rsi_zone == "OVERSOLD":
        score += 20
    elif rsi_zone == "OVERBOUGHT":
        score -= 20

    # Determine direction
    if score > 10:
        direction = "BULLISH"
    elif score < -10:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Strength
    abs_score = abs(score)
    if abs_score >= 50:
        strength = "STRONG"
    elif abs_score >= 25:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    return TrendBias(
        direction=direction,
        strength=strength,
        sma_50=sma_50 or 0.0,
        sma_200=sma_200,
        rsi=rsi or 50.0,
        rsi_zone=rsi_zone,
        bias_score=score,
        timestamp=datetime.now(),
    )


def should_trade_with_bias(trend_bias: TrendBias, entry_direction: str) -> tuple[bool, str]:
    """
    Determines if an entry aligns with trend bias.
    Returns: (should_trade: bool, reason: str)

    Rules:
    - BULLISH + LONG → YES
    - BEARISH + SHORT → YES
    - BULLISH + SHORT → SCALP ONLY
    - BEARISH + LONG → SCALP ONLY
    - RSI OVERBOUGHT + LONG → NO
    - RSI OVERSOLD + SHORT → NO
    - NEUTRAL → SCALP ONLY
    """
    if trend_bias.rsi_zone == "OVERBOUGHT" and entry_direction == "LONG":
        return False, "RSI overbought — long entry not allowed"
    if trend_bias.rsi_zone == "OVERSOLD" and entry_direction == "SHORT":
        return False, "RSI oversold — short entry not allowed"

    if trend_bias.direction == "BULLISH" and entry_direction == "LONG":
        return True, "Trend aligned"
    if trend_bias.direction == "BEARISH" and entry_direction == "SHORT":
        return True, "Trend aligned"
    if trend_bias.direction == "NEUTRAL":
        return True, "Neutral trend — scalp allowed"
    # Opposing trend
    return True, f"Counter-trend {entry_direction} (scalp only)"
