# Phase 4 — Step 1: Bot Engine — Main Async Loop
## FUBOT: The Core Trading Loop

---

## Objective
Build `BotEngine` — the main orchestrator that runs continuously, scanning enabled symbols on a configurable interval, evaluating signals, executing trades, and managing positions.

---

## Context
- **Loop interval**: 60 seconds (configurable via `check_interval_seconds` in settings)
- **Modes**: `paper` (testnet, no real orders) or `live` (real Binance)
- **Startup/Shutdown**: Clean start and stop with state restoration
- **Depends on**: Phase 3 all steps, Phase 2 all steps

---

## BotEngine: `backend/core/bot_engine.py`

```python
class BotEngine:
    def __init__(
        self,
        db: AsyncSession,
        settings: dict,
        # Services
        signal_service: SignalService,
        strategy_service: StrategyService,
        position_manager: PositionManager,
        risk_service: RiskService,
        alert_service: AlertService,
        binance: BinanceService,
        ws_manager: WebSocketManager,
    ):
        self.db = db
        self.settings = settings
        self.signals = signal_service
        self.strategy = strategy_service
        self.positions = position_manager
        self.risk = risk_service
        self.alerts = alert_service
        self.binance = binance
        self.ws = ws_manager

        self._running = False
        self._mode: Literal['paper', 'live'] = 'paper'
        self._task: asyncio.Task | None = None
        self._last_scan: datetime | None = None
        self._scan_count = 0

    @property
    def mode(self) -> str:
        return self._mode

    async def start(self, mode: Literal['paper', 'live'] = 'paper'):
        """Start the trading bot in paper or live mode."""
        self._running = True
        self._mode = mode
        self._task = asyncio.create_task(self._main_loop())
        await self.alerts.send('info', f"🚀 FUBOT started in {mode.upper()} mode")
        logger.info(f"BotEngine: started in {mode} mode")

    async def stop(self):
        """Gracefully stop the bot."""
        self._running = False
        if self._task:
            await self._task
        await self.alerts.send('info', "⏹️ FUBOT stopped")
        logger.info("BotEngine: stopped")

    async def _main_loop(self):
        """
        Main trading loop. Runs every `check_interval_seconds`.
        """
        interval = self.settings.get('check_interval_seconds', 60)

        while self._running:
            try:
                self._scan_count += 1
                start = datetime.utcnow()

                # 1. Scan all enabled symbols for signals
                await self._scan_and_evaluate()

                # 2. Equity snapshot (every 5 minutes)
                await self._maybe_snapshot_equity()

                elapsed = (datetime.utcnow() - start).total_seconds()
                self._last_scan = datetime.utcnow()

                logger.debug(f"Scan #{self._scan_count} completed in {elapsed:.1f}s")

                # Sleep until next interval
                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"BotEngine main loop error: {e}", exc_info=True)
                await self.alerts.send('error', f"Bot loop error: {e}")
                await asyncio.sleep(interval)  # Continue despite error
```

---

## Scan & Evaluate Pipeline

```python
async def _scan_and_evaluate(self):
    """
    For each enabled symbol:
    1. Scan for signals
    2. Evaluate each signal
    3. Log results
    """
    # Get enabled symbols
    symbols = await self._get_enabled_symbols()

    for symbol in symbols:
        try:
            # Skip if already have open position on this symbol
            existing = await self.binance.get_position(symbol)
            if existing and existing['quantity'] > 0:
                continue  # Position already open, skip

            # Check daily limit
            if not await self.daily_reset.check_and_enforce_limit():
                logger.info(f"Daily limit reached, skipping {symbol}")
                break

            # Run signal scan
            signals = await self.signals.scan_symbol(symbol)

            for signal in signals:
                # Evaluate and possibly execute
                decision = await self.strategy.evaluate_signal(
                    signal,
                    paper_mode=(self._mode == 'paper')
                )

                if decision.decision == 'EXECUTE':
                    await self.alerts.send(
                        'info',
                        f"🚀 {decision.signal.direction} {decision.signal.symbol} @ {decision.signal.entry_zone_min:.2f}, R:R: {decision.signal.rr_ratio:.1f}",
                        symbol=decision.signal.symbol
                    )
                elif decision.decision == 'SKIP':
                    logger.debug(f"[{signal.symbol}] Skipped: {decision.skip_reason}")

        except BinanceAPIError as e:
            logger.error(f"Binance API error for {symbol}: {e}")
            continue
        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}", exc_info=True)


async def _get_enabled_symbols(self) -> list[str]:
    """Fetch list of enabled symbols from DB."""
    result = await self.db.execute(
        select(Symbol.symbol).where(Symbol.enabled == True)
    )
    return [row[0] for row in result.fetchall()]


async def _maybe_snapshot_equity(self):
    """Log equity every 5 minutes."""
    if self._scan_count % 5 != 0:
        return

    balance = await self.binance.get_account_balance()
    equity = EquityLog(
        balance_usdt=balance,
        recorded_at=datetime.utcnow()
    )
    self.db.add(equity)
    await self.db.commit()
```

---

## Bot Status API

```python
async def get_status(self) -> dict:
    """Current bot status for dashboard."""
    return {
        'status': 'running' if self._running else 'stopped',
        'mode': self._mode,
        'uptime_seconds': (datetime.utcnow() - self._started_at).total_seconds() if self._running else 0,
        'scan_count': self._scan_count,
        'last_scan': self._last_scan.isoformat() if self._last_scan else None,
        'daily_stats': await self.daily_reset.get_daily_stats(),
        'open_positions': await self._get_open_positions_summary(),
        'enabled_symbols': await self._get_enabled_symbols()
    }
```

---

## Deliverables

### 1. `backend/core/bot_engine.py`
- `BotEngine` class
- `start()`, `stop()`, `get_status()`
- `_main_loop()` — 60-second scan cycle
- `_scan_and_evaluate()` — symbol loop
- `_maybe_snapshot_equity()` — periodic equity logging

### 2. Update `backend/core/__init__.py`
Export `BotEngine`.

### 3. Update `backend/main.py`
Add `BotEngine` as a lifespan dependency (app startup creates it, shutdown closes it):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    engine = BotEngine(...)
    await engine.start()
    app.state.bot_engine = engine
    yield
    # Shutdown
    await engine.stop()
```

### 4. API Routes for Bot Control
Add to `backend/routers/bot.py`:
```python
@router.post("/start")
async def start_bot(mode: str = "paper"):
    await app.state.bot_engine.start(mode=mode)
    return {"ok": True, "status": "running", "mode": mode}

@router.post("/stop")
async def stop_bot():
    await app.state.bot_engine.stop()
    return {"ok": True, "status": "stopped"}

@router.get("/status")
async def get_bot_status():
    return await app.state.bot_engine.get_status()
```

### 5. Validation
```python
# Start bot in paper mode
engine = BotEngine(...)
await engine.start('paper')
await asyncio.sleep(120)  # Run 2 minutes
status = await engine.get_status()
assert status['status'] == 'running'
assert status['mode'] == 'paper'
assert status['scan_count'] >= 1
await engine.stop()
```

---

## Dependencies
- **Requires**: Phase 3 Steps 1-7 all complete

## Files Modified/Created
- `backend/core/bot_engine.py`
- `backend/core/__init__.py` (updated)
- `backend/routers/bot.py` (new)
- `backend/main.py` (updated with lifespan)

## Next Step
Phase 4 Step 2: WebSocket Server + Real-time Push

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
