# Phase 2 — Step 3: FVG Detection
## FUBOT: Fair Value Gap Identification

---

## Objective
Implement the Fair Value Gap (FVG) detection indicator. FVGs are the primary entry trigger for the bot — they represent zones where price gapped and market made a decision, creating an imbalance that price typically returns to fill.

---

## Context
- **What is FVG**: Three-candle imbalance where the middle candle's body does not overlap either adjacent candle's body. The gap zone is the "fair value" area price left.
- **Bullish FVG**: Candle 1 (bearish), Candle 2 (bullish, gaps up from Candle 1's body), Candle 3 (bullish, retraces into the gap)
- **Bearish FVG**: Candle 1 (bullish), Candle 2 (bearish, gaps down from Candle 1's body), Candle 3 (bearish, retraces into the gap)
- **Timeframe**: 5m or 15m for entry signals
- **Depends on**: Phase 2 Step 1

---

## Data Structure

```python
@dataclass
class FVG:
    symbol: str
    direction: str              # 'BULLISH' or 'BEARISH'
    interval: str              # '5m', '15m', etc.
    zone_min: float            # Bottom of the FVG zone
    zone_max: float            # Top of the FVG zone
    midpoint: float            # (zone_min + zone_max) / 2
    impulse_candle_high: float # High of the impulse candle (candle 2)
    impulse_candle_low: float  # Low of the impulse candle (candle 2)
    formed_at: datetime        # When the FVG was identified
    bar_index: int             # Index of candle 2 in the dataset
    filled: bool = False       # Has price filled this FVG yet?
    fill_price: float | None = None
    fill_time: datetime | None = None
```

---

## Algorithm

```
For each consecutive 3-candle window (candle[i-2], candle[i-1], candle[i]):

Bullish FVG (FVG forms after bullish impulse):
  1. candle[i-2] is bearish (close < open)
  2. candle[i-1] is bullish (close > open) AND
     candle[i-1].low > candle[i-2].high  ← GAP UP (no overlap)
  3. candle[i].close > candle[i-1].high  ← price confirmed upward

  FVG Zone (numeric bounds — zone_min < zone_max always):
    zone_min = candle[i-2].high  ← gap bottom (top of the bearish candle)
    zone_max = candle[i-1].low   ← gap top (bottom of the bullish candle)

Bearish FVG (FVG forms after bearish impulse):
  1. candle[i-2] is bullish (close > open)
  2. candle[i-1] is bearish (close < open) AND
     candle[i-1].high < candle[i-2].low  ← GAP DOWN (no overlap)
  3. candle[i].close < candle[i-1].low  ← price confirmed downward

  FVG Zone (numeric bounds — zone_min < zone_max always):
    zone_min = candle[i-1].high  ← gap top (bottom of the bearish candle)
    zone_max = candle[i-2].low   ← gap bottom (low of the bullish candle)
```

### Important Details
- Use **body** (open/close) for gap detection, not high/low
- The gap must be a pure body gap — wick overlaps don't count
- FVGs formed by candle 2's body vs candle 1's body
- FVGs can overlap; each valid FVG is reported separately
- FVG zone is the gap between the two non-overlapping bodies

---

## Implementation: `backend/indicators/fvg.py`

```python
async def detect_fvg(candles: list[dict]) -> list[FVG]:
    """
    Detect all FVGs in a list of candles.
    candles: list of OHLCV dicts with keys: open, high, low, close, volume
    Returns: list of FVG objects, oldest first
    """
    fvgs = []
    for i in range(2, len(candles)):
        c1 = candles[i - 2]  # candle -2
        c2 = candles[i - 1]  # candle -1 (impulse candle)
        c3 = candles[i]       # candle (confirmation)

        # Bullish FVG
        if (c1['close'] < c1['open'] and          # c1 bearish
            c2['close'] > c2['open'] and          # c2 bullish
            c2['low'] > c1['high'] and             # gap up
            c3['close'] > c2['high']):              # confirmed

            fvg = FVG(
                symbol=symbol,
                direction='BULLISH',
                zone_min=c1['high'],   # gap bottom
                zone_max=c2['low'],    # gap top
                midpoint=(c2['low'] + c1['high']) / 2,
                impulse_candle_high=c2['high'],
                impulse_candle_low=c2['low'],
                formed_at=candles[i-1]['close_time'],
                bar_index=i-1,
            )
            fvgs.append(fvg)

        # Bearish FVG (mirror logic)
        elif (c1['close'] > c1['open'] and         # c1 bullish
              c2['close'] < c2['open'] and         # c2 bearish
              c2['high'] < c1['low'] and           # gap down
              c3['close'] < c2['low']):             # confirmed

            fvg = FVG(
                symbol=symbol,
                direction='BEARISH',
                zone_min=c2['high'],   # gap top
                zone_max=c1['low'],    # gap bottom
                midpoint=(c1['low'] + c2['high']) / 2,
                impulse_candle_high=c2['high'],
                impulse_candle_low=c2['low'],
                formed_at=candles[i-1]['close_time'],
                bar_index=i-1,
            )
            fvgs.append(fvg)

    return fvgs


def check_fvg_filled(fvg: FVG, current_price: float, high: float, low: float) -> bool:
    """
    Check if price has traded back into the FVG zone (mitigated it).
    A bullish FVG sits below price — filled when price returns DOWN into the
    gap (low <= zone_max). A bearish FVG sits above price — filled when price
    returns UP into the gap (high >= zone_min).
    """
    if fvg.direction == 'BULLISH':
        return low <= fvg.zone_max  # price returned into the gap from above
    else:
        return high >= fvg.zone_min  # price returned into the gap from below


def filter_valid_fvgs(fvgs: list[FVG], max_age_bars: int = 20) -> list[FVG]:
    """
    Filter out FVGs that are too old (price already moved past them).
    Keep only FVGs formed within the last `max_age_bars` candles.
    """
    return fvgs  # For now, return all; implement age filter if needed
```

---

## Entry Order Placement Logic

When the bot detects an FVG and decides to trade it:

```python
@dataclass
class FVGEntry:
    fvg: FVG
    entry_price: float        # FVG midpoint (limit order price)
    stop_loss: float          # Beyond the FVG extreme (1 ATR beyond)
    take_profit: float        # At least 3× SL distance
    rr_ratio: float           # Calculated R:R
    is_valid: bool             # R:R >= minimum threshold

def evaluate_fvg_entry(fvg: FVG, atr: float, rr_min: float = 3.0) -> FVGEntry:
    """
    Given an FVG and current ATR, calculate entry, SL, TP, and R:R.
    """
    entry_price = fvg.midpoint

    if fvg.direction == 'BULLISH':
        stop_loss = fvg.zone_min - atr
        risk_distance = entry_price - stop_loss
        take_profit = entry_price + (risk_distance * rr_min)
    else:  # BEARISH
        stop_loss = fvg.zone_max + atr
        risk_distance = stop_loss - entry_price
        take_profit = entry_price - (risk_distance * rr_min)

    rr_ratio = abs(take_profit - entry_price) / risk_distance if risk_distance > 0 else 0  # reward / risk

    return FVGEntry(
        fvg=fvg,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        rr_ratio=rr_ratio,
        is_valid=rr_ratio >= rr_min,
    )
```

---

## Deliverables

### 1. `backend/indicators/fvg.py`
- `FVG` dataclass
- `detect_fvg()` — detect all FVGs in candle list
- `check_fvg_filled()` — has price returned to fill the gap?
- `evaluate_fvg_entry()` — calculate entry price, SL, TP, R:R

### 2. Update `backend/indicators/__init__.py`
Export `FVG`, `FVGEntry`, `detect_fvg`, `evaluate_fvg_entry`

### 3. Unit Tests `tests/backend/test_fvg.py`
- Test bullish FVG detection with known candle sequences
- Test bearish FVG detection
- Test that wick-only overlap does NOT create FVG
- Test `evaluate_fvg_entry` R:R calculation
- Test `check_fvg_filled`

### 4. Validation
```python
# Create a known bullish FVG pattern
candles = [
    {'open': 100, 'high': 105, 'low': 99, 'close': 100, 'volume': 1000, 'close_time': 1},
    {'open': 106, 'high': 108, 'low': 106, 'close': 107.5, 'volume': 1200, 'close_time': 2},  # impulse (gap up: c2.low=106 > c1.high=105)
    {'open': 107.5, 'high': 110, 'low': 107, 'close': 109, 'volume': 1500, 'close_time': 3},  # confirmation (close 109 > c2.high=108)
]
# Gap zone spans [c1.high=105, c2.low=106] → numeric bounds zone_min=105, zone_max=106
fvgs = asyncio.run(detect_fvg(candles, "BTCUSDT"))
assert len(fvgs) == 1
assert fvgs[0].direction == 'BULLISH'
assert fvgs[0].zone_min == 105
assert fvgs[0].zone_max == 106
assert fvgs[0].midpoint == 105.5
```

---

## Dependencies
- **Requires**: Phase 2 Step 1 (market data)

## Files Modified/Created
- `backend/indicators/fvg.py`
- `backend/indicators/__init__.py` (updated)
- `tests/backend/test_fvg.py`

## Next Step
Phase 2 Step 4: 3-Candle / Liquidity Sweep Detection

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
