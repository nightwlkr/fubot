# Phase 3 — Step 7: Paper Trading Test (72h)
## FUBOT: Validate Full System on Testnet

---

## Objective
Run the complete trading system in paper trading mode (BINANCE_TESTNET=true) for 72 hours, verifying:
- All signals detected correctly
- Orders placed on testnet
- Positions tracked
- P&L calculated accurately
- No crashes, no data loss
- Dashboard receives live updates

---

## Context
- **Mode**: Paper trading (testnet) only
- **Duration**: 72 hours continuous
- **Goal**: Zero errors, all trades logged correctly
- **This is a validation step**, not a build step — it verifies Phase 1-3 work together
- **Depends on**: Phase 3 Steps 1-6

---

## Test Plan

### Pre-Test Checklist
```bash
# 1. Set environment
export BINANCE_TESTNET=true
export DATABASE_URL=postgresql+asyncpg://fubot:<password>@localhost:5432/fubot

# 2. Clear any existing test data
python scripts/clear_test_data.py

# 3. Start the bot
systemctl start fubot

# 4. Check it's running
systemctl status fubot
journalctl -u fubot -n 20 --no-pager
```

### During Test: Monitor These Things

#### Every Hour
- [ ] Check `journalctl -u fubot --since "1 hour ago" --no-pager` for errors
- [ ] Check signals are being detected (`select count(*) from signals where created_at > now() - interval '1 hour';`)
- [ ] Check `positions` table for open positions
- [ ] Check no unexpected order placements

#### Every 8 Hours
- [ ] Run: `psql $DATABASE_URL -c "select symbol, count(*) from signals group by symbol;"`
- [ ] Run: `psql $DATABASE_URL -c "select * from daily_summary order by date desc limit 1;"`
- [ ] Verify equity curve is updating (equity_log table growing)

#### Daily
- [ ] Check `trades` table — are closed trades recorded correctly?
- [ ] Verify P&L in USDT and R units are consistent
- [ ] Check no positions open for > 24h without action
- [ ] Review alert log for any warnings/errors

### 72-Hour Completion Checklist
```sql
-- 1. Signal detection
SELECT count(*) as signals_generated, count(*) filter (where status = 'TRIGGERED') as triggered
FROM signals WHERE created_at > :test_start;

-- 2. Trade performance
SELECT
  count(*) as total_trades,
  count(*) filter (where pnl_r > 0) as wins,
  count(*) filter (where pnl_r < 0) as losses,
  avg(pnl_r) as avg_r,
  sum(pnl_r) as total_r,
  sum(pnl_usdt) as total_usdt
FROM trades;

-- 3. Position tracking
SELECT count(*) as open_positions FROM positions WHERE status = 'OPEN';

-- 4. Equity log
SELECT count(*) as equity_snapshots, min(balance_usdt), max(balance_usdt)
FROM equity_log;

-- 5. No data corruption
SELECT count(*) as errors FROM logs WHERE level = 'ERROR';
```

### Expected Results
| Metric | Expected |
|---|---|
| Signals generated | 5-20 (depending on market) |
| Triggered/traded | 0-5 (depends on R:R filter) |
| Win rate | Not meaningful on 72h sample |
| Total R | Should be close to 0 or slightly positive/negative |
| Errors | 0 |
| Open positions stuck | 0 |

---

## Deliverables

### 1. `scripts/clear_test_data.py`
Clears signals, trades, positions, orders, equity_log for a fresh test run:
```python
async def clear_test_data():
    async with async_session() as db:
        await db.execute(delete(Trade))
        await db.execute(delete(Position))
        await db.execute(delete(Order))
        await db.execute(delete(Signal))
        await db.execute(delete(EquityLog))
        await db.execute(delete(DailySummary))
        await db.commit()
```

### 2. `scripts/paper_test_report.py`
Generates a formatted report at test completion:
```bash
python scripts/paper_test_report.py --start "2026-10-10 12:00" --end "2026-10-13 12:00"
```

### 3. Test Report Document
Format:
```
FUBOT Paper Trading Test Report
===============================
Period: 2026-10-10 12:00 UTC → 2026-10-13 12:00 UTC (72h)
Mode: PAPER (testnet)

SIGNALS
  Total generated: X
  Triggered (traded): X
  Skipped (R:R): X
  Skipped (trend): X
  Expired: X

TRADES
  Total closed: X
  Wins: X (X%)
  Losses: X (X%)
  Avg R: X.X
  Best trade: X.XR
  Worst trade: X.XR
  Total R: X.X

POSITIONS
  Open at end: X
  Largest: X.X USDT

SYSTEM
  Errors: X
  Uptime: X%
  Alerts: X
```

### 4. Sign-Off
If all checks pass:
- System is ready for Phase 4 (live trading connection)
- Any errors found → fix before proceeding

---

## Dependencies
- **Requires**: Phase 3 Steps 1-6 all complete

## Files Modified/Created
- `scripts/clear_test_data.py`
- `scripts/paper_test_report.py`

## Next Step
**Phase 3 Complete!** — Phase 4 Step 1: Bot Engine Loop

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
