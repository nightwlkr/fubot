# Phase 2 — Step 4: 3-Candle / Liquidity Sweep Detection
## FUBOT: Candle 2 Wick Sweep + Candle 3 Confirmation Pattern

---

## Objective
Implement the 3-Candle / Liquidity Sweep pattern detection. This is mechanically similar to the FVG entry — both capture the same liquidity grab behavior. The 3-Candle pattern is more explicit about the wick sweep that triggers a "liquidity pool" stop hunt before the reversal.

---

## Context
- **What is 3-Candle**: Candle 1 establishes a range, Candle 2 sweeps beyond Candle 1's range (wick only), Candle 3 breaks out in the opposite direction confirming the reversal
- **Bullish 3-Candle**: C1 low is the range low; C2 sweeps below it (wick down), then closes back above C1 low; C3 breaks above C1 high → LONG entry
- **Bearish 3-Candle**: C1 high is the range high; C2 sweeps above it (wick up), then closes back below C1 high; C3 breaks below C1 low → SHORT entry
- **Key difference from FVG**: C2 sweeps with its **wick** past C1's range, but C2's **body** doesn't leave the range. The sweep is the "liquidity grab" where stop losses are collected before the reversal.
- **Timeframe**: 5m or 15m
- **Depends on**: Phase 2 Step 1

---

## Data Structure

```python
@dataclass
class ThreeCandle:
    symbol: str
    direction: str              # 'BULLISH' or 'BEARISH'
    interval: str
    # Candle 1 (the range candle)
    c1_high: float
    c1_low: float
    c1_open: float
    c1_close: float
    c1_time: datetime
    # Candle 2 (the sweep candle)
    c2_high: float
    c2_low: float
    c2_sweep_extreme: float     # The wick extreme that swept past C1
    c2_closed_in_range: bool    # C2 closed back inside C1's range
    c2_time: datetime
    # Candle 3 (the confirmation candle)
    c3_break_price: float       # Price that C3 broke to confirm direction
    c3_time: datetime
    # Entry zone
    entry_zone_min: float
    entry_zone_max: float
    entry_price: float          # Midpoint of C1 range
    stop_loss: float            # Beyond C2's sweep wick
    is_valid: bool              # All conditions met
    formed_at: datetime
```

---

## Algorithm

```
For each consecutive 3-candle window (C1, C2, C3):

Bullish 3-Candle:
  1. C1 is any candle — defines the initial range
  2. C2.wick_low < C1.low  ← C2 swept BELOW C1 low (wick only!)
  3. C2.close > C1.low     ← C2 CLOSED back inside C1 range (body not broken)
  4. C3.close > C1.high    ← C3 BROKE OUT above C1 high = CONFIRMATION
  5. C2_sweep_extreme = C2.low (the wick low)
  6. Entry price = C1.high (top of range to break)
  7. Stop loss = C2.low - 1 ATR (below the sweep wick)
  8. Take profit = entry + 3× SL distance

Bearish 3-Candle (mirror):
  1. C1 defines range
  2. C2.wick_high > C1.high ← C2 swept ABOVE C1 high
  3. C2.close < C1.high     ← C2 closed back inside range
  4. C3.close < C1.low     ← C3 broke out below C1 low
  5. Entry price = C1.low
  6. Stop loss = C2.high + 1 ATR
```

### Critical Distinction
- C2's **body** must stay inside C1's range — only the **wick** sweeps
- If C2's **close** goes past C1's range, it's not a 3-Candle — it's a breakout (BOS) scenario
- The sweep is a "liquidity grab" — stop orders clustered below support or above resistance get swept before the reversal

---

## Implementation: `backend/indicators/three_candle.py`

