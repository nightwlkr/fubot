# Phase 3 — Step 4: Position Manager + Break-Even Trailing
## FUBOT: Track Open Positions, Trail Stops to BE at New BOS

---

## Objective
Build `PositionManager` — the component that tracks all open positions in real-time, monitors price movement to detect new BOS (for BE trailing), updates stop losses, and closes positions when TP or SL is hit.

---

## Context
- **BOS Trailing**: When price creates a new Break of Structure in favor of the trade direction, move SL to BE immediately — this locks in profit without cutting the winner short
- **Real-time**: Must run continuously or on a short interval (every 30-60s)
- **Data**: Uses Binance WebSocket for live prices + REST for order updates
- **Depends on**: Phase 3 Steps 1, 2, 3 and Phase 2 Step 5 (BOS detection)

---

## PositionManager: `backend/core/position_manager.py`

```python
class PositionManager:
    def __init__(
        self,
        db: AsyncSession,
        binance: BinanceService,
        order_service: OrderService,
        indicators,  # Import indicators needed for BOS check
        settings: dict
    ):
        self.db = db
        self.binance = binance
        self.orders = order_service
        self.indicators = indicators
        self.settings = settings
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the position manager loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        """Stop the position manager loop."""
        self._running = False
        if self._task:
            await self._task

    async def _loop(self):
        """Main loop — runs every 30 seconds."""
        while self._running:
            try:
                await self._check_all_positions()
            except Exception as e:
                logger.error(f"PositionManager error: {e}")
            await asyncio.sleep(30)

    async def _check_all_positions(self):
        """
        Check all open positions:
        1. Get current prices from Binance
        2. Update unrealized P&L
        3. Check for new BOS → trail stop to BE
        4. Check if TP or SL was hit → close position
        5. Check for liquidation risk → emergency close
        """
        open_positions = await self._get_open_positions_from_db()

        for position in open_positions:
            symbol = position.symbol
            current_price = await self.binance.get_ticker(symbol)['price']

            # Update unrealized P&L
            pnl = self._calc_pnl(position, current_price)
            await self._update_position_pnl(position, pnl)

            # Check BE trailing
            await self._check_be_trailing(position, current_price)

            # Check if TP or SL hit
            tp_hit, sl_hit = self._check_tp_sl_hit(position, current_price)
            if tp_hit:
                await self._close_position(position, 'TP_HIT', current_price)
            elif sl_hit:
                await self._close_position(position, 'SL_HIT', current_price)
```

---

## Break-Even Trailing Logic

```python
async def _check_be_trailing(self, position: Position, current_price: float):
    """
    If price has moved in favor enough to create a new BOS, move SL to BE.

    Rules:
    - Only move SL to BE once (once BE, never lower again)
    - BE = entry price (exactly)
    - Trigger: new BOS in the trade's favor on 5m timeframe
    - Before moving: confirm the move is valid (BOS confirmed, not just noise)
    """
    if position.side == 'LONG':
        be_candidate = position.entry_price
        current_sl = position.stop_loss

        if current_sl >= be_candidate:  # Already at BE or above
            return  # Nothing to do

        # Check for new bullish BOS on 5m
        candles = await self.binance.get_klines(position.symbol, "5m", 100)
        struct = await self.indicators.analyze_structure(candles, position.symbol, "5m", atr)

        if struct.last_bos and struct.last_bos.direction == 'BULLISH':
            # New BOS in our favor! Move SL to BE
            if current_sl < be_candidate:
                await self._trail_stop_to_be(position, be_candidate)

    elif position.side == 'SHORT':
        be_candidate = position.entry_price
        current_sl = position.stop_loss

        if current_sl <= be_candidate:  # Already at BE or below
            return

        candles = await self.binance.get_klines(position.symbol, "5m", 100)
        struct = await self.indicators.analyze_structure(candles, position.symbol, "5m", atr)

        if struct.last_bos and struct.last_bos.direction == 'BEARISH':
            if current_sl > be_candidate:
                await self._trail_stop_to_be(position, be_candidate)


async def _trail_stop_to_be(self, position: Position, new_stop: float):
    """
    Move stop loss to break-even (entry price).
    """
    # Get current SL order ID from DB
    # Cancel old SL
    # Place new SL at entry price
    # Update DB
    logger.info(f"[{position.symbol}] Trailing stop to BE: {position.stop_loss} → {new_stop}")

    cancel_ok = await self.orders.cancel_order(position.symbol, position.sl_order_id)
    if not cancel_ok.success:
        logger.error(f"Failed to cancel old SL: {cancel_ok.error}")
        return

    sl_result = await self.orders.place_stop_loss(
        symbol=position.symbol,
        side=position.side,
        stop_price=new_stop,
        quantity=position.quantity
    )

    if sl_result.success:
        position.stop_loss = new_stop
        position.sl_order_id = sl_result.binance_order_id
        position.sl_moved_to_be_at = datetime.utcnow()
        await self.db.commit()
```

