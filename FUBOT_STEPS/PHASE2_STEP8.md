# Phase 2 — Step 8: Unit Tests for All Indicators
## FUBOT: Comprehensive Indicator Test Suite

---

## Objective
Write comprehensive unit tests covering all indicator modules (SMA, RSI, FVG, 3-Candle, CHoCH, BOS, ATR, Volume Profile) to ensure correctness before live trading.

---

## Context
- **Framework**: pytest + pytest-asyncio
- **Mocking**: Use hardcoded candle data — no external API calls in tests
- **Coverage target**: All indicator functions, edge cases, boundary conditions
- **Depends on**: Phase 2 Steps 1-6

---

## Test File Structure

```
tests/
└── backend/
    ├── __init__.py
    ├── conftest.py               # Shared fixtures
    ├── test_sma.py              # Phase 2 Step 2
    ├── test_rsi.py              # Phase 2 Step 2
    ├── test_fvg.py              # Phase 2 Step 3
    ├── test_three_candle.py     # Phase 2 Step 4
    ├── test_pivots.py           # Phase 2 Step 5
    ├── test_choch.py            # Phase 2 Step 5
    ├── test_bos.py              # Phase 2 Step 5
    ├── test_atr.py              # Phase 2 Step 6
    ├── test_volume_profile.py   # Phase 2 Step 6
    ├── test_trend.py            # Phase 2 Step 2
    └── test_indicator_integration.py  # All indicators together
```

---

## Shared Fixtures: `tests/backend/conftest.py`

```python
import pytest
import asyncio
from datetime import datetime

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def sample_candle():
    """Returns a standard BTC-like candle."""
    return {
        'open': 62500.0,
        'high': 62800.0,
        'low': 62200.0,
        'close': 62700.0,
        'volume': 500.0,
        'close_time': datetime.now()
    }

@pytest.fixture
def trending_up_candles():
    """
    50 candles in a clear uptrend: higher highs, higher lows.
    """
    candles = []
    for i in range(50):
        base = 60000 + i * 100
        candles.append({
            'open': base,
            'high': base + 150,
            'low': base - 80,
            'close': base + 120,
            'volume': 200 + i * 5,
            'close_time': datetime.fromtimestamp(1700000000 + i * 300)
        })
    return candles

@pytest.fixture
def trending_down_candles():
    """50 candles in a clear downtrend."""
    candles = []
    for i in range(50):
        base = 64000 - i * 100
        candles.append({
            'open': base,
            'high': base + 80,
            'low': base - 150,
            'close': base - 120,
            'volume': 200 + i * 5,
            'close_time': datetime.fromtimestamp(1700000000 + i * 300)
        })
    return candles

@pytest.fixture
def fvg_bullish_candles():
    """
    A clear bullish FVG:
    - Candle -2: bearish (gap zone upper bound)
    - Candle -1: bullish impulse (gap zone lower bound)
    - Candle 0: bullish confirmation
    """
    return [
        {'open': 62200, 'high': 62500, 'low': 62100, 'close': 62200, 'volume': 500, 'close_time': 1},
        {'open': 62250, 'high': 62450, 'low': 62600, 'close': 62600, 'volume': 600, 'close_time': 2},  # gap up from 62500
        {'open': 62600, 'high': 63000, 'low': 62550, 'close': 62900, 'volume': 700, 'close_time': 3},  # confirmed
    ]

@pytest.fixture
def three_candle_bullish_candles():
    """
    A clear bullish 3-Candle:
    - C1: range 62000-63000
    - C2: wick sweeps to 61800, closes back at 62500 (inside C1 range)
    - C3: breaks above 63000
    """
    return [
        {'open': 62500, 'high': 63000, 'low': 62000, 'close': 62500, 'volume': 500, 'close_time': 1},
        {'open': 62500, 'high': 62900, 'low': 61800, 'close': 62500, 'volume': 550, 'close_time': 2},  # wick swept 61800
        {'open': 62500, 'high': 63200, 'low': 62400, 'close': 63100, 'volume': 700, 'close_time': 3},  # broke above 63000
    ]
```

---

## Test Cases by Module

