# Phase 3 — Step 3: Strategy Service — Trade Decision Logic
## FUBOT: When to Trade, When to Skip

---

## Objective
Build `StrategyService` — the decision-making layer that evaluates whether a valid signal from SignalService should actually result in a trade. It applies the final strategic filters (trend alignment, daily limit check, position count check) and decides whether to send an execution order to OrderService.

---

## Context
- **Role**: The "judge" — SignalService finds opportunities, StrategyService decides which to act on
- **Input**: `Signal` objects from SignalService
- **Output**: Either: (a) order placed → `Position` created, or (b) signal skipped with reason logged
- **Strategic filters**: Beyond R:R and volume, includes: position cooldowns, same-symbol re-entry, news/sentiment blackout (future)
- **Depends on**: Phase 3 Steps 1 and 2

---

## StrategyService: `backend/services/strategy_service.py`

```python
@dataclass
class TradeDecision:
    signal: Signal
    decision: str            # 'EXECUTE', 'SKIP', 'PAPER'
    position_size: PositionSize | None
    skip_reason: str | None
    order_ids: list[int]      # Binance order IDs if executed

class StrategyService:
    def __init__(
        self,
        db: AsyncSession,
        signal_service: SignalService,
        risk_service: RiskService,
        order_service: OrderService,
        settings: dict
    ):
        self.db = db
        self.signals = signal_service
        self.risk = risk_service
        self.orders = order_service
        self.settings = settings

    async def evaluate_signal(self, signal: Signal) -> TradeDecision:
        """
        Full evaluation of one signal:
        1. Check if symbol already has an open position
        2. Check signal freshness (signal not older than X minutes)
        3. Get ATR for current price
        4. Run RiskService.validate_trade
        5. If valid: execute via OrderService
        6. If invalid: log skip reason
        7. Return TradeDecision
        """

    async def evaluate_all_signals(self, signals: list[Signal]) -> list[TradeDecision]:
        """
        Evaluate a batch of signals, returning decisions for each.
        Prioritize by R:R (highest first) if multiple signals on same symbol.
        """
```

---

## Evaluation Pipeline: `evaluate_signal()`

```python
async def evaluate_signal(self, signal: Signal) -> TradeDecision:
    # 1. Already have open position on this symbol?
    existing = await self.orders.get_current_position(signal.symbol)
    if existing:
        return TradeDecision(
            signal=signal,
            decision='SKIP',
            position_size=None,
            skip_reason=f"Position already open on {signal.symbol}",
            order_ids=[]
        )

    # 2. Signal freshness check
    signal_age = datetime.utcnow() - signal.created_at
    max_age = timedelta(minutes=self.settings.get('signal_max_age_minutes', 60))
    if signal_age > max_age:
        signal.status = 'EXPIRED'
        await self.db.commit()
        return TradeDecision(
            signal=signal,
            decision='SKIP',
            position_size=None,
            skip_reason=f"Signal expired ({signal_age.minutes:.0f}min old)",
            order_ids=[]
        )

    # 3. Get current market price
    ticker = await self.binance.get_ticker(signal.symbol)
    current_price = float(ticker['price'])

    # 4. Recalculate entry (limit order at midpoint of zone)
    entry_price = (signal.entry_zone_min + signal.entry_zone_max) / 2

    # 5. Verify price is still near entry zone
    # Allow 1% deviation from zone midpoint
    max_deviation = 0.01
    if signal.direction == 'LONG':
        if current_price > signal.entry_zone_max * (1 + max_deviation):
            return TradeDecision(..., decision='SKIP', skip_reason="Price moved too far above zone", ...)
        if current_price < signal.entry_zone_min * (1 - max_deviation):
            return TradeDecision(..., decision='SKIP', skip_reason="Price already below zone", ...)
    else:  # SHORT
        if current_price < signal.entry_zone_min * (1 - max_deviation):
            return TradeDecision(..., decision='SKIP', skip_reason="Price moved too far below zone", ...)
        if current_price > signal.entry_zone_max * (1 + max_deviation):
            return TradeDecision(..., decision='SKIP', skip_reason="Price already above zone", ...)

    # 6. Full risk validation
    risk_check = await self.risk.validate_trade(
        symbol=signal.symbol,
        entry_price=entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        direction=signal.direction
    )

    if not risk_check.is_valid:
        signal.status = 'SKIPPED'
        signal.skip_reason = risk_check.reason
        await self.db.commit()
        return TradeDecision(
            signal=signal,
            decision='SKIP',
            position_size=None,
            skip_reason=risk_check.reason,
            order_ids=[]
        )

    # 7. Execute the trade
    position_size = risk_check.position_size
    entry_result = await self.orders.place_entry_order(
        signal=signal,
        entry_price=entry_price,
        quantity=position_size.quantity
    )

    if not entry_result.success:
        return TradeDecision(
            signal=signal,
            decision='SKIP',
            position_size=None,
            skip_reason=f"Order placement failed: {entry_result.error}",
            order_ids=[]
        )

    # 8. Place SL and TP
    stop_result = await self.orders.place_stop_loss(
        symbol=signal.symbol,
        side=signal.direction,
        stop_price=signal.stop_loss,
        quantity=position_size.quantity
    )

    tp_result = await self.orders.place_take_profit(
        symbol=signal.symbol,
        side=signal.direction,
        tp_price=signal.take_profit,
        quantity=position_size.quantity
    )

    # 9. Create Position record in DB
    position = Position(
        signal_id=signal.id,
        symbol=signal.symbol,
        side=signal.direction,
        entry_price=entry_price,
        quantity=position_size.quantity,
        leverage=position_size.leverage_used,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        status='OPEN',
        opened_at=datetime.utcnow()
    )
    self.db.add(position)
    await self.db.commit()

    # 10. Update signal status
    signal.status = 'TRIGGERED'
    signal.triggered_at = datetime.utcnow()
    await self.db.commit()

    return TradeDecision(
        signal=signal,
        decision='EXECUTE',
        position_size=position_size,
        skip_reason=None,
        order_ids=[entry_result.binance_order_id, stop_result.binance_order_id, tp_result.binance_order_id]
    )
```

