# Phase 3 — Step 5: Daily Reset + Loss Limit Enforcement
## FUBOT: -3R Daily Hard Stop

---

## Objective
Implement `daily_reset.py` — the component that tracks cumulative daily P&L in R units, enforces the -3R hard stop, and resets counters at UTC midnight.

---

## Context
- **Daily loss limit**: -3R per day (configurable)
- **Reset time**: UTC midnight (00:00 UTC)
- **Scope**: Applies to ALL positions — open and closed
- **Behavior at limit**: Bot stops accepting new trades, alerts sent
- **Depends on**: Phase 3 Step 4

---

## DailyResetService: `backend/core/daily_reset.py`

```python
class DailyResetService:
    def __init__(
        self,
        db: AsyncSession,
        settings: dict,
        bot_engine,  # Reference to bot engine to trigger stop
        alert_service  # For sending alerts
    ):
        self.db = db
        self.settings = settings
        self.bot = bot_engine
        self.alerts = alert_service
        self._limit_hit_today = False

    async def get_daily_stats(self) -> dict:
        """
        Returns today's trading stats.
        """
        today = datetime.utcnow().date()
        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        if summary:
            return {
                'date': today,
                'total_trades': summary.total_trades,
                'wins': summary.wins,
                'losses': summary.losses,
                'total_pnl_r': summary.total_pnl_r,
                'used_r': abs(summary.total_pnl_r),
                'limit_r': abs(self.settings.get('daily_loss_limit_r', -3.0)),
                'remaining_r': abs(self.settings.get('daily_loss_limit_r', -3.0)) - abs(summary.total_pnl_r),
                'limit_hit': summary.daily_loss_limit_hit
            }

        return {
            'date': today,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl_r': 0.0,
            'used_r': 0.0,
            'limit_r': abs(self.settings.get('daily_loss_limit_r', -3.0)),
            'remaining_r': abs(self.settings.get('daily_loss_limit_r', -3.0)),
            'limit_hit': False
        }

    async def check_and_enforce_limit(self) -> bool:
        """
        Called before every new trade is considered.
        Returns True if trading is allowed.
        Returns False if daily limit is hit (trading stopped).
        """
        if self._limit_hit_today:
            return False

        stats = await self.get_daily_stats()
        limit = stats['limit_r']
        used = stats['used_r']

        if used >= limit:
            await self._trigger_daily_stop()
            return False

        # Warning at -2R (80% of limit used)
        if used >= limit * 0.8 and used < limit:
            await self.alerts.send(
                level='warn',
                message=f"Daily loss limit warning: {used:.1f}R of {limit}R used ({remaining_r:.1f}R remaining)"
            )

        return True

    async def _trigger_daily_stop(self):
        """
        Emergency stop: bot stops trading for the rest of the day.
        """
        self._limit_hit_today = True

        # Update daily summary
        today = datetime.utcnow().date()
        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()
        if summary:
            summary.daily_loss_limit_hit = True
            await self.db.commit()

        # Stop the bot
        await self.bot.stop()

        # Alert
        await self.alerts.send(
            level='error',
            message=f"🚨 DAILY LOSS LIMIT HIT: {stats['used_r']:.1f}R / {stats['limit_r']:.1f}R. Bot stopped for the day."
        )

        logger.critical(f"Daily loss limit hit: {stats['used_r']:.1f}R. Bot stopped.")

    async def update_daily_summary(self, trade: Trade):
        """
        Called after each trade closes to update the daily summary.
        """
        today = datetime.utcnow().date()

        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        if not summary:
            summary = DailySummary(date=today)
            self.db.add(summary)

        summary.total_trades += 1
        summary.total_pnl_r += trade.pnl_r
        summary.total_pnl_usdt += trade.pnl_usdt

        if trade.pnl_r > 0:
            summary.wins += 1
            if trade.pnl_r > summary.best_trade_r:
                summary.best_trade_r = trade.pnl_r
        elif trade.pnl_r < 0:
            summary.losses += 1
            if trade.pnl_r < summary.worst_trade_r:
                summary.worst_trade_r = trade.pnl_r
        else:
            summary.breakeven += 1

        await self.db.commit()

    async def check_and_reset(self):
        """
        Called at UTC midnight to reset the daily tracking.
        """
        now = datetime.utcnow()
        if now.hour == 0 and now.minute < 5:  # Within 5 minutes of midnight
            if self._limit_hit_today:
                logger.info("New UTC day — resetting daily loss limit flag.")
                self._limit_hit_today = False

            # The daily summary is already date-keyed, so next trade will auto-create a new record
```

---

## Integration with BotEngine

The daily reset check is called at key points:

```python
# In BotEngine._check_before_new_trade():
async def _check_before_new_trade(self) -> bool:
    """
    Pre-trade checks. Returns True if trade can proceed.
    """
    # 1. Daily limit check
    if not await self.daily_reset.check_and_enforce_limit():
        logger.warning(f"Daily loss limit reached, skipping trade.")
        return False

    # 2. Check bot is running
    if not self._running:
        return False

    # 3. Check not in cooldown
    if self._in_cooldown:
        return False

    return True
```

---

## Deliverables

### 1. `backend/core/daily_reset.py`
- `DailyResetService` class
- `get_daily_stats()` — current day's stats
- `check_and_enforce_limit()` — called before new trades
- `update_daily_summary()` — called after each trade closes
- `check_and_reset()` — UTC midnight reset

### 2. Update `backend/core/__init__.py`
Export `DailyResetService`.

### 3. Unit Tests `tests/backend/test_daily_reset.py`
- Test daily stats calculation
- Test limit enforcement (used_r >= limit → blocked)
- Test warning at 80% of limit
- Test reset at midnight

### 4. Validation
```python
reset = DailyResetService(db, settings, bot, alerts)

# Before any trades
stats = await reset.get_daily_stats()
assert stats['remaining_r'] == 3.0

# After a -1R trade
await reset.update_daily_summary(trade_loss_1r)
stats = await reset.get_daily_stats()
assert stats['remaining_r'] == 2.0

# After 3 losses of -1R each
# check_and_enforce_limit should return False
can_trade = await reset.check_and_enforce_limit()
assert can_trade is False
```

---

## Dependencies
- **Requires**: Phase 3 Step 4

## Files Modified/Created
- `backend/core/daily_reset.py`
- `backend/core/__init__.py` (updated)
- `tests/backend/test_daily_reset.py`

## Next Step
Phase 3 Step 6: Alert Service

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
