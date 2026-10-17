"""VWAP (Volume-Weighted Average Price) mean reversion signal detection.

Strategy: price deviates N×ATR from session VWAP, enter a fade targeting the
VWAP reversion. Wide stops (beyond the deviation extreme) make this fee-friendly.

Entry logic:
  LONG:  price dips below VWAP - k*ATR, close back above VWAP - k*ATR → buy
  SHORT: price rallies above VWAP + k*ATR, close back below VWAP + k*ATR → sell

Stops: 1.5×ATR beyond the deviation extreme (structural, not indicator-based)
Target: 2× the risk distance (R:R 2.0 — wide enough to survive fee drag)

Lookahead-free: uses `calculate_atr_series` (per-index ATR) and a rolling
VWAP window, so bar i's signal only sees candles[0..i].
"""
from dataclasses import dataclass


@dataclass
class VWAPSignal:
    direction: str          # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    vwap_value: float
    deviation_atr: float    # how far price was from VWAP at the extreme
    is_valid: bool
    formed_at_idx: int      # candle index where the signal formed


def calculate_vwap(candles: list[dict], lookback: int = 96) -> list[float]:
    """Rolling VWAP over `lookback` candles.

    VWAP = Σ(typical_price × volume) / Σ(volume)
    typical_price = (high + low + close) / 3
    Returns [] when len(candles) < lookback — callers must not fall back to
    close, which silently degenerates the band to "close ± k*ATR".
    """
    if len(candles) < lookback:
        return []

    vwap_values = []
    for i in range(len(candles)):
        start = max(0, i - lookback + 1)
        window = candles[start:i + 1]

        num = 0.0
        den = 0.0
        for c in window:
            tp = (c["high"] + c["low"] + c["close"]) / 3.0
            vol = c.get("volume", 1.0) or 1.0
            num += tp * vol
            den += vol

        vwap_values.append(num / den if den > 0 else candles[i]["close"])

    return vwap_values


def detect_vwap_reversion(
    candles: list[dict],
    atr_series: list[float],
    deviation_mult: float = 1.5,
    rr_min: float = 2.0,
    lookback: int = 96,
    start_idx: int = 60
) -> list[VWAPSignal]:
    """Detect VWAP mean-reversion signals.

    `atr_series` is a per-index ATR (from `calculate_atr_series`) — NOT a
    scalar. A scalar would use the full-series ATR for every historical bar,
    leaking future volatility into past stops and bands.

    Signal: price deviates > deviation_mult × ATR from VWAP, then closes back
    inside the band (rejection of the extreme).

    Stops sit beyond the DEVIATION EXTREME (prev candle's wick), not the
    reversal candle's — the reversal candle's wick is inside the structure
    the stop is meant to protect, so a stop there can be hit at a price the
    structure never offered.
    """
    if len(candles) < lookback or len(atr_series) < len(candles):
        return []

    vwap = calculate_vwap(candles, lookback)
    if not vwap:
        return []

    signals = []

    for i in range(start_idx, len(candles)):
        atr = atr_series[i]
        if atr <= 0:
            continue

        v = vwap[i]
        c = candles[i]
        prev = candles[i - 1] if i > 0 else c

        lower_band = v - deviation_mult * atr
        upper_band = v + deviation_mult * atr

        # Bullish reversion: prev closed below lower band, current closes above it
        if prev["close"] < lower_band and c["close"] >= lower_band:
            deviation = v - c["close"]  # positive = below VWAP
            entry_price = c["close"]
            stop_loss = prev["low"] - 1.5 * atr  # below the deviation extreme (prev's wick)
            risk_dist = entry_price - stop_loss
            if risk_dist <= 0:
                continue
            take_profit = entry_price + risk_dist * rr_min

            signals.append(VWAPSignal(
                direction="LONG",
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rr_ratio=rr_min,
                vwap_value=v,
                deviation_atr=deviation / atr,
                is_valid=True,
                formed_at_idx=i,
            ))

        # Bearish reversion: prev closed above upper band, current closes back inside/below
        elif prev["close"] > upper_band and c["close"] <= upper_band:
            deviation = c["close"] - v  # positive = above VWAP
            entry_price = c["close"]
            stop_loss = prev["high"] + 1.5 * atr  # above the deviation extreme (prev's wick)
            risk_dist = stop_loss - entry_price
            if risk_dist <= 0:
                continue
            take_profit = entry_price - risk_dist * rr_min

            signals.append(VWAPSignal(
                direction="SHORT",
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rr_ratio=rr_min,
                vwap_value=v,
                deviation_atr=deviation / atr,
                is_valid=True,
                formed_at_idx=i,
            ))

    return signals
