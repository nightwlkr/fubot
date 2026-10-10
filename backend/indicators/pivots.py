"""Pivot point detection — swing highs and lows."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple


@dataclass
class PivotPoint:
    """A swing high or low pivot."""
    type: str          # 'HIGH' or 'LOW'
    price: float
    index: int         # Candle index in the dataset
    timestamp: datetime


def find_pivot_highs_lows(
    candles: list[dict],
    left_bars: int = 5,
    right_bars: int = 5,
) -> Tuple[List[PivotPoint], List[PivotPoint]]:
    """
    Find all pivot highs and pivot lows.

    Pivot High: high of this candle is higher than left_bars candles to the left
                AND right_bars candles to the right.
    Pivot Low: low of this candle is lower than left_bars candles to the left
               AND right_bars candles to the right.

    Default: left_bars=5, right_bars=5 (swing high/low of ~11 candles).
    """
    highs: List[PivotPoint] = []
    lows: List[PivotPoint] = []

    for i in range(left_bars, len(candles) - right_bars):
        # Check pivot high
        is_high = True
        for j in range(1, left_bars + 1):
            if candles[i - j]["high"] >= candles[i]["high"]:
                is_high = False
                break
        if is_high:
            for j in range(1, right_bars + 1):
                if candles[i + j]["high"] >= candles[i]["high"]:
                    is_high = False
                    break
        if is_high:
            highs.append(PivotPoint(
                type="HIGH",
                price=candles[i]["high"],
                index=i,
                timestamp=candles[i].get("close_time", datetime.now()),
            ))

        # Check pivot low
        is_low = True
        for j in range(1, left_bars + 1):
            if candles[i - j]["low"] <= candles[i]["low"]:
                is_low = False
                break
        if is_low:
            for j in range(1, right_bars + 1):
                if candles[i + j]["low"] <= candles[i]["low"]:
                    is_low = False
                    break
        if is_low:
            lows.append(PivotPoint(
                type="LOW",
                price=candles[i]["low"],
                index=i,
                timestamp=candles[i].get("close_time", datetime.now()),
            ))

    return highs, lows
