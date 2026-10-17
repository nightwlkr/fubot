"""Opening Range Breakout (ORB) signal detection.

Strategy: mark the high/low of the first N candles of a session (or time block),
enter on a close outside that range. Wide stops at the opposite range edge.

Crypto adaptation: no traditional market open, so we use the first `range_length`
candles of each UTC hour as the "opening range" (e.g., 3 × 5m candles = 15min block).

Entry: 5m close outside the range
Stop: opposite edge of the range (or 1×ATR beyond it for buffer)
Target: 2× the risk distance (R:R 2.0)

Lookahead-free: uses `calculate_atr_series` (per-index ATR) so bar i's
stop/filter uses only candles[0..i].
"""
from dataclasses import dataclass


@dataclass
class ORBSignal:
    direction: str          # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    range_high: float
    range_low: float
    range_width_atr: float  # range width as multiple of ATR (filter: too tight = noise)
    formed_at_idx: int


def detect_orb(
    candles: list[dict],
    atr_series: list[float],
    range_length: int = 3,       # candles in the opening range (3 × 5m = 15min)
    rr_min: float = 2.0,
    min_range_atr: float = 0.5,  # min range width as multiple of ATR (skip noise)
    max_range_atr: float = 3.0,  # max range width (too wide = breakout already priced in)
    session_candles: int = 12,   # candles per session block (12 × 5m = 1h)
    start_idx: int = 60,
    buffer_atr: float = 0.25,    # stop buffer beyond the range edge
) -> list[ORBSignal]:
    """Detect ORB signals.

    `atr_series` is a per-index ATR (from `calculate_atr_series`) — NOT a
    scalar. A scalar would use the full-series ATR for every historical bar,
    leaking future volatility into past stops and range filters.

    Session = every `session_candles` bars (e.g., 12 × 5m = 1 hour).
    Opening range = first `range_length` bars of each session.
    Entry = close outside the range (breakout confirmation).
    """
    if len(candles) < start_idx + range_length + 1 or len(atr_series) < len(candles):
        return []

    signals = []

    session_start = start_idx
    while session_start + range_length < len(candles):
        range_end = session_start + range_length
        range_candles = candles[session_start:range_end]

        range_high = max(c["high"] for c in range_candles)
        range_low = min(c["low"] for c in range_candles)
        range_width = range_high - range_low

        # Use ATR at the last bar of the opening range (lookahead-free)
        atr_at_range = atr_series[range_end - 1] if range_end - 1 < len(atr_series) else 0.0
        if atr_at_range <= 0:
            session_start += session_candles
            continue
        range_width_atr = range_width / atr_at_range

        if range_width_atr < min_range_atr or range_width_atr > max_range_atr:
            session_start += session_candles
            continue

        session_end = min(session_start + session_candles, len(candles))

        for i in range(range_end, session_end):
            c = candles[i]
            atr_i = atr_series[i] if i < len(atr_series) else 0.0
            if atr_i <= 0:
                continue

            if c["close"] > range_high:
                entry_price = c["close"]
                stop_loss = range_low - buffer_atr * atr_i
                risk_dist = entry_price - stop_loss
                if risk_dist <= 0:
                    continue
                take_profit = entry_price + risk_dist * rr_min

                signals.append(ORBSignal(
                    direction="LONG",
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    rr_ratio=rr_min,
                    range_high=range_high,
                    range_low=range_low,
                    range_width_atr=range_width_atr,
                    formed_at_idx=i,
                ))
                break  # one breakout per session

            elif c["close"] < range_low:
                entry_price = c["close"]
                stop_loss = range_high + buffer_atr * atr_i
                risk_dist = stop_loss - entry_price
                if risk_dist <= 0:
                    continue
                take_profit = entry_price - risk_dist * rr_min

                signals.append(ORBSignal(
                    direction="SHORT",
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    rr_ratio=rr_min,
                    range_high=range_high,
                    range_low=range_low,
                    range_width_atr=range_width_atr,
                    formed_at_idx=i,
                ))
                break  # one breakout per session

        session_start += session_candles

    return signals
