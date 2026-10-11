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
    Detect all FVGs in a list of candles.
    candles: list of OHLCV dicts with keys: open, high, low, close, close_time

    Bullish FVG:
      - candle[i-2] bearish (close < open)
      - candle[i-1] bullish (close > open) AND low > candle[i-2].high (gap up)
      - candle[i].close > candle[i-1].high (confirmed)

    Bearish FVG: mirror logic.
    """
    fvgs = []
    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish FVG
        if (
            c1["close"] < c1["open"] and          # c1 bearish
            c2["close"] > c2["open"] and          # c2 bullish
            c2["low"] > c1["high"] and             # gap up between bodies
            c3["close"] > c2["high"]               # confirmation
        ):
            fvg = FVG(
                symbol=symbol or "",
                direction="BULLISH",
                # zone bounds are numeric: min = gap bottom, max = gap top
                zone_min=c1["high"],
                zone_max=c2["low"],
                midpoint=(c1["high"] + c2["low"]) / 2,
                impulse_high=c2["high"],
                impulse_low=c2["low"],
                formed_at=candles[i - 1].get("close_time", datetime.now()),
                bar_index=i - 1,
            )
            fvgs.append(fvg)

        # Bearish FVG
        elif (
            c1["close"] > c1["open"] and          # c1 bullish
            c2["close"] < c2["open"] and          # c2 bearish
            c2["high"] < c1["low"] and            # gap down between bodies
            c3["close"] < c2["low"]                # confirmation
        ):
            fvg = FVG(
                symbol=symbol or "",
                direction="BEARISH",
                # zone bounds are numeric: min = gap top, max = gap bottom
                zone_min=c2["high"],
                zone_max=c1["low"],
                midpoint=(c1["low"] + c2["high"]) / 2,
                impulse_high=c2["high"],
                impulse_low=c2["low"],
                formed_at=candles[i - 1].get("close_time", datetime.now()),
                bar_index=i - 1,
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
