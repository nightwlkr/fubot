"""Volume bias analysis — buy/sell volume split and confirmation filter."""
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class VolumeBias:
    """Volume bias for a zone/candle sequence."""
    total_buy_volume: float
    total_sell_volume: float
    dominant_side: str         # 'BUY', 'SELL', or 'NEUTRAL'
    buy_ratio: float            # buy / (buy + sell), 0.0 to 1.0
    sell_ratio: float           # sell / (buy + sell), 0.0 to 1.0
    is_confirmed: bool          # True if buy_ratio >= 0.55 or sell_ratio >= 0.55


@dataclass
class VolumeConfirmedSignal:
    """Volume-filtered signal result."""
    signal_type: str        # 'FVG', '3CANDLE', 'CHOC'
    direction: str
    volume_bias: VolumeBias
    passes_filter: bool     # True only if volume confirms direction


def estimate_buy_sell_volume(candle: dict) -> Tuple[float, float]:
    """
    Split a candle's total volume into estimated buy and sell volume.

    Method: Position of close within the high-low range.
    - close near high → most volume was buying
    - close near low → most volume was selling
    """
    high = candle["high"]
    low = candle["low"]
    close = candle["close"]
    volume = candle["volume"]

    if high == low:
        return volume * 0.5, volume * 0.5

    close_position = (close - low) / (high - low)
    close_position = max(0.0, min(1.0, close_position))

    buy_volume = volume * close_position
    sell_volume = volume * (1.0 - close_position)

    return buy_volume, sell_volume


def analyze_zone_volume(candles: list[dict]) -> VolumeBias:
    """Sum buy/sell volume across a series of candles. Returns dominant side."""
    total_buy = 0.0
    total_sell = 0.0

    for candle in candles:
        buy, sell = estimate_buy_sell_volume(candle)
        total_buy += buy
        total_sell += sell

    total = total_buy + total_sell
    if total == 0:
        return VolumeBias(0.0, 0.0, "NEUTRAL", 0.5, 0.5, False)

    buy_ratio = total_buy / total
    sell_ratio = total_sell / total

    if buy_ratio >= 0.55:
        dominant = "BUY"
        confirmed = True
    elif sell_ratio >= 0.55:
        dominant = "SELL"
        confirmed = True
    else:
        dominant = "NEUTRAL"
        confirmed = False

    return VolumeBias(
        total_buy_volume=total_buy,
        total_sell_volume=total_sell,
        dominant_side=dominant,
        buy_ratio=buy_ratio,
        sell_ratio=sell_ratio,
        is_confirmed=confirmed,
    )


def get_volume_confirmed_filter(
    signal_direction: str,
    candles_in_zone: list[dict],
) -> VolumeConfirmedSignal:
    """
    Check if volume confirms the signal direction.

    Rules:
    - LONG signal → volume must be BUY-dominant and confirmed
    - SHORT signal → volume must be SELL-dominant and confirmed
    - Otherwise → filter fails
    """
    bias = analyze_zone_volume(candles_in_zone)

    passes = (
        (signal_direction == "LONG" and bias.dominant_side == "BUY" and bias.is_confirmed) or
        (signal_direction == "SHORT" and bias.dominant_side == "SELL" and bias.is_confirmed)
    )

    return VolumeConfirmedSignal(
        signal_type="",
        direction=signal_direction,
        volume_bias=bias,
        passes_filter=passes,
    )
