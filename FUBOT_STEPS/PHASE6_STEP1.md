# Phase 6 — Step 1: End-to-End Trade Flow Test
## FUBOT: Validate the Complete Trading Pipeline

---

## Objective
Validate the complete trading pipeline from signal detection → order placement → position tracking → P&L calculation → trade logging. All components must work together correctly.

---

## Context
- **Depends on**: Phases 1–5 complete (backend + frontend built)
- **Mode**: Paper trading (testnet) — NO real money
- **Test symbol**: BTCUSDT on Binance Testnet Futures
- **Expected flow**: Signal detected → decision made → test order placed → position tracked → trade closed → P&L logged

---

## Prerequisites

1. Backend running: `cd /home/vick/fubot/backend && uvicorn main:app --host 0.0.0.0 --port 8000`
2. Frontend running: `cd /home/vick/fubot/frontend && npm run dev`
3. Admin user created via `scripts/create_admin.py`
4. Binance testnet API key configured (or real key in paper mode)
5. VPS accessible via SSH

---

## Step-by-Step Validation

### Test 1: Authentication

```bash
# Login via API
curl -X POST https://fubot.bedikian.cc/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_ADMIN_PASSWORD"}'
```

Expected: Returns JWT token. Copy the token.

```bash
TOKEN="your_token_here"

# Verify token works
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/auth/me
```

Expected: `{"user":{"id":1,"username":"admin"}}`

---

### Test 2: Settings API

```bash
# Get current trading settings
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/settings/trading

# Enable BTCUSDT symbol
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/settings/symbols/BTCUSDT \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"max_leverage":10}'
```

Expected: Both return 200 OK.

---

### Test 3: Bot Start (Paper Mode)

```bash
# Start bot in paper mode
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/start \
  -H "Content-Type: application/json" \
  -d '{"mode":"paper"}'

# Check bot status
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/status
```

Expected:
```json
{
  "status": "running",
  "mode": "paper",
  "uptime_seconds": 0,
  "scan_count": 0
}
```

---

### Test 4: Signal Detection (Wait 2 Minutes)

Let the bot run for at least 2 minutes. Monitor signals via the dashboard or API:

```bash
# Check for signals
curl -H "Authorization: Bearer $TOKEN" \
  "https://fubot.bedikian.cc/api/signals?status=PENDING&symbol=BTCUSDT"
```

Expected: Either a signal is found with:
- `strategy_type`: FVG or 3CANDLE or CHoCH
- `rr_ratio`: ≥ 3.0
- `status`: PENDING

Or no signal yet (acceptable if market conditions don't match). Let it run up to 30 minutes.

---

### Test 5: Manual Signal Trigger (If No Signal Fires)

If no signal appears naturally after 30 minutes, trigger a test signal manually by forcing a known setup:

1. Open the FUBOT dashboard at `https://fubot.bedikian.cc`
2. Navigate to **Scanner**
3. Manually emit a test signal via the backend Python shell:

```python
# On the VPS, run:
cd /home/vick/fubot/backend
python3 -c "
import asyncio
from database import async_session
from models.signal import Signal
from datetime import datetime

async def add_test_signal():
    async with async_session() as db:
        signal = Signal(
            symbol='BTCUSDT',
            strategy_type='FVG',
            direction='LONG',
            entry_zone_min=61000.0,
            entry_zone_max=61200.0,
            stop_loss=60000.0,
            take_profit=64000.0,
            rr_ratio=3.0,
            risk_r=1.0,
            status='PENDING',
            trend_bias='BULLISH',
            rsi_zone='OVERSOLD',
            volume_confirmed=True,
            created_at=datetime.utcnow()
        )
        db.add(signal)
        await db.commit()
        print(f'Created signal ID: {signal.id}')

asyncio.run(add_test_signal())
"
```

Expected: Signal appears in Scanner page within 60 seconds.

---

### Test 6: Execute Paper Trade

Wait for the bot to execute the paper trade, or trigger manually:

```bash
# Monitor positions
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/positions
```

Expected (after execution): Position appears with:
- `symbol`: BTCUSDT
- `side`: LONG or SHORT
- `quantity`: > 0
- `entry_price`: > 0

---

### Test 7: P&L Updates

```bash
# Check today's P&L
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/dashboard/today
```

Expected: Shows today's stats including trades_count ≥ 1 and pnl_r (may be 0 if trade hasn't closed yet).

---

### Test 8: Bot Stop

```bash
# Stop the bot
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/stop

# Verify stopped
curl -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/status
```

Expected: `{"status": "stopped", ...}`

---

### Test 9: Dashboard Integration

1. Open `https://fubot.bedikian.cc` in browser
2. Login with admin credentials
3. Check **Overview** — should show bot status (paper), open positions, equity curve
4. Check **Trade Log** — should show any completed paper trades
5. Check **Scanner** — should show signals
6. Check **Settings** — should show current configuration
7. Check **WebSocket** — data should auto-update without refresh

---

## Validation Checklist

Mark each as **PASS** or **FAIL**:

- [ ] Auth login returns JWT token
- [ ] Auth /me endpoint returns user info
- [ ] Bot starts in paper mode
- [ ] Bot status endpoint reflects running state
- [ ] Signals appear in the database/scanner
- [ ] Paper trade executes (or test signal + manual trigger)
- [ ] Position appears in positions endpoint
- [ ] Dashboard reflects position updates in real-time
- [ ] Bot stops cleanly
- [ ] No errors in backend logs (`journalctl -u fubot -f`)

---

## Expected Issues and Fixes

| Issue | Fix |
|---|---|
| Bot returns 401 on start | JWT token not being passed; check frontend `startBot()` |
| No signals detected | Increase `check_interval_seconds` or wait longer; check indicator code |
| Order not placed | Binance testnet API key may be invalid; check `binance_service.py` logs |
| WebSocket not updating | Check `Access-Control-Allow-Credentials` header; verify WS route in `main.py` |
| Frontend 401 loops | Clear cookies, re-login; check `apiClient.interceptors.response` |

---

## Deliverables

1. All 9 tests documented above passing
2. Bot runs for ≥1 hour without crash
3. Full trade cycle completed: signal → order → position → trade log
4. Dashboard reflects live state

---

## Next Step
Phase 6 Step 2: Daily Loss Limit Enforcement Test

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
