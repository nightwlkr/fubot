"""BOS-Based Trading Strategy - Break of Structure entries.
Strategy: Enter on confirmed BOS (Break of Structure) signals.
- Bullish BOS: Price breaks above previous pivot high
- Bearish BOS: Price breaks below previous pivot low
- Entry at BOS confirmation price
- Structural stop at broken level (pivot high/low)
- Take profit at 3× R:R from risk distance
- Minimum stop distance: 1.5× ATR(14) from entry
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass
class BOSSignal:
    """A BOS-based trading signal."""
    symbol: str
    direction: str              # 'BULLISH' or 'BEARISH'
    interval: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_distance: float
    rr_ratio: float
    formed_at: datetime
    index: int
    is_valid: bool


def detect_bos_signals(
    candles: list[dict],
    symbol: str = "",
    interval: str = "5m",
    atr: float = 0.0,
    rr_min: float = 4.0,
    atr_stop_mult: float = 1.5,
    left_bars: int = 5,
    right_bars: int = 5,
) -> list[BOSSignal]:
    """
    Detect BOS signals with proper risk management.

    Bullish BOS: Price breaks above previous pivot high
    Bearish BOS: Price breaks below previous pivot low

    Entry at confirmation price (the candle that broke the level).
    Stop at the broken level (pivot high/low).
    Risk distance = |entry - stop|
    Take profit = entry ± (risk_distance × rr_min)

    The stop is widened to atr_stop_mult × ATR if it's too tight.
    """
    from backend.indicators.bos import detect_bos
    from backend.indicators.pivots import find_pivot_highs_lows

    # Get all BOS patterns
    bos_patterns = detect_bos(candles, symbol, interval, left_bars, right_bars)
    signals = []

    # Filter and validate each BOS pattern
    for bos in bos_patterns:
        # Skip if ATR is not available (first few bars)
        if atr <= 0:
            continue

        entry = bos.confirmation_price
        broken_level = bos.broken_level
        risk = abs(entry - broken_level)

        # Validate risk distance
        if risk <= 0:
            continue

        # Minimum stop distance using ATR
        min_stop = atr * atr_stop_mult

        if bos.direction == "BULLISH":
            # Stop must be BELOW entry
            stop = broken_level
            if entry - stop < min_stop:
                stop = entry - min_stop
                risk = entry - stop
        else:
            # Stop must be ABOVE entry
            stop = broken_level
            if stop - entry < min_stop:
                stop = entry + min_stop
                risk = stop - entry

        # Calculate take profit
        if bos.direction == "BULLISH":
            tp = entry + (risk * rr_min)
        else:
            tp = entry - (risk * rr_min)

        # Calculate R:R ratio
        rr_ratio = abs(tp - entry) / risk if risk > 0 else 0.0

        # Validate R:R ratio
        is_valid = risk > 0 and rr_ratio >= rr_min

        if is_valid:
            signals.append(BOSSignal(
                symbol=symbol or "",
                direction=bos.direction,
                interval=interval,
                entry_price=entry,
                stop_loss=stop,
                take_profit=tp,
                risk_distance=risk,
                rr_ratio=rr_ratio,
                formed_at=bos.formed_at,
                index=bos.index,
                is_valid=True,
            ))

    return signals


def evaluate_bos_entry(
    entry: float,
    stop: float,
    direction: str,
    atr: float,
    rr_min: float = 4.0,
    atr_stop_mult: float = 1.5,
) -> Tuple[float, float, float, float, bool]:
    """
    Calculate entry, stop loss, take profit, R:R ratio for a BOS signal.

    Returns: (entry_price, stop_loss, take_profit, rr_ratio, is_valid)
    """
    if direction not in ("BULLISH", "BEARISH"):
        return (0.0, 0.0, 0.0, 0.0, False)

    if atr <= 0:
        return (entry, stop, stop, 0.0, False)

    risk = abs(entry - stop)
    if risk <= 0:
        return (entry, stop, stop, 0.0, False)

    min_stop = atr * atr_stop_mult

    if direction == "BULLISH":
        # Ensure stop is below entry
        if entry - stop < min_stop:
            stop = entry - min_stop
            risk = entry - stop
        tp = entry + (risk * rr_min)
    else:
        # Ensure stop is above entry
        if stop - entry < min_stop:
            stop = entry + min_stop
            risk = stop - entry
        tp = entry - (risk * rr_min)

    rr_ratio = abs(tp - entry) / risk if risk > 0 else 0.0
    is_valid = risk > 0 and rr_ratio >= rr_min

    return (entry, stop, tp, rr_ratio, is_valid)


def check_bos_signal_filled(
    signal: BOSSignal,
    current_price: float,
    high: float,
    low: float,
    time_threshold_minutes: int = 60,
) -> bool:
    """
    Check if BOS signal has been filled.

    Bullish signal: filled if price is ABOVE entry (stop is below entry)
    Bearish signal: filled if price is BELOW entry (stop is above entry)

    Also checks time threshold (signal must be fresh).
    """
    from datetime import datetime, timedelta

    if not signal.is_valid:
        return False

    # Check time threshold
    time_since_signal = (datetime.now() - signal.formed_at).total_seconds() / 60
    if time_since_signal > time_threshold_minutes:
        return False

    if signal.direction == "BULLISH":
        return current_price >= signal.entry_price
    else:
        return current_price <= signal.entry_price


def get_bos_entry_zone(
    candles: list[dict],
    index: int,
    direction: str,
    atr: float,
    rr_min: float = 4.0,
    atr_stop_mult: float = 1.5,
) -> Optional[Tuple[float, float, float, float, float]]:
    """
    Get entry zone, stop, and TP for a BOS signal at a specific index.

    Returns: (entry_price, stop_loss, take_profit, risk_distance, rr_ratio) or None
    """
    if index >= len(candles) or index < 0:
        return None

    entry = candles[index]["close"]
    high = candles[index]["high"]
    low = candles[index]["low"]

    # Calculate stop and TP
    stop, tp, rr_ratio, is_valid = evaluate_bos_entry(
        entry, 0.0, direction, atr, rr_min, atr_stop_mult
    )

    if not is_valid:
        return None

    # For BOS, the broken level is the stop
    # We need to find it from the pivot structure
    from backend.indicators.bos import detect_bos
    from backend.indicators.pivots import find_pivot_highs_lows

    bos_patterns = detect_bos(candles, "", "5m", 5, 5)

    for bos in bos_patterns:
        if bos.index == index:
            if direction == "BULLISH":
                stop = bos.broken_level
            else:
                stop = bos.broken_level
            break

    if stop <= 0:
        return None

    risk = abs(entry - stop)
    return (entry, stop, tp, risk, rr_ratio)