### `test_sma.py`
```python
def test_sma_50_exact():
    """SMA of 50 equal values should equal that value."""
    closes = [100.0] * 50
    result = calculate_sma(closes, 50)
    assert result == 100.0

def test_sma_49_returns_none():
    """SMA with insufficient data returns None."""
    closes = [100.0] * 49
    result = calculate_sma(closes, 50)
    assert result is None

def test_sma_50_returns_value():
    closes = [100.0] * 49 + [200.0]
    result = calculate_sma(closes, 50)
    assert result == pytest.approx(102.0)  # (99*100 + 200) / 50

def test_get_trend_bias_bullish():
    assert get_trend_bias(sma_50=62500, current_price=63000) == 'BULLISH'

def test_get_trend_bias_bearish():
    assert get_trend_bias(sma_50=63000, current_price=62500) == 'BEARISH'

def test_get_trend_bias_neutral():
    # Within 0.1%
    assert get_trend_bias(sma_50=62500, current_price=62562) == 'NEUTRAL'
```

### `test_rsi.py`
```python
def test_rsi_100_when_all_up():
    """If price goes up every period, RSI = 100."""
    closes = [100 + i for i in range(15)]
    result = calculate_rsi(closes, 14)
    assert result == 100.0

def test_rsi_0_when_all_down():
    closes = [100 - i for i in range(15)]
    result = calculate_rsi(closes, 14)
    assert result == 0.0

def test_rsi_zone_overbought():
    assert get_rsi_zone(75.0) == 'OVERBOUGHT'
    assert get_rsi_zone(100.0) == 'OVERBOUGHT'

def test_rsi_zone_oversold():
    assert get_rsi_zone(25.0) == 'OVERSOLD'
    assert get_rsi_zone(0.0) == 'OVERSOLD'

def test_rsi_zone_neutral():
    assert get_rsi_zone(50.0) == 'NEUTRAL'
    assert get_rsi_zone(30.0) == 'NEUTRAL'
    assert get_rsi_zone(70.0) == 'NEUTRAL'
```

### `test_fvg.py`
```python
def test_bullish_fvg_detected(fvg_bullish_candles):
    fvgs = asyncio.run(detect_fvg(fvg_bullish_candles, "BTCUSDT"))
    assert len(fvgs) == 1
    assert fvgs[0].direction == 'BULLISH'
    assert fvgs[0].zone_min == 62600  # c2.low
    assert fvgs[0].zone_max == 62500  # c1.high

def test_bearish_fvg_detected():
    candles = [
        {'open': 62500, 'high': 62600, 'low': 62100, 'close': 62100, 'volume': 500, 'close_time': 1},
        {'open': 62100, 'high': 61900, 'low': 62000, 'close': 61950, 'volume': 600, 'close_time': 2},  # gap down
        {'open': 61950, 'high': 62050, 'low': 61800, 'close': 61850, 'volume': 700, 'close_time': 3},  # confirmed
    ]
    fvgs = asyncio.run(detect_fvg(candles, "BTCUSDT"))
    assert len(fvgs) == 1
    assert fvgs[0].direction == 'BEARISH'

def test_no_fvg_when_bodies_overlap():
    """Wick-only gap should not create FVG."""
    candles = [
        {'open': 62000, 'high': 62500, 'low': 61900, 'close': 62100, 'volume': 500, 'close_time': 1},
        {'open': 62200, 'high': 62600, 'low': 62100, 'close': 62500, 'volume': 600, 'close_time': 2},  # c2 body overlaps c1 body
        {'open': 62500, 'high': 62700, 'low': 62400, 'close': 62600, 'volume': 700, 'close_time': 3},
    ]
    fvgs = asyncio.run(detect_fvg(candles, "BTCUSDT"))
    assert len(fvgs) == 0

def test_fvg_rr_calculation():
    fvgs = asyncio.run(detect_fvg(fvg_bullish_candles, "BTCUSDT"))
    entry = evaluate_fvg_entry(fvgs[0], atr=100.0, rr_min=3.0)
    assert entry.rr_ratio == 3.0  # ATR matches 3:1 exactly in this case
    assert entry.is_valid is True
```

