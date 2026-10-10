# Phase 6 — Step 6: README.md Documentation
## FUBOT: Complete Documentation for Future Developers

---

## Objective
Write a comprehensive README.md for `/home/vick/fubot/README.md` covering setup, configuration, operation, troubleshooting, and architecture. This is the definitive reference for anyone who needs to understand or modify FUBOT.

---

## Context
- **Depends on**: All phases complete, all tests passing
- **Audience**: Future developer (or you, in 6 months) who needs to understand, maintain, or extend FUBOT

---

## README.md Template

Create `/home/vick/fubot/README.md`:

```markdown
# FUBOT — Automated Binance Futures Trading Bot

**URL**: https://fubot.bedikian.cc
**Version**: 1.0.0
**License**: Private — All rights reserved

> ⚠️ **DISCLAIMER**: This software is for educational and informational purposes only.
> It is not financial advice. Trading futures carries a high risk of loss.
> Use at your own risk. The authors assume no liability for any financial losses.

---

## Overview

FUBOT (Fu-tures **BOT**) is a fully automated Binance Futures USDS-M trading bot that executes
a validated multi-strategy trading system. It runs 24/7 on a VPS, analyzes markets in real-time,
and manages positions with structural risk rules.

**Key Features:**
- Multi-timeframe strategy (1H trend bias + 5m/15m entries)
- Three entry types: Fair Value Gap (FVG), 3-Candle Liquidity Sweep, Change of Character (CHoCH)
- Structural stops (not monetary) — stops placed beyond swing highs/lows
- ≥3:1 Risk:Reward ratio required before any trade
- Break-even trailing stop (moves to BE at new BOS in favor)
- -3R daily loss limit (bot stops automatically)
- Real-time React dashboard with live WebSocket updates
- Paper trading mode (testnet) before going live

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     VPS (10.10.30.25)                       │
│                                                             │
│   Nginx (SSL, Reverse Proxy)                                │
│   ├── /api/* → FastAPI (port 8000)                        │
│   └── /ws   → WebSocket (port 8000)                       │
│                                                             │
│   FastAPI Backend (uvicorn, async)                         │
│   ├── Bot Engine (main async loop, 60s interval)           │
│   ├── Signal Service (FVG, 3CANDLE, CHoCH detection)      │
│   ├── Order Service (Binance Futures API)                  │
│   ├── Risk Service (position sizing, daily limits)         │
│   └── WebSocket Manager (broadcasts to dashboards)        │
│                                                             │
│   PostgreSQL (port 5432)                                    │
│   └── Signals, Positions, Trades, Settings, Users           │
│                                                             │
│   Frontend (Vite, React, served as static files)          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11+, async) |
| Database | PostgreSQL 15+ with async SQLAlchemy |
| Frontend | React 18 + TypeScript + Vite |
| State | Zustand |
| Charts | TradingView Lightweight Charts |
| Styling | Tailwind CSS |
| Auth | JWT (httpOnly cookies) |
| Exchange | Binance Futures USDS-M |
| Deployment | Nginx + systemd + Let's Encrypt |
| OS | Debian 13 (Trixie) |

---

## Quick Start

### Prerequisites

- VPS (Debian 12+) with SSH access
- Node.js 18+ and Python 3.11+
- PostgreSQL 15+
- Binance Futures account with API key (futures trading permission)

### 1. Clone the project

```bash
git clone https://github.com/YOUR_REPO/fubot.git
cd fubot
```

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment variables
cp ../.env.example .env
nano .env  # Fill in DATABASE_URL, FUBOT_SECRET_KEY, FUBOT_ENCRYPTION_KEY
```

### 3. Database Setup

```bash
# Ensure PostgreSQL is running
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create the database
sudo -u postgres psql -c "CREATE DATABASE fubot;"
sudo -u postgres psql -c "CREATE USER fubot_user WITH PASSWORD 'your_db_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE fubot TO fubot_user;"
sudo -u postgres psql -d fubot -c "GRANT ALL ON SCHEMA public TO fubot_user;"

# Run migrations
alembic upgrade head
```

### 4. Create Admin User

```bash
cd /home/vick/fubot
python3 scripts/create_admin.py --username admin --password YOUR_STRONG_PASSWORD
```

### 5. Start Backend

```bash
cd /home/vick/fubot/backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

### 7. Access Dashboard

Open `http://localhost:5173` (or `https://fubot.bedikian.cc` in production).

---

## Configuration

### Environment Variables (`.env`)

