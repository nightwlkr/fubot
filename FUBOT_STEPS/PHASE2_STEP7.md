# Phase 2 — Step 7: Signal Service — Orchestration
## FUBOT: Bringing All Indicators Together

---

## Objective
Build `SignalService` — the orchestration layer that pulls all indicator modules together, evaluates every candidate signal against the complete strategy filter chain, and writes valid signals to the database.

---

## Context
- **Role**: The "brain" that coordinates all other indicator modules
- **Input**: Live candle data from BinanceService
- **Output**: Validated, R:R-checked signals written to `signals` table in DB
- **Filter chain**: Trend Bias → Pattern Detection → Volume Confirmation → R:R Check
- **Depends on**: Phase 2 Steps 1-6 (all indicators implemented)

---

## Signal Service Architecture

```python
class SignalService:
    def __init__(
        self,
        db: AsyncSession,
        binance: BinanceService,
        settings: dict   # From DB settings table
    ):
        self.db = db
        self.binance = binance
        self.settings = settings

    async def scan_symbol(self, symbol: str) -> list[Signal]:
        """
        Full scan pipeline for one symbol.
        Returns: list of valid Signal objects (may be empty)
        """
        # 1. Fetch all needed data
        # 2. Run trend bias (1H)
        # 3. Detect FVG, 3-Candle, CHoCH on 5m/15m
        # 4. Filter by trend bias alignment
        # 5. Filter by volume confirmation
        # 6. Filter by R:R >= minimum
        # 7. Create Signal records in DB
        # 8. Return list of triggered signals
```

---

## Filter Chain (Detailed)

### Filter 1: Trend Bias Alignment
```python
async def _filter_by_trend(self, signal_direction: str, trend_bias: TrendBias) -> bool:
    """
    Returns True if signal aligns with trend bias.
    Aligned: LONG + BULLISH, SHORT + BEARISH
    Scalp-only: LONG + BEARISH, SHORT + BULLISH (reduced size or skip)
    Skip: RSI overbought + LONG, RSI oversold + SHORT
    """
    if trend_bias.direction == 'NEUTRAL':
        return True  # Allow scalp trades in neutral

    if signal_direction == 'LONG' and trend_bias.direction == 'BEARISH':
        return False  # Against bias, skip
    if signal_direction == 'SHORT' and trend_bias.direction == 'BULLISH':
        return False  # Against bias, skip

    # RSI check
    if signal_direction == 'LONG' and trend_bias.rsi_zone == 'OVERBOUGHT':
        return False
    if signal_direction == 'SHORT' and trend_bias.rsi_zone == 'OVERSOLD':
        return False

    return True
```

### Filter 2: Pattern Detection
```python
async def _detect_all_patterns(self, candles_5m: list, candles_15m: list, symbol: str):
    """
    Run all three pattern detectors.
    Returns: list of pattern signals (direction, entry_zone, stop, tp, rr, type)
    """
    patterns = []

    # FVG
    fvgs = await detect_fvg(candles_5m, symbol)
    for fvg in fvgs:
        entry, sl, tp, rr = evaluate_fvg_entry(fvg, atr_value)
        patterns.append(SignalCandidate(
            type='FVG', direction=fvg.direction,
            entry=entry, stop=sl, tp=tp, rr=rr,
            candles=candles_5m  # For volume filter
        ))

    # 3-Candle
    tc_patterns = await detect_three_candles(candles_5m, symbol)
    for tc in tc_patterns:
        entry, sl, tp, rr = evaluate_three_candle_entry(tc, atr_value)
        patterns.append(SignalCandidate(...))

    # CHoCH
    choch_patterns = await detect_choch(candles_15m, symbol, atr_value)
    for ch in choch_patterns:
        patterns.append(SignalCandidate(
            type='CHOC', direction=ch.direction,
            entry=ch.pullback_entry_zone, stop=ch.stop_loss, tp=ch.take_profit,
            rr=ch.rr_ratio, candles=candles_15m
        ))

    return patterns
```

### Filter 3: Volume Confirmation
```python
async def _filter_by_volume(self, candidate: SignalCandidate) -> bool:
    """
    Check if volume bias confirms the signal direction.
    Uses the 10 candles preceding the signal for volume analysis.
    """
    # Get last 10 candles before the signal
    candles_for_volume = candidate.candles[-15:-5]  # Before signal formation

    vol = await analyze_zone_volume(candles_for_volume)

    if candidate.direction == 'LONG' and vol.dominant_side != 'BUY':
        return False
    if candidate.direction == 'SHORT' and vol.dominant_side != 'SELL':
        return False

    return True
```