```python
async def detect_three_candles(candles: list[dict], symbol: str, interval: str) -> list[ThreeCandle]:
    """
    Detect all valid 3-Candle patterns in the candle list.
    candles: list of OHLCV dicts
    """
    patterns = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish 3-Candle
        # C2 wick swept below C1 low, C2 closed back inside C1 range, C3 broke above C1 high
        c2_swept_below = c2['low'] < c1['low']       # wick only
        c2_body_inside = c2['close'] >= c1['low'] and c2['close'] <= c1['high']  # body stayed in range
        c3_broke_above = c3['close'] > c1['high']     # confirmation

        if c2_swept_below and c2_body_inside and c3_broke_above:
            entry_price = c1['high']
            stop_loss = c2['low']  # At the sweep wick itself

            pattern = ThreeCandle(
                symbol=symbol,
                direction='BULLISH',
                interval=interval,
                c1_high=c1['high'], c1_low=c1['low'],
                c1_open=c1['open'], c1_close=c1['close'],
                c1_time=c1['close_time'],
                c2_high=c2['high'], c2_low=c2['low'],
                c2_sweep_extreme=c2['low'],
                c2_closed_in_range=True,
                c2_time=c2['close_time'],
                c3_break_price=c3['close'],
                c3_time=c3['close_time'],
                entry_zone_min=c1['low'],
                entry_zone_max=c1['high'],
                entry_price=entry_price,
                stop_loss=stop_loss,
                is_valid=True,
                formed_at=c3['close_time'],
            )
            patterns.append(pattern)

        # Bearish 3-Candle (mirror logic)
        c2_swept_above = c2['high'] > c1['high']
        c2_body_inside_bear = c2['close'] <= c1['high'] and c2['close'] >= c1['low']
        c3_broke_below = c3['close'] < c1['low']

        if c2_swept_above and c2_body_inside_bear and c3_broke_below:
            entry_price = c1['low']
            stop_loss = c2['high']

            pattern = ThreeCandle(
                symbol=symbol,
                direction='BEARISH',
                interval=interval,
                c1_high=c1['high'], c1_low=c1['low'],
                c1_open=c1['open'], c1_close=c1['close'],
                c1_time=c1['close_time'],
                c2_high=c2['high'], c2_low=c2['low'],
                c2_sweep_extreme=c2['high'],
                c2_closed_in_range=True,
                c2_time=c2['close_time'],
                c3_break_price=c3['close'],
                c3_time=c3['close_time'],
                entry_zone_min=c1['low'],
                entry_zone_max=c1['high'],
                entry_price=entry_price,
                stop_loss=stop_loss,
                is_valid=True,
                formed_at=c3['close_time'],
            )
            patterns.append(pattern)

    return patterns


def evaluate_three_candle_entry(
    pattern: ThreeCandle,
    atr: float,
    rr_min: float = 3.0
) -> tuple[float, float, float, float]:
    """
    Calculate entry price, stop loss, take profit, and R:R for a 3-Candle pattern.
    Returns: (entry_price, stop_loss, take_profit, rr_ratio)
    """
    entry = pattern.entry_price
    sl = pattern.stop_loss

    if pattern.direction == 'BULLISH':
        risk = entry - sl
        tp = entry + (risk * rr_min)
    else:  # BEARISH
        risk = sl - entry
        tp = entry - (risk * rr_min)

    rr = risk / abs(tp - entry) if (tp - entry) > 0 else 0
    return entry, sl, tp, rr
```

---

## Deliverables

### 1. `backend/indicators/three_candle.py`
- `ThreeCandle` dataclass
- `detect_three_candles()` — detect all valid 3-Candle patterns
- `evaluate_three_candle_entry()` — calculate entry, SL, TP, R:R

### 2. Update `backend/indicators/__init__.py`
Export all three_candle symbols.

### 3. Unit Tests `tests/backend/test_three_candle.py`
- Test bullish: C2 wick below C1 low, body closed inside, C3 broke above C1 high
- Test bearish: mirror
- Test that C2 body going past C1 range does NOT count as 3-Candle
- Test R:R calculation

### 4. Validation
```python
candles = [
    {'open': 100, 'high': 110, 'low': 100, 'close': 105, 'volume': 1000, 'close_time': 1},  # C1: range 100-110
    {'open': 105, 'high': 109, 'low': 98, 'close': 105, 'volume': 1100, 'close_time': 2},   # C2: wick 98 swept below 100, closed at 105 inside
    {'open': 105, 'high': 112, 'low': 104, 'close': 111, 'volume': 1300, 'close_time': 3}, # C3: broke above 110
]
patterns = asyncio.run(detect_three_candles(candles, "BTCUSDT", "5m"))
assert len(patterns) == 1
assert patterns[0].direction == 'BULLISH'
assert patterns[0].c2_sweep_extreme == 98.0  # wick low
```

---

## Dependencies
- **Requires**: Phase 2 Step 1

## Files Modified/Created
- `backend/indicators/three_candle.py`
- `backend/indicators/__init__.py` (updated)
- `tests/backend/test_three_candle.py`

## Next Step
Phase 2 Step 5: CHoCH + BOS Detection

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