---

## Same-Symbol Re-Entry Prevention

```python
async def _has_recent_trade_on_symbol(self, symbol: str, lookback_hours: int = 4) -> bool:
    """
    Prevent the bot from opening a new position on a symbol
    if it had a trade (win or loss) within the last `lookback_hours`.
    """
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    result = await self.db.execute(
        select(Trade).where(
            Trade.symbol == symbol,
            Trade.closed_at >= cutoff
        )
    )
    return result.scalar_one_or_none() is not None
```

---

## Paper Trading Mode

```python
async def evaluate_signal(self, signal: Signal, paper_mode: bool = False) -> TradeDecision:
    """
    If paper_mode=True:
    - Run all validation checks normally
    - Log the decision and signals
    - DO NOT actually place orders on Binance
    - DO NOT create Position records (create PaperPosition instead)
    """
    if paper_mode:
        # Full validation, but simulate execution
        decision = await self._evaluate_without_execution(signal)
        decision.decision = 'PAPER'
        return decision
```

---

## Deliverables

### 1. `backend/services/strategy_service.py`
- `TradeDecision` dataclass
- `StrategyService` class
- `evaluate_signal()` — full pipeline
- `evaluate_all_signals()` — batch evaluation
- `_has_recent_trade_on_symbol()` — re-entry guard

### 2. Update `backend/services/__init__.py`
Export `StrategyService`.

### 3. Unit Tests `tests/backend/test_strategy_service.py`
- Test open position → skip
- Test stale signal → skip with EXPIRED status
- Test price moved too far → skip
- Test R:R fail → skip
- Test valid signal → EXECUTE with all orders placed

### 4. Validation
```python
svc = StrategyService(db, signals, risk, orders, settings)

# Valid signal
decision = await svc.evaluate_signal(valid_signal)
assert decision.decision == 'EXECUTE'
assert len(decision.order_ids) == 3  # entry + SL + TP

# Signal on symbol with open position
decision = await svc.evaluate_signal(signal_on_busy_symbol)
assert decision.decision == 'SKIP'
assert 'already open' in decision.skip_reason
```

---

## Dependencies
- **Requires**: Phase 3 Steps 1 and 2

## Files Modified/Created
- `backend/services/strategy_service.py`
- `backend/services/__init__.py` (updated)
- `tests/backend/test_strategy_service.py`

## Next Step
Phase 3 Step 4: Position Manager + Break-Even Trailing

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
