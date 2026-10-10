# Phase 4 — Step 4: Connect Live + 72h Stability Test
## FUBOT: Real Binance Futures — First Live Trades

---

## Objective
Switch the bot from paper trading to live trading mode on real Binance Futures, and run a 72-hour stability test to verify the system handles real market conditions.

---

## Context
- **Mode switch**: Change `BINANCE_TESTNET=false` in environment
- **API key**: Use the real Binance API key (futures permissions only — no withdrawal)
- **This is the big moment**: Real money on the line — be cautious
- **Depends on**: Phase 4 Steps 1-3, Phase 3 Paper Test passed

---

## Pre-Live Checklist

### Environment Setup
```bash
# .env on VPS (change from testnet to live)
BINANCE_TESTNET=false

# Verify API key is correct:
# - Has Futures trading permission
# - No withdrawal permission
# - IP restriction if possible
```

### API Key Verification (Read-Only Test First)
```python
# Before starting the bot, run this to verify the key works:
from backend.services.binance_service import BinanceService

svc = BinanceService(api_key=REAL_KEY, api_secret=REAL_SECRET, testnet=False)

# Read-only calls (verify key works):
balance = await svc.get_account_balance()
print(f"Balance: {balance}")

positions = await svc.get_all_positions()
print(f"Open positions: {positions}")

klines = await svc.get_klines("BTCUSDT", "1h", 5)
print(f"Recent BTC klines: {len(klines)}")

# Verify these all return real data before proceeding
```

### Position Size Reality Check
With **$180 account** and **1% risk**:
- Risk per trade = $1.80
- With 10× leverage, position = ~$180 notional
- Stop distance of 1% = $1.80 risk ✓
- Commission on $180 at 0.04% = $0.072 ≈ 1.3R of commission
- This is acceptable

### Verify Daily Loss Limit Is Working
Before going live, temporarily set `daily_loss_limit_r = -0.5R` and run a few simulated losing trades. Verify the bot actually stops.

---

## Go-Live Procedure

### 1. Stop the bot
```bash
systemctl stop fubot
```

### 2. Update .env
```bash
# On VPS
nano /home/vick/fubot/.env
# Change: BINANCE_TESTNET=false
```

### 3. Start in PAPER mode first (one more time)
```bash
# Start in paper to verify no crashes on real API
systemctl start fubot
sleep 30
systemctl status fubot
journalctl -u fubot -n 30 --no-pager
```

### 4. Check for real data
```sql
-- On VPS:
psql $DATABASE_URL -c "select symbol, entry_price, quantity from positions where status='OPEN';"
-- Should show real positions if any were opened on testnet
```

### 5. Clear test positions
```bash
# If any test positions exist, close them manually on Binance website
# Then clear:
python scripts/clear_test_data.py
```

### 6. Switch to LIVE mode via API
```bash
curl -X POST https://fubot.bedikian.cc/api/bot/start \
  -H "Cookie: fubot_token=..." \
  -H "Content-Type: application/json" \
  -d '{"mode": "live"}'
```

### 7. Watch the logs
```bash
journalctl -u fubot -f --since "5 minutes ago"
```
Look for: `🚀 FUBOT started in LIVE mode`

---

## 72-Hour Live Stability Test

### Monitor
- **Every 6 hours**: Check journal for errors
- **Every 12 hours**: Check positions and trades
- **Every 24 hours**: Review daily summary, verify P&L makes sense

### Key Things to Watch
1. **No unauthorized trades** — bot only trades on signals it detects
2. **Position sizes are correct** — $1.80 risk per trade
3. **SL and TP placed immediately** after entry
4. **No duplicate orders** — each signal creates exactly one entry order
5. **Daily limit enforced** — if losses mount, bot stops
6. **WebSocket updates reaching dashboard** — live P&L updates

### Expected Results
| Metric | Value |
|---|---|
| Trades | 1-5 (depends on market) |
| Errors | 0 |
| Unauthorized trades | 0 |
| Commission | ~$0.05-0.20 per trade |
| Bot uptime | 100% |

### Completion
If 72 hours pass with:
- 0 errors
- All trades logged correctly
- Dashboard live
- No unauthorized orders

→ **System is production ready** → Proceed to Phase 5 (Dashboard)

---

## Deliverables

### 1. `scripts/go_live_checklist.md`
A checklist document with all the steps above in checklist form.

### 2. Sign-off Report
Generate a report:
```
FUBOT Live Trading Test Report
============================
Period: 2026-XX-XX → 2026-XX-XX (72h)
Mode: LIVE

TRADES
  Total: X
  PnL USDT: $X.XX
  PnL R: X.XR
  Commission paid: $X.XX

SYSTEM
  Errors: X
  Uptime: 100%
  Bot restarts: X
  Memory leaks: none

DASHBOARD
  WebSocket latency: <1s
  All pages loading: ✓

RESULT: ✅ READY FOR PRODUCTION
```

---

## Dependencies
- **Requires**: Phase 4 Steps 1-3, Phase 3 Step 7 passed

## Next Step
**Phase 4 Complete!** — Phase 5: React Dashboard

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