### `test_three_candle.py`
```python
def test_bullish_3candle_detected(three_candle_bullish_candles):
    patterns = asyncio.run(detect_three_candles(three_candle_bullish_candles, "BTCUSDT", "5m"))
    assert len(patterns) == 1
    assert patterns[0].direction == 'BULLISH'
    assert patterns[0].c2_sweep_extreme == 61800.0

def test_body_breakout_is_not_3candle():
    """If C2's body breaks range, it's not a 3-Candle."""
    candles = [
        {'open': 62500, 'high': 63000, 'low': 62000, 'close': 62500, 'volume': 500, 'close_time': 1},
        {'open': 62500, 'high': 63200, 'low': 61900, 'close': 63100, 'volume': 600, 'close_time': 2},  # body broke above 63000!
        {'open': 63100, 'high': 63400, 'low': 63000, 'close': 63300, 'volume': 700, 'close_time': 3},
    ]
    patterns = asyncio.run(detect_three_candles(candles, "BTCUSDT", "5m"))
    assert len(patterns) == 0  # Not a 3-Candle — C2 body was already outside C1 range
```

### `test_pivots.py`
```python
def test_pivot_highs_detected():
    candles = [{'high': 100, 'low': 90, 'close': 95, 'open': 92}] * 20
    candles[10] = {'high': 120, 'low': 110, 'close': 115, 'open': 112}  # Pivot high at index 10
    highs, lows = find_pivot_highs_lows(candles, left_bars=3, right_bars=3)
    assert any(h.price == 120 for h in highs)

def test_pivot_lows_detected():
    candles = [{'high': 100, 'low': 90, 'close': 95, 'open': 92}] * 20
    candles[10] = {'high': 98, 'low': 75, 'close': 77, 'open': 95}  # Pivot low at index 10
    highs, lows = find_pivot_highs_lows(candles, left_bars=3, right_bars=3)
    assert any(l.price == 75 for l in lows)
```

### `test_volume_profile.py`
```python
def test_close_at_high_100pct_buy():
    candle = {'high': 100, 'low': 90, 'close': 100, 'volume': 1000}
    buy, sell = estimate_buy_sell_volume(candle)
    assert buy == 1000.0
    assert sell == 0.0

def test_close_at_low_100pct_sell():
    candle = {'high': 100, 'low': 90, 'close': 90, 'volume': 1000}
    buy, sell = estimate_buy_sell_volume(candle)
    assert buy == 0.0
    assert sell == 1000.0

def test_close_at_mid_50_50():
    candle = {'high': 100, 'low': 90, 'close': 95, 'volume': 1000}
    buy, sell = estimate_buy_sell_volume(candle)
    assert buy == 500.0
    assert sell == 500.0

def test_volume_confirms_bullish():
    candles = [
        {'high': 100, 'low': 90, 'close': 99, 'volume': 1000},
        {'high': 101, 'low': 91, 'close': 100, 'volume': 1000},
    ]
    result = asyncio.run(analyze_zone_volume(candles))
    assert result.dominant_side == 'BUY'
    assert result.is_confirmed is True

def test_volume_rejects_bearish_breakout():
    candles = [
        {'high': 100, 'low': 90, 'close': 91, 'volume': 1000},  # close at low
        {'high': 102, 'low': 90, 'close': 91, 'volume': 1000},  # close at low
    ]
    result = asyncio.run(analyze_zone_volume(candles))
    assert result.dominant_side == 'SELL'
    assert result.is_confirmed is True
```

---

## Deliverables

### 1. `tests/backend/conftest.py`
All shared fixtures.

### 2. All test files listed above
Each indicator module has its own test file.

### 3. Run All Tests
```bash
cd /home/vick/fubot
pytest tests/backend/ -v --tb=short
```

**Target**: All tests pass before Phase 3 begins.

### 4. Coverage Report
```bash
pytest --cov=backend.indicators --cov-report=term-missing
```
**Target**: ≥ 90% line coverage on all indicator modules.

---

## Dependencies
- **Requires**: Phase 2 Steps 1-6 complete

## Files Modified/Created
- `tests/backend/conftest.py`
- `tests/backend/test_sma.py`
- `tests/backend/test_rsi.py`
- `tests/backend/test_fvg.py`
- `tests/backend/test_three_candle.py`
- `tests/backend/test_pivots.py`
- `tests/backend/test_choch.py`
- `tests/backend/test_bos.py`
- `tests/backend/test_atr.py`
- `tests/backend/test_volume_profile.py`
- `tests/backend/test_indicator_integration.py`

## Next Step
**Phase 2 Complete!** — Phase 3 Step 1: Order Service + Binance Execution

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
