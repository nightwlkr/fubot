# Phase 2 — Step 5: CHoCH + BOS Detection
## FUBOT: Structural Break / Trend Shift Identification

---

## Objective
Implement Change of Character (CHoCH) and Break of Structure (BOS) detection. These structural concepts identify when the market's internal order has shifted — CHoCH signals a potential trend reversal, BOS confirms trend continuation.

---

## Context
- **CHoCH** (Change of Character): A break of the last higher low (bullish) or lower high (bearish) — signals momentum shift, often used for pullback entry timing
- **BOS** (Break of Structure): Price breaks a previous significant high/low — confirms the trend and is used to trail stop to BE
- **Implementation**: Based on swing pivot detection (zigzag-like high/low identification)
- **Timeframe**: 5m/15m for entries, 1H for trend
- **Depends on**: Phase 2 Step 1

---

## Part A: Pivot Detection (`backend/indicators/pivots.py`)

We need to identify swing highs and lows before we can detect CHoCH/BOS.

```python
@dataclass
class PivotPoint:
    type: str          # 'HIGH' or 'LOW'
    price: float
    index: int         # Candle index in the dataset
    timestamp: datetime

def find_pivot_highs_lows(
    candles: list[dict],
    left_bars: int = 5,
    right_bars: int = 5
) -> tuple[list[PivotPoint], list[PivotPoint]]:
    """
    Find all pivot highs and pivot lows in the candle list.

    Pivot High: high of this candle is higher than `left_bars` candles
                to the left AND `right_bars` candles to the right
    Pivot Low:  low of this candle is lower than `left_bars` candles
                to the left AND `right_bars` candles to the right

    Default: left_bars=5, right_bars=5 (swing high/low of ~11 candles)

    Returns: (list of pivot highs, list of pivot lows), sorted by index
    """
```

**Implementation**: Simple loop through candles. For each candle at index `i`, check if it's higher than all `left_bars` and `right_bars` neighbors.

---

## Part B: CHoCH Detection (`backend/indicators/choch.py`)

```python
@dataclass
class CHoCH:
    symbol: str
    direction: str           # 'BULLISH' or 'BEARISH'
    interval: str
    trigger_price: float      # Price level that was broken to trigger CHoCH
    pivot_price: float        # The pivot point that was broken
    formed_at: datetime
    index: int
    # For pullback entry calculation
    pullback_entry_zone: tuple[float, float]  # (zone_min, zone_max)
    stop_loss: float
    take_profit: float
    rr_ratio: float | None
    is_valid: bool

async def detect_choch(
    candles: list[dict],
    symbol: str,
    interval: str,
    atr: float,
    rr_min: float = 3.0,
    left_bars: int = 5,
    right_bars: int = 5
) -> list[CHoCH]:
    """
    Detect all CHoCH patterns in the candle data.

    Bullish CHoCH (trend shifting from down to up):
      1. Identify last two pivot lows: L1 (older), L2 (recent)
      2. Identify last two pivot highs: H1 (older), H2 (recent)
      3. The market is in a bearish structure: H2 < H1 (lower highs)
      4. L2 breaks above H1 → bullish CHoCH (change of character)
         This means: the buyers are now making higher highs,
         breaking the downtrend structure

    Bearish CHoCH (trend shifting from up to down):
      1. Market in bullish structure: L2 > L1 (higher lows)
      2. H2 breaks below L1 → bearish CHoCH

    After CHoCH detected:
      - Entry zone: 0.5-0.618 Fibonacci retracement of the CHoCH impulse leg
      - Stop: beyond the pivot that was broken
      - TP: structural level, minimum 3:1 R:R
    """
```

### CHoCH Detection Algorithm

```
1. Find pivot highs and pivot lows
2. Get the last 4 pivots (alternating: high, low, high, low or low, high, low, high)

For bullish CHoCH:
  - Need pattern: Higher Low breaks a previous Lower High
  - recent_low > older_low  ← still making higher lows (not bearish)
  - recent_low > previous_high_that_was_lower  ← this is the CHoCH trigger
  - Trigger: price breaks above the last lower high, confirming buyers in control

For bearish CHoCH:
  - recent_high < older_high  ← still making lower highs (not bullish)
  - recent_high < previous_low_that_was_higher  ← this is the CHoCH trigger
```