```bash
# FUBOT Core
FUBOT_ENV=production                    # development | production
FUBOT_SECRET_KEY=your-jwt-secret        # Min 32 chars, random
FUBOT_ENCRYPTION_KEY=your-aes-key       # 32-byte key for AES-256

# Database
DATABASE_URL=postgresql+asyncpg://fubot_user:password@localhost:5432/fubot

# Binance
BINANCE_API_KEY=your-binance-api-key
BINANCE_API_SECRET=your-binance-secret
BINANCE_TESTNET=false                    # true for paper trading first

# Admin User (first run)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=set-strong-password
```

### Trading Parameters

| Parameter | Default | Description |
|---|---|---|
| `risk_percent` | 2% | Balance risked per trade |
| `max_positions` | 5 | Max concurrent positions |
| `daily_loss_limit_r` | 3R | Daily loss limit (-3R) |
| `rr_min` | 3.0 | Minimum R:R ratio |
| `check_interval_seconds` | 60 | Bot scan interval |
| `max_leverage_BTC` | 10x | Max leverage for BTC |
| `max_leverage_alts` | 5x | Max leverage for altcoins |

---

## Bot Modes

| Mode | Description | API Endpoint |
|---|---|---|
| `paper` | Testnet — no real money | `POST /api/bot/start {"mode":"paper"}` |
| `live` | Real Binance Futures | `POST /api/bot/start {"mode":"live"}` |

**Always start with paper mode and run for at least 1 week before going live.**

---

## API Reference

### Authentication

```
POST /api/auth/login
  Body: { username, password }
  Response: { token, user }
  Sets: httpOnly JWT cookie

POST /api/auth/logout
  Clears cookie

GET /api/auth/me
  Returns current user
```

### Bot Control

```
POST /api/bot/start     — Start bot (paper or live)
POST /api/bot/stop      — Stop bot
GET  /api/bot/status    — Current bot status
```

### Dashboard

```
GET /api/dashboard/overview  — Bot status, positions, equity curve
GET /api/dashboard/today     — Today's P&L, daily limit status
```

### Positions

```
GET /api/positions           — All positions (filter by status, symbol)
GET /api/positions/:id       — Single position
PATCH /api/positions/:id     — Update SL/TP
```

### Trades

```
GET /api/trades              — Trade history (filter + pagination)
GET /api/trades/stats        — Win rate, avg R, best/worst trade
```

### Signals

```
GET /api/signals             — All signals (filter by status, symbol)
GET /api/signals/active      — Currently pending signals
```

### Settings

```
GET  /api/settings/trading     — Current trading parameters
PUT  /api/settings/trading     — Update parameters
GET  /api/settings/symbols     — Per-symbol config
PUT  /api/settings/symbols/:s  — Update symbol config
```

### WebSocket

```
WS /ws
  Send: { type: "auth", data: { token: "..." } }
  Receive: position_update, pnl_update, bot_status, alert
```

---

## Directory Structure

```
/home/vick/fubot/
├── backend/
│   ├── main.py                # FastAPI app entry
│   ├── config.py              # Environment config
│   ├── database.py            # Async SQLAlchemy
│   ├── encryption.py          # AES-256 encryption
│   ├── models/                # ORM models
│   ├── schemas/               # Pydantic schemas
│   ├── routers/               # API route modules
│   ├── services/              # Business logic
│   ├── core/                  # Bot engine, scanners
│   └── indicators/            # TA library (SMA, RSI, FVG...)
├── frontend/
│   ├── src/
│   │   ├── api/               # Axios client, WebSocket
│   │   ├── hooks/             # React hooks
│   │   ├── store/             # Zustand stores
│   │   ├── pages/             # React pages
│   │   └── components/        # UI components
│   └── dist/                  # Production build
├── nginx/
│   └── fubot.conf             # Nginx config
├── scripts/
│   ├── create_admin.py        # Create admin user
│   └── init_db.py             # Initialize DB tables
├── alembic/                   # DB migrations
├── fubot.service              # systemd unit
└── README.md
```

---

## systemd Service

```bash
# Install
sudo ln -sf /home/vick/fubot/fubot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fubot
sudo systemctl start fubot

# Commands
sudo systemctl status fubot
sudo systemctl restart fubot
sudo systemctl stop fubot
journalctl -u fubot -f
```

---

## Troubleshooting

### Bot won't start

```bash
# Check logs
sudo journalctl -u fubot -n 50

# Common causes:
# - DATABASE_URL wrong → check PostgreSQL is running
# - FUBOT_ENCRYPTION_KEY missing → generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
# - Binance API key invalid → test with python-binance directly
```

