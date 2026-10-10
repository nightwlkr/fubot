# Phase 2 — Step 2: Trend Indicators — 50 SMA + RSI
## FUBOT: Higher-Timeframe Trend Bias Detection

---

## Objective
Implement the 50-period SMA and RSI indicator modules. These determine the **trend bias** on the 1H timeframe — the most critical filter before any entry signal is taken.

---

## Context
- **Trend bias**: Used to filter entries (only trade in direction of bias 80% of time)
- **Timeframe**: 1H for trend bias
- **SMA**: 50-period Simple Moving Average
- **RSI**: 14-period Relative Strength Index
- **Depends on**: Phase 2 Step 1 (market data service)

---

## Part A: SMA Module (`backend/indicators/sma.py`)

```python
async def calculate_sma(closes: list[float], period: int = 50) -> float | None:
    """
    Calculate SMA from a list of close prices.
    Returns None if not enough data.
    """
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def get_sma_multi(closes: list[float], periods: list[int]) -> dict[int, float | None]:
    """
    Calculate multiple SMAs at once.
    Returns: {period: sma_value}
    """
    return {p: calculate_sma(closes, p) for p in periods}


def get_trend_bias(sma_50: float, current_price: float, sma_200: float | None = None) -> str:
    """
    Determine trend bias based on SMA.
    Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'

    Rules:
    - sma_50 > current_price → BEARISH (price below average = bearish)
    - sma_50 < current_price → BULLISH (price above average = bullish)
    - Within 0.1% → NEUTRAL
    """
```

### Implementation Notes
- Use `pandas` for efficient SMA calculation: `pd.Series(closes).rolling(50).mean().iloc[-1]`
- Handle edge cases: exactly 50 bars returns valid SMA, 49 returns None
- Return `float` not `numpy.float64` (cast to Python float)

---

## Part B: RSI Module (`backend/indicators/rsi.py`)

```python
async def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    """
    Calculate RSI from close prices using the standard Wilder method.
    Returns value 0-100.
    Returns None if not enough data.
    """
    if len(closes) < period + 1:
        return None

    deltas = pd.Series(closes).diff()
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)

    # Wilder's smoothed averages
    avg_gain = gains.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    avg_loss = losses.ewm(alpha=1/period, adjust=False).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi)


def get_rsi_zone(rsi: float) -> str:
    """
    Classify RSI into zone.
    Returns: 'OVERBOUGHT', 'OVERSOLD', or 'NEUTRAL'

    Rules:
    - RSI > 70 → OVERBOUGHT
    - RSI < 30 → OVERSOLD
    - 30 ≤ RSI ≤ 70 → NEUTRAL
    """
```

---

## Part C: Trend Bias Service (`backend/indicators/trend.py`)

Combine SMA + RSI for a complete trend bias assessment:

```python
@dataclass
class TrendBias:
    direction: str          # 'BULLISH', 'BEARISH', 'NEUTRAL'
    strength: str            # 'STRONG', 'MODERATE', 'WEAK'
    sma_50: float
    sma_200: float | None
    rsi: float
    rsi_zone: str           # 'OVERBOUGHT', 'OVERSOLD', 'NEUTRAL'
    bias_score: int          # -100 to +100 (composite score)
    timestamp: datetime

async def analyze_trend_bias(symbol: str, interval: str = "1h") -> TrendBias:
    """
    Fetch klines, calculate SMA + RSI, return composite trend bias.

    Bias Score Calculation:
    - sma_bullish: +40, sma_bearish: -40
    - rsi_oversold: +20, rsi_overbought: -20
    - rsi_neutral: 0
    - strength: abs(score)
      - >= 50: STRONG
      - 25-49: MODERATE
      - < 25: WEAK
    """
```

**Example `TrendBias` output:**
```python
TrendBias(
    direction='BULLISH',
    strength='STRONG',
    sma_50=62500.0,
    sma_200=61000.0,
    rsi=28.5,
    rsi_zone='OVERSOLD',
    bias_score=60,
    timestamp=datetime(2026, 10, 10, 12, 0, 0)
)
```

---

## Part D: Signal Filter Integration

These indicators feed into `SignalService` (Phase 2 Step 7) to filter entries:

```python
def should_trade_with_bias(trend_bias: TrendBias, entry_direction: str) -> tuple[bool, str]:
    """
    Determines if an entry aligns with trend bias.
    Returns: (should_trade: bool, reason: str)

    Rules:
    - Trend BULLISH + entry LONG → YES (aligned)
    - Trend BEARISH + entry SHORT → YES (aligned)
    - Trend BULLISH + entry SHORT → SCALP ONLY (oppose bias, tight stop only)
    - Trend BEARISH + entry LONG → SCALP ONLY
    - RSI OVERBOUGHT + LONG entry → NO (overbought + long = against momentum)
    - RSI OVERSOLD + SHORT entry → NO
    - Trend NEUTRAL → SCALP ONLY regardless of direction
    """
```

---

## Deliverables

### 1. `backend/indicators/sma.py`
- `calculate_sma()`, `get_sma_multi()`, `get_trend_bias()`

### 2. `backend/indicators/rsi.py`
- `calculate_rsi()`, `get_rsi_zone()`

### 3. `backend/indicators/trend.py`
- `TrendBias` dataclass
- `analyze_trend_bias()`

### 4. `backend/indicators/__init__.py`
Export all indicator functions.

### 5. Unit Tests `tests/backend/test_indicators.py`
- Test SMA with known values
- Test RSI with known values (verify against standard RSI formula)
- Test `get_trend_bias` with mock data
- Test `should_trade_with_bias` with all 8 combinations

### 6. Validation
```python
# Test with real data
import asyncio
from backend.indicators import analyze_trend_bias

bias = asyncio.run(analyze_trend_bias("BTCUSDT", "1h"))
print(bias.direction)  # Should print BULLISH, BEARISH, or NEUTRAL
print(bias.bias_score) # Should be int between -100 and 100
```

---

## Dependencies
- **Requires**: Phase 2 Step 1 (market data)

## Files Modified/Created
- `backend/indicators/sma.py`
- `backend/indicators/rsi.py`
- `backend/indicators/trend.py`
- `backend/indicators/__init__.py`
- `tests/backend/test_indicators.py`

## Next Step
Phase 2 Step 3: FVG (Fair Value Gap) Detection

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
