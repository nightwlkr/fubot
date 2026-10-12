"""3-Candle / Liquidity Sweep pattern detection."""
from dataclasses import dataclass
from datetime import datetime

# Minimum stop distance as a multiple of ATR(14). The structural sweep wick is
# often only a few pips from entry on 5m candles — too tight for price noise
# (observed 0.07%-0.3% stops getting hit instantly). 1.0× ATR gives the stop
# room to breathe while keeping it on the correct structural side.
atr_stop_mult = 1.0


@dataclass
class ThreeCandle:
    """3-Candle liquidity sweep pattern."""
    symbol: str
    direction: str              # 'BULLISH' or 'BEARISH'
    interval: str
    c1_high: float
    c1_low: float
    c1_open: float
    c1_close: float
    c1_time: datetime
    c2_high: float
    c2_low: float
    c2_sweep_extreme: float
    c2_closed_in_range: bool
    c2_time: datetime
    c3_break_price: float
    c3_time: datetime
    entry_zone_min: float
    entry_zone_max: float
    entry_price: float
    stop_loss: float
    is_valid: bool
    formed_at: datetime


def detect_three_candles(candles: list[dict], symbol: str = "", interval: str = "5m") -> list[ThreeCandle]:
    """
    Detect all valid 3-Candle patterns.

    Bullish:
      - C2.wick_low < C1.low (wick sweep below)
      - C2.close >= C1.low AND C2.close <= C1.high (body inside range)
      - C3.close > C1.high (confirmation breakout)

    Bearish: mirror logic.
    """
    patterns = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish 3-Candle
        c2_swept_below = c2["low"] < c1["low"]
        c2_body_inside = c2["close"] >= c1["low"] and c2["close"] <= c1["high"]
        c3_broke_above = c3["close"] > c1["high"]

        if c2_swept_below and c2_body_inside and c3_broke_above:
            entry_price = c1["high"]
            stop_loss = c2["low"]

            patterns.append(ThreeCandle(
                symbol=symbol or "",
                direction="BULLISH",
                interval=interval,
                c1_high=c1["high"], c1_low=c1["low"],
                c1_open=c1["open"], c1_close=c1["close"],
                c1_time=c1.get("close_time", datetime.now()),
                c2_high=c2["high"], c2_low=c2["low"],
                c2_sweep_extreme=c2_swept_below and c2["low"] or c2["low"],
                c2_closed_in_range=True,
                c2_time=c2.get("close_time", datetime.now()),
                c3_break_price=c3["close"],
                c3_time=c3.get("close_time", datetime.now()),
                entry_zone_min=c1["low"],
                entry_zone_max=c1["high"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                is_valid=True,
                formed_at=c3.get("close_time", datetime.now()),
            ))

        # Bearish 3-Candle
        c2_swept_above = c2["high"] > c1["high"]
        c2_body_inside_bear = c2["close"] <= c1["high"] and c2["close"] >= c1["low"]
        c3_broke_below = c3["close"] < c1["low"]

        if c2_swept_above and c2_body_inside_bear and c3_broke_below:
            entry_price = c1["low"]
            stop_loss = c2["high"]

            patterns.append(ThreeCandle(
                symbol=symbol or "",
                direction="BEARISH",
                interval=interval,
                c1_high=c1["high"], c1_low=c1["low"],
                c1_open=c1["open"], c1_close=c1["close"],
                c1_time=c1.get("close_time", datetime.now()),
                c2_high=c2["high"], c2_low=c2["low"],
                c2_sweep_extreme=c2["high"],
                c2_closed_in_range=True,
                c2_time=c2.get("close_time", datetime.now()),
                c3_break_price=c3["close"],
                c3_time=c3.get("close_time", datetime.now()),
                entry_zone_min=c1["low"],
                entry_zone_max=c1["high"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                is_valid=True,
                formed_at=c3.get("close_time", datetime.now()),
            ))

    return patterns


def evaluate_three_candle_entry(pattern: ThreeCandle, atr: float, rr_min: float = 3.0) -> dict:
    """Calculate entry, SL, TP, R:R for a 3-Candle pattern.

    The structural stop is the sweep wick (c2.low for bullish, c2.high for
    bearish). But adjacent 5m candles can have a tiny range, giving a
    razor-thin stop that price noise hits instantly (observed: 0.07%-0.3%
    stops → immediate -1R losses). Widen the stop to at least `atr_stop_mult`
    × ATR(14) from entry so it sits beyond normal volatility, while staying
    on the correct structural side.
    """
    entry = pattern.entry_price
    structural_sl = pattern.stop_loss
    min_stop = max(atr, 0.0) * atr_stop_mult

    if pattern.direction == "BULLISH":
        # stop must be BELOW entry; structural stop is c2.low. If it's closer
        # than the ATR floor, push it down to entry - min_stop.
        sl = structural_sl
        if entry - sl < min_stop:
            sl = entry - min_stop
        risk = entry - sl
        tp = entry + (risk * rr_min)
    else:
        # stop must be ABOVE entry; structural stop is c2.high.
        sl = structural_sl
        if sl - entry < min_stop:
            sl = entry + min_stop
        risk = sl - entry
        tp = entry - (risk * rr_min)

    rr = abs(tp - entry) / risk if risk > 0 else 0.0  # R:R = reward / risk

    return {
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "rr_ratio": rr,
        "is_valid": risk > 0 and rr >= rr_min,
    }
