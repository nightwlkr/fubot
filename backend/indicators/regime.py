"""Market regime detection — combines ADX + SMA + volume for comprehensive analysis."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from backend.indicators.adx import calculate_adx, get_market_regime
from backend.indicators.sma import calculate_sma
from backend.indicators.rsi import calculate_rsi


@dataclass
class MarketRegime:
    """Comprehensive market regime assessment."""
    regime: str                    # 'STRONG_BULL', 'STRONG_BEAR', 'WEAK_TREND', 'RANGING'
    adx: float                     # Trend strength (0-100)
    plus_di: float                 # Bullish pressure
    minus_di: float                # Bearish pressure
    trend_strength: str            # 'STRONG', 'MODERATE', 'WEAK'
    price_vs_sma: str              # 'ABOVE', 'BELOW', 'NEAR'
    rsi: float                     # Current RSI
    rsi_zone: str                  # 'OVERBOUGHT', 'OVERSOLD', 'NEUTRAL'
    bias_score: int                # -100 to +100 composite score
    timestamp: datetime


def analyze_market_regime(
    highs_1h: list[float],
    lows_1h: list[float],
    closes_1h: list[float],
    period: int = 14,
) -> MarketRegime:
    """Calculate comprehensive market regime from 1h candles.
    
    Combines:
    1. ADX for trend strength (regime classification)
    2. SMA50 for trend direction
    3. RSI for overbought/oversold
    4. Composite bias score for entry timing
    """
    if len(closes_1h) < period * 2 + 1:
        # Not enough data — return neutral
        return MarketRegime(
            regime="UNKNOWN",
            adx=0,
            plus_di=50,
            minus_di=50,
            trend_strength="WEAK",
            price_vs_sma="NEAR",
            rsi=50,
            rsi_zone="NEUTRAL",
            bias_score=0,
            timestamp=datetime.now(),
        )
    
    # Calculate ADX
    adx_data = calculate_adx(highs_1h, lows_1h, closes_1h, period)
    
    # Calculate SMA
    sma_50 = calculate_sma(closes_1h, 50)
    current_price = closes_1h[-1]
    
    # Determine price vs SMA
    if sma_50:
        diff_pct = (current_price - sma_50) / sma_50
        if diff_pct > 0.005:  # > 0.5% above
            price_vs_sma = "ABOVE"
        elif diff_pct < -0.005:  # > 0.5% below
            price_vs_sma = "BELOW"
        else:
            price_vs_sma = "NEAR"
    else:
        price_vs_sma = "NEAR"
    
    # Calculate RSI
    rsi = calculate_rsi(closes_1h, 14)
    if rsi is None:
        rsi = 50.0
    
    if rsi >= 70:
        rsi_zone = "OVERBOUGHT"
    elif rsi <= 30:
        rsi_zone = "OVERSOLD"
    else:
        rsi_zone = "NEUTRAL"
    
    # Get regime from ADX + price position
    regime = get_market_regime(adx_data, current_price, sma_50) if adx_data else "UNKNOWN"
    
    # Calculate composite bias score
    adx = adx_data.get("adx", 0) if adx_data else 0
    plus_di = adx_data.get("plus_di", 50) if adx_data else 50
    minus_di = adx_data.get("minus_di", 50) if adx_data else 50
    
    # Base score from ADX + DI divergence
    if adx > 25:
        # Strong trend — score follows DI direction
        base_score = (plus_di - minus_di) * 2  # Scale up to ~100 range
    elif adx > 20:
        base_score = (plus_di - minus_di)  # Moderate
    else:
        base_score = 0  # Ranging — no trend bias
    
    # Adjust for price vs SMA
    if price_vs_sma == "ABOVE":
        base_score += 20
    elif price_vs_sma == "BELOW":
        base_score -= 20
    
    # Adjust for RSI (mean-reversion signal)
    if rsi_zone == "OVERSOLD" and regime in ("STRONG_BEAR", "WEAK_TREND"):
        base_score += 15  # Potential reversal
    elif rsi_zone == "OVERBOUGHT" and regime in ("STRONG_BULL", "WEAK_TREND"):
        base_score -= 15
    
    # Clamp to -100 to +100
    bias_score = max(-100, min(100, int(base_score)))
    
    # Determine trend strength label
    if adx > 25:
        trend_strength = "STRONG"
    elif adx >= 20:
        trend_strength = "MODERATE"
    else:
        trend_strength = "WEAK"
    
    return MarketRegime(
        regime=regime,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        trend_strength=trend_strength,
        price_vs_sma=price_vs_sma,
        rsi=rsi,
        rsi_zone=rsi_zone,
        bias_score=bias_score,
        timestamp=datetime.now(),
    )


def should_trade_regime(regime: MarketRegime, entry_direction: str) -> tuple[bool, str]:
    """Determine if entry is allowed given market regime.
    
    Rules:
    - STRONG_BULL: Only LONG entries allowed
    - STRONG_BEAR: Only SHORT entries allowed
    - WEAK_TREND: Both allowed but lower confidence
    - RANGING: No entries (chop kills SMC strategies)
    - OVERBOUGHT + LONG: Skip (overextended)
    - OVERSOLD + SHORT: Skip (overextended)
    """
    if regime.regime == "UNKNOWN":
        return False, "Insufficient data for regime analysis"
    
    if regime.regime == "RANGING":
        return False, f"Ranging market (ADX {regime.adx:.1f}) — SMC strategies fail in chop"
    
    if regime.regime == "STRONG_BULL" and entry_direction == "SHORT":
        return False, "Counter-trend short in strong bull regime"
    
    if regime.regime == "STRONG_BEAR" and entry_direction == "LONG":
        return False, "Counter-trend long in strong bear regime"
    
    if regime.rsi_zone == "OVERBOUGHT" and entry_direction == "LONG":
        return False, f"RSI overbought ({regime.rsi:.1f}) — long entry risky"
    
    if regime.rsi_zone == "OVERSOLD" and entry_direction == "SHORT":
        return False, f"RSI oversold ({regime.rsi:.1f}) — short entry risky"
    
    if regime.regime == "WEAK_TREND" and regime.adx < 22:
        return False, f"Weak trend (ADX {regime.adx:.1f}) — insufficient momentum"
    
    return True, f"Regime OK: {regime.regime} (ADX {regime.adx:.1f})"


def get_regime_recommendation(regime: MarketRegime) -> str:
    """Get human-readable recommendation for current regime."""
    if regime.regime == "STRONG_BULL":
        return f"Strong uptrend (ADX {regime.adx:.1f}) — Look for LONG entries on pullbacks"
    elif regime.regime == "STRONG_BEAR":
        return f"Strong downtrend (ADX {regime.adx:.1f}) — Look for SHORT entries on rallies"
    elif regime.regime == "WEAK_TREND":
        return f"Weak trend (ADX {regime.adx:.1f}) — Wait for stronger signal"
    elif regime.regime == "RANGING":
        return f"Ranging market (ADX {regime.adx:.1f}) — Stand aside, no clear edge"
    else:
        return "Insufficient data"