### Filter 4: R:R Gate
```python
async def _filter_by_rr(self, candidate: SignalCandidate) -> bool:
    """
    Skip if R:R is below minimum threshold.
    Default minimum: 3.0 (3:1)
    Configurable in settings.
    """
    rr_min = self.settings.get('rr_min', 3.0)
    return candidate.rr >= rr_min
```

---

## Signal Model (Database)

```python
# backend/models/signal.py
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(20), nullable=False)  # FVG, 3CANDLE, CHOC
    direction: Mapped[str] = mapped_column(String(5), nullable=False)       # LONG, SHORT
    entry_zone_min: Mapped[float] = mapped_column(Numeric, nullable=False)
    entry_zone_max: Mapped[float] = mapped_column(Numeric, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric, nullable=False)
    take_profit: Mapped[float] = mapped_column(Numeric, nullable=False)
    rr_ratio: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default='PENDING')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

---

## Signal Candidate Dataclass (Internal)

```python
@dataclass
class SignalCandidate:
    type: str          # 'FVG', '3CANDLE', 'CHOC'
    direction: str     # 'LONG', 'SHORT'
    entry: float       # Entry price
    stop: float        # Stop loss
    tp: float          # Take profit
    rr: float          # R:R ratio
    candles: list      # For volume filter
    entry_zone_min: float
    entry_zone_max: float
    passes_filters: bool = False
    skip_reason: str = ""
```

---

## Main Scan Pipeline

```python
async def scan_symbol(self, symbol: str) -> list[Signal]:
    """
    Complete scan pipeline:
    1. Fetch 1H candles → trend bias
    2. Fetch 5m + 15m candles → pattern detection
    3. Calculate ATR → SL/TP sizing
    4. Run all 4 filters
    5. Create Signal DB records for passing signals
    6. Return list of triggered signals
    """
    # Fetch candles (all in parallel)
    candles_1h = await self.binance.get_klines(symbol, "1h", 200)
    candles_5m = await self.binance.get_klines(symbol, "5m", 200)
    candles_15m = await self.binance.get_klines(symbol, "15m", 200)

    # Calculate ATR (14-period on 1H for structural stop sizing)
    atr = await calculate_atr(candles_1h)

    # Step 1: Trend bias
    trend = await analyze_trend_bias(symbol, "1h", candles_1h)

    # Step 2: Pattern detection
    candidates = await self._detect_all_patterns(candles_5m, candles_15m, symbol, atr)

    # Step 3-4: Apply all filters
    valid_signals = []
    for c in candidates:
        c.passes_filters = True

        # Filter by trend
        if not await self._filter_by_trend(c.direction, trend):
            c.passes_filters = False
            c.skip_reason = f"Trend filter: {trend.direction}"

        # Filter by volume
        elif not await self._filter_by_volume(c):
            c.passes_filters = False
            c.skip_reason = "Volume filter: no dominant side confirmation"

        # Filter by R:R
        elif not await self._filter_by_rr(c):
            c.passes_filters = False
            c.skip_reason = f"R:R {c.rr:.2f} below minimum {self.settings.get('rr_min', 3.0)}"

        if c.passes_filters:
            # Write to DB
            signal = Signal(...)
            self.db.add(signal)
            valid_signals.append(signal)

    await self.db.commit()
    return valid_signals
```

---

## Deliverables

### 1. `backend/services/signal_service.py`
- `SignalService` class
- All filter methods
- `scan_symbol()` — full pipeline

### 2. Update `backend/models/signal.py`
Ensure Signal model matches the schema in Phase 1 Step 2.

### 3. Update `backend/schemas/signal.py`
Pydantic schemas for API responses.

### 4. Update `backend/services/__init__.py`
Export `SignalService`.

### 5. Unit Tests `tests/backend/test_signal_service.py`
- Mock BinanceService responses with real-looking candle data
- Test full pipeline with a known FVG pattern + aligned trend
- Test that wrong-trend signal is filtered out
- Test that R:R < 3.0 is filtered out
- Test that non-volume-confirmed signal is filtered out

### 6. Validation
```python
svc = SignalService(db, binance, settings)
signals = await svc.scan_symbol("BTCUSDT")
# With testnet data, should return 0-N signals
# Check that skip_reason is populated for filtered candidates
```

---

## Dependencies
- **Requires**: Phase 2 Steps 1-6 (all indicators)

## Files Modified/Created
- `backend/services/signal_service.py`
- `backend/models/signal.py` (updated)
- `backend/schemas/signal.py`
- `backend/services/__init__.py` (updated)
- `tests/backend/test_signal_service.py`

## Next Step
Phase 2 Step 8: Unit Tests for All Indicators

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
