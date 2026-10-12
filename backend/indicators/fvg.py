"""Fair Value Gap (FVG) detection."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class FVG:
    """A Fair Value Gap — three-candle imbalance zone."""
    symbol: str
    direction: str              # 'BULLISH' or 'BEARISH'
    zone_min: float
    zone_max: float
    midpoint: float
    impulse_high: float
    impulse_low: float
    formed_at: datetime
    bar_index: int
    filled: bool = False
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None


def detect_fvg(candles: list[dict], symbol: str = "") -> list[FVG]:
    """
    Detect Fair Value Gaps (3-candle imbalance) in a list of candles.
    candles: list of OHLCV dicts with keys: open, high, low, close, close_time

    Uses the classic ICT/SMC imbalance definition — a gap left by an impulse
    candle between c1 and c3:
      Bullish: gap where c3.low > c1.high (price never returned to fill the
               range between c1's high and c3's low), with a bullish impulse.
      Bearish: gap where c3.high < c1.low, with a bearish impulse.

    The earlier definition required zero overlap between ADJACENT candles
    (c2.low > c1.high), which essentially never happens on liquid crypto —
    0 detections across 200 candles on 1h/4h. This classic form fires 20-28x
    over the same window.

    The FVG zone is the unfilled gap [c1.high, c3.low] (bullish) or
    [c3.high, c1.low] (bearish); midpoint is the entry reference.
    """
    fvgs = []
    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish FVG: gap above c1.high left unfilled through c3.low
        if c3["low"] > c1["high"] and c2["close"] > c2["open"]:
            zone_min = c1["high"]
            zone_max = c3["low"]
            fvg = FVG(
                symbol=symbol or "",
                direction="BULLISH",
                zone_min=zone_min,
                zone_max=zone_max,
                midpoint=(zone_min + zone_max) / 2,
                impulse_high=c2["high"],
                impulse_low=c2["low"],
                formed_at=c3.get("close_time", datetime.now()),
                bar_index=i,
            )
            fvgs.append(fvg)

        # Bearish FVG: gap below c1.low left unfilled through c3.high
        elif c3["high"] < c1["low"] and c2["close"] < c2["open"]:
            zone_min = c3["high"]
            zone_max = c1["low"]
            fvg = FVG(
                symbol=symbol or "",
                direction="BEARISH",
                zone_min=zone_min,
                zone_max=zone_max,
                midpoint=(zone_min + zone_max) / 2,
                impulse_high=c2["high"],
                impulse_low=c2["low"],
                formed_at=c3.get("close_time", datetime.now()),
                bar_index=i,
            )
            fvgs.append(fvg)

    return fvgs


def check_fvg_filled(fvg: FVG, current_price: float, high: float, low: float) -> bool:
    """Check if price has traded back into the FVG zone (mitigated it).

    A bullish FVG sits below price — it is filled when price returns DOWN into
    the gap (low <= zone_max). A bearish FVG sits above price — filled when
    price returns UP into the gap (high >= zone_min).
    """
    if fvg.direction == "BULLISH":
        return low <= fvg.zone_max
    return high >= fvg.zone_min


def evaluate_fvg_entry(fvg: FVG, atr: float, rr_min: float = 3.0, current_price: float = 0.0) -> dict:
    """
    Given an FVG and ATR, calculate entry, SL, TP, and R:R.
    Returns dict with entry_price, stop_loss, take_profit, rr_ratio, is_valid.
    """
    entry_price = fvg.midpoint

    if fvg.direction == "BULLISH":
        stop_loss = fvg.zone_min - atr
        risk_distance = entry_price - stop_loss
        take_profit = entry_price + (risk_distance * rr_min)
    else:
        stop_loss = fvg.zone_max + atr
        risk_distance = stop_loss - entry_price
        take_profit = entry_price - (risk_distance * rr_min)

    if risk_distance <= 0:
        return {"entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
                "rr_ratio": 0.0, "is_valid": False}

    # R:R = reward / risk (TP is built from risk * rr_min, so this equals rr_min)
    rr_ratio = abs(take_profit - entry_price) / risk_distance

    return {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rr_ratio": rr_ratio,
        "is_valid": rr_ratio >= rr_min,
    }