### No signals detected

- Market conditions may not match strategy rules (check RSI zones, FVG formation)
- Indicators may not be loading correctly → check `journalctl -u fubot` for indicator errors
- Increase `check_interval_seconds` to see more frequent updates

### Order not placed

- Binance API key may lack futures permissions
- Testnet must be enabled if in paper mode: `BINANCE_TESTNET=true`
- Check `order_service.py` logs for specific rejection reason

### WebSocket not updating on dashboard

- Check browser console for connection errors
- Ensure `nginx.conf` has `proxy_set_header Upgrade $http_upgrade` for `/ws`
- Check `ws_manager.py` logs

### 502 Bad Gateway

- Backend not running → `sudo systemctl restart fubot`
- Port conflict → `sudo lsof -i :8000`

### Daily limit won't reset

- Limit resets at 00:00 UTC
- Manual reset: `python3 scripts/reset_daily_limit.py`
- Or wait for midnight UTC

---

## Common Commands

```bash
# Restart bot
sudo systemctl restart fubot

# View live logs
sudo journalctl -u fubot -f

# Check bot status
curl -H "Authorization: Bearer $(cat /tmp/fubot_token)" \
  https://fubot.bedikian.cc/api/bot/status

# Open a position manually (paper)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/start \
  -H "Content-Type: application/json" \
  -d '{"mode":"paper"}'

# Force stop
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://fubot.bedikian.cc/api/bot/stop

# Reset daily limit
python3 -c "
import asyncio
from database import async_session
from core.daily_reset import DailyReset
async def reset():
    async with async_session() as db:
        dr = DailyReset(db)
        await dr.force_reset()
asyncio.run(reset())
"
```

---

## Security Checklist

- [ ] API key has NO withdrawal permission on Binance
- [ ] API key restricted to Futures only
- [ ] `FUBOT_ENCRYPTION_KEY` is random, 32+ chars
- [ ] `FUBOT_SECRET_KEY` is random, 32+ chars
- [ ] SSL certificate is valid (Let's Encrypt auto-renew)
- [ ] Bot runs as non-root user (`vick`)
- [ ] Firewall only allows 22, 80, 443
- [ ] No secrets in logs (`grep` check passed)
- [ ] JWT uses httpOnly + Secure cookies

---

## Performance Notes

- Bot uses ~500MB RAM, 1 CPU core
- PostgreSQL uses ~200MB RAM
- Frontend static files: ~50MB
- Total: ~750MB RAM on VPS
- Scan cycle: ~15-30 seconds for 8 symbols

---

## Strategy Summary

**Entry Types:**
1. **FVG** — Fair Value Gap (three-bar imbalance) — limit order at 50% retracement
2. **3-Candle** — Liquidity sweep / 3-candle pattern — entry on BOS of candle 1
3. **CHoCH** — Change of Character — trend shift signal

**Trend Filter:** 50 SMA on 1H (above = bearish, below = bullish)
**RSI Confirmation:** <30 (bullish), >70 (bearish)
**Stop:** Beyond nearest swing high/low (structural)
**Target:** ≥3× stop distance
**Position Sizing:** 1-2% of balance
**Daily Limit:** -3R hard stop
**Exit:** Move SL to BE at new BOS in favor

---

## License

Private. All rights reserved. Not for redistribution.

---

*Built with the Crypto Banter / Nitro Trades 6-pillar strategy framework.*
```

---

## Deliverables

1. **`/home/vick/fubot/README.md`** — Complete, accurate documentation
2. All environment variables documented
3. All API endpoints documented
4. Troubleshooting section covers all common issues
5. Security checklist completed
6. README renders correctly on GitHub (if applicable)

---

## Final Checklist Before Going Live

- [ ] All 48 step files created ✓
- [ ] Backend runs without errors
- [ ] Frontend builds without TypeScript errors
- [ ] Bot starts and scans symbols
- [ ] Paper trade executes correctly
- [ ] Daily loss limit enforced at -3R
- [ ] WebSocket live updates working
- [ ] HTTPS + SSL certificate valid
- [ ] All 6 security checks passed
- [ ] README.md written and accurate
- [ ] User has reviewed and approved

---

## Ready for Production

FUBOT is now ready for live trading after:
1. Paper trading validation (1+ week)
2. User's manual review of all settings
3. Confirmation of Binance API key permissions
4. Signed security checklist

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
