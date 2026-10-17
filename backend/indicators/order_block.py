"""Order Block detection — institutional supply/demand zones.

Strategy: Order Block = last opposing candle before a strong move (BOS).
Combined with FVG, only take FVG signals that sit inside a swept Order Block.

This is a filter, not a standalone strategy — used to pre-filter FVG candidates.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderBlock:
    direction: str          # 'BULLISH' (demand) or 'BEARISH' (supply)
    zone_min: float
    zone_max: float
    midpoint: float
    high: float
    low: float
    formed_at_idx: int      # candle index of the OB candle itself
    confirm_at_idx: int     # index when the OB becomes knowable (after pivot + BOS confirm)
    swept: bool = False
    swept_at_idx: Optional[int] = None


def detect_order_blocks(
    candles: list[dict],
    left_bars: int = 2,
    right_bars: int = 2,
    start_idx: int = 30
) -> list[OrderBlock]:
    """Detect Order Blocks from pivot structure.

    Bullish OB: last bearish candle (lower close) before a pivot low that leads
    to a strong bullish move (breaking the last pivot high).
    Bearish OB: last bullish candle before a pivot high that leads to a strong
    bearish move (breaking the last pivot low).

    The OB is only knowable after BOTH the pivot (right_bars after it) AND the
    BOS pivot (right_bars after that) are confirmed. `confirm_at_idx` records
    the later of those two confirmations — an OB stamped at j is visible only
    from confirm_at_idx onwards, never from j itself.
    """
    if len(candles) < start_idx + right_bars + 2:
        return []

    obs = []

    # Find pivot highs and lows with their confirmation indices
    pivot_highs = []  # (idx, price, confirm_idx)
    pivot_lows = []

    for i in range(left_bars, len(candles) - right_bars):
        is_high = True
        for j in range(1, left_bars + 1):
            if candles[i]["high"] <= candles[i - j]["high"]:
                is_high = False
                break
        if is_high:
            for j in range(1, right_bars + 1):
                if candles[i]["high"] <= candles[i + j]["high"]:
                    is_high = False
                    break
        if is_high:
            pivot_highs.append((i, candles[i]["high"], i + right_bars))

        is_low = True
        for j in range(1, left_bars + 1):
            if candles[i]["low"] >= candles[i - j]["low"]:
                is_low = False
                break
        if is_low:
            for j in range(1, right_bars + 1):
                if candles[i]["low"] >= candles[i + j]["low"]:
                    is_low = False
                    break
        if is_low:
            pivot_lows.append((i, candles[i]["low"], i + right_bars))

    # Bullish OB: bearish candle before a pivot low, followed by strong move up
    for pivot_idx, _pivot_price, pivot_confirm_idx in pivot_lows:
        if pivot_idx < start_idx:
            continue
        for j in range(pivot_idx - 1, max(start_idx, pivot_idx - 4), -1):
            c = candles[j]
            if c["close"] < c["open"]:  # bearish candle
                later_highs = [(idx, h, cidx) for idx, h, cidx in pivot_highs if idx > j and idx < j + 10]
                if later_highs and max(h for _, h, _ in later_highs) > c["high"]:
                    # OB is knowable after the BOS pivot high is confirmed
                    bos_confirm_idx = max(cidx for _, _, cidx in later_highs)
                    confirm_at_idx = max(pivot_confirm_idx, bos_confirm_idx)
                    obs.append(OrderBlock(
                        direction="BULLISH",
                        zone_min=c["low"],
                        zone_max=c["high"],
                        midpoint=(c["low"] + c["high"]) / 2,
                        high=c["high"],
                        low=c["low"],
                        formed_at_idx=j,
                        confirm_at_idx=confirm_at_idx,
                    ))
                break

    # Bearish OB: bullish candle before a pivot high, followed by strong move down
    for pivot_idx, _pivot_price, pivot_confirm_idx in pivot_highs:
        if pivot_idx < start_idx:
            continue
        for j in range(pivot_idx - 1, max(start_idx, pivot_idx - 4), -1):
            c = candles[j]
            if c["close"] > c["open"]:  # bullish candle
                later_lows = [(idx, l, cidx) for idx, l, cidx in pivot_lows if idx > j and idx < j + 10]
                if later_lows and min(l for _, l, _ in later_lows) < c["low"]:
                    bos_confirm_idx = max(cidx for _, _, cidx in later_lows)
                    confirm_at_idx = max(pivot_confirm_idx, bos_confirm_idx)
                    obs.append(OrderBlock(
                        direction="BEARISH",
                        zone_min=c["low"],
                        zone_max=c["high"],
                        midpoint=(c["low"] + c["high"]) / 2,
                        high=c["high"],
                        low=c["low"],
                        formed_at_idx=j,
                        confirm_at_idx=confirm_at_idx,
                    ))
                break

    return obs


def fvg_within_order_block(
    fvg_zone_min: float,
    fvg_zone_max: float,
    fvg_direction: str,
    obs: list[OrderBlock],
    lookback: int = 50,
    current_idx: int = 0,
) -> bool:
    """Check if an FVG zone overlaps with a RECENT, ALREADY-CONFIRMED Order Block
    of matching direction.

    Uses `confirm_at_idx` (not `formed_at_idx`) for the visibility bound: an OB
    stamped at bar j is only knowable after its pivot + BOS confirmations land.
    Without this, an OB forming 5 bars after the FVG would count as confluence —
    the confluence has to be visible at signal time, not in the future.
    """
    relevant = [
        ob for ob in obs
        if ob.direction == fvg_direction
        and ob.confirm_at_idx <= current_idx
        and current_idx - ob.confirm_at_idx <= lookback
    ]

    for ob in relevant:
        if fvg_zone_min <= ob.zone_max * 1.01 and fvg_zone_max >= ob.zone_min * 0.99:
            return True

    return False
