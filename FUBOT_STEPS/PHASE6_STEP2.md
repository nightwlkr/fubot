# Phase 6 — Step 2: Daily Loss Limit Enforcement Test
## FUBOT: Verify -3R Hard Stop Works Correctly

---

## Objective
Verify that the bot correctly stops trading after the daily loss limit (-3R) is reached. This is a critical safety feature that must be validated before going live.

---

## Context
- **Depends on**: Phase 6 Step 1 (bot running, trade cycle validated)
- **Safety feature**: Bot must stop automatically at -3R cumulative daily loss
- **Reset**: Limit resets at 00:00 UTC
- **Test approach**: Simulate losses until the limit is hit; verify bot stops

---

## Background: How Daily Loss Limit Works

The `DailyReset` service (Phase 3 Step 5) tracks:
- `used_r`: cumulative R lost today (positive number = losses)
- When `used_r >= limit_r`, bot stops and refuses new trades
- Limit is per-trading-day (00:00 UTC reset)

---

## Test: Simulate -3R Loss

### Option A: Use a Paper Trade that Hits Stop Loss

1. Ensure bot is running in **paper mode**
2. Wait for a signal → trade executes
3. Monitor the position — when it hits stop loss, the trade closes with a loss
4. Repeat until `daily_limit_status.used_r >= 3.0`

```bash
# Monitor daily stats (run every minute)
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/dashboard/today
```

Look at `daily_limit_status`:
```json
{
  "used_r": 1.5,
  "limit_r": 3.0,
  "remaining_r": 1.5
}
```

### Option B: Direct DB Manipulation (Quicker Test)

```bash
# On VPS, insert simulated losses directly
cd /home/vick/fubot/backend
python3 -c "
import asyncio
from database import async_session
from models.trade import Trade
from models.daily_summary import DailySummary
from datetime import datetime, date

async def add_losses():
    async with async_session() as db:
        # Add 2R in simulated losses (need 3R total to trigger limit)
        for i in range(2):
            trade = Trade(
                symbol='BTCUSDT',
                side='LONG',
                entry_price=65000.0,
                exit_price=64000.0,
                quantity=0.01,
                pnl_r=-1.0,
                pnl_usdt=-10.0,
                outcome='loss',
                strategy_type='FVG',
                entry_time=datetime.utcnow(),
                exit_time=datetime.utcnow(),
                fees=0.5,
                rr_achieved=-1.0
            )
            db.add(trade)
        await db.commit()
        print('Added 2R simulated loss')

asyncio.run(add_losses())
"
```

After this, check:
```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/dashboard/today
```

Expected: `daily_limit_status.remaining_r` is now 1.0R.

Now add 1 more R loss:

```python
# Add 1 more R to hit exactly 3R
python3 -c "
import asyncio
from database import async_session
from models.trade import Trade
from datetime import datetime

async def add_final_loss():
    async with async_session() as db:
        trade = Trade(
            symbol='BTCUSDT',
            side='LONG',
            entry_price=65000.0,
            exit_price=64000.0,
            quantity=0.01,
            pnl_r=-1.0,
            pnl_usdt=-10.0,
            outcome='loss',
            strategy_type='FVG',
            entry_time=datetime.utcnow(),
            exit_time=datetime.utcnow(),
            fees=0.5,
            rr_achieved=-1.0
        )
        db.add(trade)
        await db.commit()
        print('Added 1R — total now -3R')

asyncio.run(add_final_loss())
"
```

---

## Verify Bot Stops at -3R

After the simulated -3R total:

```bash
# Check bot status — should show STOPPED (auto-stopped)
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/status

# Expected: status is "stopped" (forced stop due to limit)

# Try to start the bot manually — should fail or refuse
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/start \
  -H "Content-Type: application/json" \
  -d '{"mode":"paper"}'
```

Expected: Bot refuses to start with an error message:
```json
{"detail": "Daily loss limit reached. Bot cannot start until 00:00 UTC."}
```

---

## Verify Dashboard Shows Limit Hit

1. Open the FUBOT dashboard
2. The **BotStatusCard** should show "STOPPED"
3. The **PnLCard** daily limit bar should be 100% red
4. A warning alert should be visible

---

## Test: Midnight UTC Reset

```bash
# Check current UTC time
date -u

# Manually trigger the reset for testing (or wait for 00:00 UTC)
cd /home/vick/fubot/backend
python3 -c "
import asyncio
from core.daily_reset import DailyReset
from database import async_session

async def force_reset():
    async with async_session() as db:
        dr = DailyReset(db)
        await dr.reset_if_new_day()
        print('Daily limit reset')

asyncio.run(force_reset())
"
```

After reset:
```bash
# Bot should start again
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/start \
  -H "Content-Type: application/json" \
  -d '{"mode":"paper"}'

curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/dashboard/today
```

Expected:
- Bot starts successfully
- `daily_limit_status.used_r` = 0 (reset)
- `daily_limit_status.remaining_r` = 3.0

---

## Validation Checklist

- [ ] Bot auto-stops when daily loss reaches -3R
- [ ] Dashboard shows 100% daily limit bar (red)
- [ ] Bot refuses to restart after limit hit (until reset)
- [ ] Midnight UTC reset clears the limit
- [ ] Bot restarts successfully after reset
- [ ] Alert sent (Telegram/email) when limit is hit

---

## Deliverables

1. Confirmed bot stops at -3R cumulative loss
2. Confirmed bot cannot be manually started after limit hit
3. Confirmed midnight reset works
4. Alert notification fires at -3R

---

## Next Step
Phase 6 Step 3: Security Audit

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