---

## Position Close Logic

```python
async def _close_position(
    self,
    position: Position,
    reason: str,            # 'TP_HIT', 'SL_HIT', 'MANUAL', 'LIQUIDATION', 'BE_TRAIL'
    exit_price: float
):
    """
    Close position: record trade, update DB, log P&L.
    """
    # Get entry price from position
    entry = position.entry_price
    qty = position.quantity
    side = position.side

    # Calculate realized P&L
    if side == 'LONG':
        pnl_usdt = (exit_price - entry) * qty
    else:
        pnl_usdt = (entry - exit_price) * qty

    # Calculate P&L in R units
    risk_amount = abs(entry - position.stop_loss) * qty
    pnl_r = pnl_usdt / risk_amount if risk_amount > 0 else 0.0

    # Create Trade record
    trade = Trade(
        position_id=position.id,
        symbol=position.symbol,
        side=side,
        entry_price=entry,
        exit_price=exit_price,
        quantity=qty,
        pnl_usdt=pnl_usdt,
        pnl_r=pnl_r,
        exit_reason=reason,
        strategy_type=position.signal.strategy_type,
        closed_at=datetime.utcnow()
    )
    self.db.add(trade)

    # Update position status
    position.status = 'CLOSED'
    position.closed_at = datetime.utcnow()
    await self.db.commit()

    # Update daily summary
    await self._update_daily_summary(trade)

    # Broadcast via WebSocket
    await self.ws_manager.broadcast({
        'type': 'position_closed',
        'data': {
            'position_id': position.id,
            'symbol': position.symbol,
            'pnl_r': pnl_r,
            'exit_reason': reason
        }
    })

    logger.info(f"[{position.symbol}] Position closed: {reason}, PnL: {pnl_usdt:.2f} USDT ({pnl_r:.2f}R)")
```

---

## Deliverables

### 1. `backend/core/position_manager.py`
- `PositionManager` class
- `start()`, `stop()`, `_loop()`
- `_check_be_trailing()` — BE trailing at new BOS
- `_close_position()` — close + record trade
- `_check_tp_sl_hit()` — detect TP/SL hits

### 2. Update `backend/core/__init__.py`
Export `PositionManager`.

### 3. Unit Tests `tests/backend/test_position_manager.py`
- Mock Binance price feed
- Test BE trailing fires at new BOS
- Test BE trailing doesn't fire when already at BE
- Test TP hit detection
- Test P&L calculation

### 4. Validation
```python
pm = PositionManager(db, binance, orders, indicators, settings)
await pm.start()
await asyncio.sleep(60)  # Run for 1 minute
positions = await pm._get_open_positions_from_db()
# Check that positions were monitored
await pm.stop()
```

---

## Dependencies
- **Requires**: Phase 3 Steps 1, 2, 3 and Phase 2 Step 5 (BOS detection)

## Files Modified/Created
- `backend/core/position_manager.py`
- `backend/core/__init__.py`
- `tests/backend/test_position_manager.py`

## Next Step
Phase 3 Step 5: Daily Reset + Loss Limit Enforcement

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