### Fibonacci Pullback Entry
```python
def calculate_fib_entry(impulse_low: float, impulse_high: float, direction: str) -> float:
    """
    Calculate 50% and 61.8% Fibonacci retracement levels for pullback entry.
    Fibonacci levels: 0.5, 0.618
    """
    impulse_range = abs(impulse_high - impulse_low)
    fib_50 = impulse_low + impulse_range * 0.5
    fib_618 = impulse_low + impulse_range * 0.618
    return (fib_50 + fib_618) / 2  # midpoint of fib zone as entry
```

---

## Part C: BOS Detection (`backend/indicators/bos.py`)

```python
@dataclass
class BOS:
    symbol: str
    direction: str           # 'BULLISH' or 'BEARISH'
    interval: str
    broken_level: float       # The high/low that was broken
    confirmation_price: float # Price that confirmed the BOS
    formed_at: datetime
    index: int

async def detect_bos(
    candles: list[dict],
    symbol: str,
    interval: str,
    left_bars: int = 5,
    right_bars: int = 5
) -> list[BOS]:
    """
    Detect Break of Structure.

    Bullish BOS: Price breaks above a previous pivot high,
                 confirming the uptrend is still intact.
    Bearish BOS: Price breaks below a previous pivot low,
                 confirming the downtrend is still intact.

    BOS is used for:
      1. Confirming trend direction
      2. Triggering BE trailing (move SL to BE after new BOS)
    """
```

---

## Part D: Combined Structural Analysis (`backend/indicators/structural.py`)

```python
@dataclass
class StructuralAnalysis:
    trend: str               # 'UPTREND', 'DOWNTREND', 'RANGING'
    last_choch: CHoCH | None
    last_bos: BOS | None
    current_swing_high: float
    current_swing_low: float
    pivot_highs: list[PivotPoint]
    pivot_lows: list[PivotPoint]
    timestamp: datetime

async def analyze_structure(
    candles: list[dict],
    symbol: str,
    interval: str,
    atr: float,
    left_bars: int = 5,
    right_bars: int = 5
) -> StructuralAnalysis:
    """
    Full structural analysis combining CHoCH + BOS + trend detection.
    """
```

---

## Deliverables

### 1. `backend/indicators/pivots.py`
- `PivotPoint` dataclass
- `find_pivot_highs_lows()` — detect all swing points

### 2. `backend/indicators/choch.py`
- `CHoCH` dataclass
- `detect_choch()` — detect all CHoCH patterns
- `calculate_fib_entry()` — Fibonacci pullback entry zone

### 3. `backend/indicators/bos.py`
- `BOS` dataclass
- `detect_bos()` — detect all BOS patterns

### 4. `backend/indicators/structural.py`
- `StructuralAnalysis` dataclass
- `analyze_structure()` — combined structural assessment

### 5. Update `backend/indicators/__init__.py`
Export all new symbols.

### 6. Unit Tests `tests/backend/test_structural.py`
- Test pivot detection with known candle sequences
- Test bullish CHoCH detection
- Test bearish CHoCH detection
- Test bullish BOS detection
- Test that non-alternating pivots don't trigger false signals

### 7. Validation
```python
# Test CHoCH detection
candles = [...]  # 50 candles with known pivot structure
struct = asyncio.run(analyze_structure(candles, "BTCUSDT", "5m", atr=50.0))
print(struct.trend)  # UPTREND or DOWNTREND
print(struct.last_choch)  # CHoCH object or None
print(struct.last_bos)  # BOS object or None
```

---

## Dependencies
- **Requires**: Phase 2 Step 1

## Files Modified/Created
- `backend/indicators/pivots.py`
- `backend/indicators/choch.py`
- `backend/indicators/bos.py`
- `backend/indicators/structural.py`
- `backend/indicators/__init__.py` (updated)
- `tests/backend/test_structural.py`

## Next Step
Phase 2 Step 6: Volume Bias Analysis

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
