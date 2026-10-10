# Phase 2 — Step 6: Volume Bias Analysis
## FUBOT: Buy/Sell Volume Dominance Filter

---

## Objective
Implement volume analysis to determine which side (buyers or sellers) is dominating price action. This acts as a **confirmation filter** — any breakout signal must have volume confirming the direction before the bot enters.

---

## Context
- **Concept**: A candle's volume can be split into "buy volume" and "sell volume" based on where the close is within the candle's high-low range
  - Close near high → most of the volume was buy volume
  - Close near low → most of the volume was sell volume
- **Purpose**: Confirm breakout direction — if price breaks up but volume shows sell-side dominance, the breakout is weaker
- **Usage**: Applied as a filter in `SignalService` alongside FVG, 3-Candle, and CHoCH signals
- **Depends on**: Phase 2 Step 1

---

## Algorithm

For each candle, estimate buy/sell volume split:

```python
def estimate_buy_sell_volume(candle: dict) -> tuple[float, float]:
    """
    Split a candle's total volume into estimated buy and sell volume.

    Method: Position of close within the high-low range.

    close_position = (close - low) / (high - low)
    - close_position near 1.0 → close at high → most volume was buying
    - close_position near 0.0 → close at low → most volume was selling
    - close_position near 0.5 → close in middle → roughly even

    buy_volume = total_volume * close_position
    sell_volume = total_volume * (1 - close_position)
    """
    high = candle['high']
    low = candle['low']
    close = candle['close']
    volume = candle['volume']

    if high == low:
        # Doji — no range, assume 50/50
        return volume * 0.5, volume * 0.5

    close_position = (close - low) / (high - low)
    close_position = max(0.0, min(1.0, close_position))  # clamp to [0, 1]

    buy_volume = volume * close_position
    sell_volume = volume * (1.0 - close_position)

    return buy_volume, sell_volume
```

---

## Volume Bias for a Zone / Candle Sequence

```python
@dataclass
class VolumeBias:
    total_buy_volume: float
    total_sell_volume: float
    dominant_side: str         # 'BUY', 'SELL', or 'NEUTRAL'
    buy_ratio: float            # buy / (buy + sell), 0.0 to 1.0
    sell_ratio: float           # sell / (buy + sell), 0.0 to 1.0
    is_confirmed: bool          # True if buy_ratio >= 0.55 or sell_ratio >= 0.55

async def analyze_zone_volume(candles: list[dict]) -> VolumeBias:
    """
    Sum buy/sell volume across a series of candles (e.g., inside an FVG zone).
    Returns dominant side and confirmation status.
    """
    total_buy = 0.0
    total_sell = 0.0

    for candle in candles:
        buy, sell = estimate_buy_sell_volume(candle)
        total_buy += buy
        total_sell += sell

    total = total_buy + total_sell
    if total == 0:
        return VolumeBias(0, 0, 'NEUTRAL', 0.5, 0.5, False)

    buy_ratio = total_buy / total
    sell_ratio = total_sell / total

    if buy_ratio >= 0.55:
        dominant = 'BUY'
        confirmed = True
    elif sell_ratio >= 0.55:
        dominant = 'SELL'
        confirmed = True
    else:
        dominant = 'NEUTRAL'
        confirmed = False

    return VolumeBias(
        total_buy_volume=total_buy,
        total_sell_volume=total_sell,
        dominant_side=dominant,
        buy_ratio=buy_ratio,
        sell_ratio=sell_ratio,
        is_confirmed=confirmed,
    )
```

---

## Volume-Confirmed Breakout Filter

When evaluating any entry signal (FVG, 3-Candle, CHoCH):

```python
@dataclass
class VolumeConfirmedSignal:
    signal_type: str        # 'FVG', '3CANDLE', 'CHOC'
    direction: str
    volume_bias: VolumeBias
    passes_filter: bool     # True only if volume confirms direction

def filter_by_volume(
    signal_direction: str,
    candles_in_zone: list[dict]
) -> VolumeConfirmedSignal:
    """
    Check if volume confirms the signal direction.

    Rules:
    - Bullish signal (LONG) → volume_bias.dominant_side must be 'BUY' and is_confirmed=True
    - Bearish signal (SHORT) → volume_bias.dominant_side must be 'SELL' and is_confirmed=True
    - Otherwise → filter fails, signal is skipped
    """
    bias = asyncio.run(analyze_zone_volume(candles_in_zone))

    passes = (
        (signal_direction == 'LONG' and bias.dominant_side == 'BUY' and bias.is_confirmed) or
        (signal_direction == 'SHORT' and bias.dominant_side == 'SELL' and bias.is_confirmed)
    )

    return VolumeConfirmedSignal(
        signal_type=signal_type,
        direction=signal_direction,
        volume_bias=bias,
        passes_filter=passes,
    )
```

---

## Implementation: `backend/indicators/volume_profile.py`

```python
# File: backend/indicators/volume_profile.py
async def estimate_buy_sell_volume(candle: dict) -> tuple[float, float]
async def analyze_zone_volume(candles: list[dict]) -> VolumeBias
def get_volume_confirmed_filter(signal_direction: str, candles: list[dict]) -> VolumeConfirmedSignal
```

---

## ATR Implementation (`backend/indicators/atr.py`)

Also needed by several strategies for stop sizing:

```python
async def calculate_atr(candles: list[dict], period: int = 14) -> float:
    """
    Calculate Average True Range.
    ATR = Wilder's smoothed True Range over `period` candles.

    True Range = max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )
    """
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_close = candles[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    # Wilder's smoothed ATR
    atr = pd.Series(trs).ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    return float(atr)
```

---

## Deliverables

### 1. `backend/indicators/volume_profile.py`
- `estimate_buy_sell_volume()`
- `analyze_zone_volume()`
- `VolumeBias` dataclass
- `VolumeConfirmedSignal` dataclass
- `get_volume_confirmed_filter()`

### 2. `backend/indicators/atr.py`
- `calculate_atr()`

### 3. Update `backend/indicators/__init__.py`
Export all volume and ATR symbols.

### 4. Unit Tests `tests/backend/test_volume_profile.py`
- Test buy/sell split when close is at high (should be ~100% buy)
- Test buy/sell split when close is at low (should be ~100% sell)
- Test buy/sell split when close is mid-range (should be ~50/50)
- Test `analyze_zone_volume` with multiple candles
- Test `filter_by_volume` for bullish + buy-dominant → passes
- Test `filter_by_volume` for bullish + sell-dominant → fails

### 5. Validation
```python
# Close at high → 100% buy volume
candle = {'high': 100, 'low': 90, 'close': 100, 'volume': 1000}
buy, sell = estimate_buy_sell_volume(candle)
assert buy == 1000.0
assert sell == 0.0

# Close at low → 100% sell volume
candle = {'high': 100, 'low': 90, 'close': 90, 'volume': 1000}
buy, sell = estimate_buy_sell_volume(candle)
assert buy == 0.0
assert sell == 1000.0
```

---

## Dependencies
- **Requires**: Phase 2 Step 1

## Files Modified/Created
- `backend/indicators/volume_profile.py`
- `backend/indicators/atr.py`
- `backend/indicators/__init__.py` (updated)
- `tests/backend/test_volume_profile.py`

## Next Step
Phase 2 Step 7: Signal Service — Orchestration

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
