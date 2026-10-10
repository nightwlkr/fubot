# FUBOT — Automated Binance Futures Trading Bot
## Comprehensive Product Plan & Build Guide
**URL:** https://fubot.bedikian.cc
**Version:** 1.0.0
**Date:** 2026-10-10
**Status:** Planning Complete — Ready for Development

---

## 1. Product Overview

### What Is FUBOT?
FUBOT (Fu-tures **BOT**) is a fully automated Binance Futures USDS-M trading bot that executes a validated multi-strategy trading system derived from Crypto Banter's 6-pillar framework. It runs 24/7 on a VPS, analyzes markets in real-time, and manages positions with structural risk rules — no manual trading required after configuration.

### Core Value Proposition
- **Automated**: No manual order placement after setup
- **Strategic**: Combines FVG entries, liquidity sweep detection, CHoCH/BOS structural analysis, and volume confirmation
- **Risk-controlled**: Hard -3R daily loss limit, structural stops (not monetary), 1-2% risk per trade
- **Real-time dashboard**: Live positions, P&L, trade log, equity curve — accessible from any browser
- **Self-improving**: Logs every signal and trade for later evaluation and strategy refinement

### Target User
Single trader, ~$180 account balance, wants full automation. No coding knowledge required to operate — agents will build it; user manages via dashboard.

### Exchange & Market
- **Exchange**: Binance Futures USDS-M
- **Symbols**: BTCUSDT, ETHUSDT, and major altcoins (BNBUSDT, SOLUSDT, etc.)
- **Account Type**: Isolated margin (one position per symbol, per bot)
- **Leverage**: Configurable per symbol (default 10x for BTC, 5x for alts)

---

## 2. Validated Trading Strategy

> This strategy is derived from 7 Crypto Banter/Nitro Trades videos and 1 TradingView indicator analysis. All rules below have been validated as programmatically definable.

### 2.1 Multi-Timeframe Analysis
| Timeframe | Purpose | Indicators |
|---|---|---|
| 1H | Trend bias | 50 SMA direction, RSI zone (<30 / >70) |
| 5m / 15m | Entry signals | FVG, BOS/CHoCH, 3-Candle pattern |
| 1m | Execution timing | Limit order placement at FVG midpoint |

**Trend Bias Rules:**
- 50 SMA above price → **bearish bias** (only short)
- 50 SMA below price → **bullish bias** (only long)
- RSI < 30 → oversold (bullish bias confirmed)
- RSI > 70 → overbought (bearish bias confirmed)
- Bot takes **only the bias-direction trades** 80% of the time; counter-trend trades are scalp-only with tight stops

---

### 2.2 Entry Signal Types

#### A. Fair Value Gap (FVG) Entry
1. Identify FVG on 5m/15m — three-candle imbalance where the middle candle's wick doesn't overlap the body of adjacent candles
2. Place **limit buy order at the 50% midpoint** of the FVG zone
3. Stop loss: **1 ATR beyond the FVG extreme** (swing high/low)
4. Take profit: **≥ 3× the stop distance** (3:1 R:R minimum; 1:4 R:R as conservative default)
5. Entry confirmed only if:
   - Higher timeframe trend bias aligns with entry direction
   - Volume on the breakout candle confirms direction (buy volume > sell volume for longs)

#### B. 3-Candle / Liquidity Sweep Entry
1. Candle 1 establishes a range (high-low body)
2. Candle 2 sweeps beyond Candle 1's range (wick past the high/low), then **closes back within** Candle 1's range
3. Candle 3 breaks out in the **opposite direction** of the sweep — this is the entry
4. Stop: beyond Candle 2's sweep wick (the liquidity trap)
5. TP: at next structural level (previous high/low), minimum 3:1 R:R
6. Equivalent to FVG entry mechanically — both are capturing the same liquidity grab behavior

#### C. CHoCH (Change of Character) Entry
1. Price breaks below last higher low (bullish CHoCH) → bullish entry signal
2. Price breaks above last lower high (bearish CHoCH) → bearish entry signal
3. Wait for pullback entry (limit order at 0.5-0.618 Fibonacci retracement of the CHoCH leg)
4. Stop: beyond the pivot point that defined the CHoCH
5. TP: structural level, minimum 3:1 R:R

---

### 2.3 Exit Management

#### Trailing Stop to Break-Even (BE)
- When price creates a **new BOS (Break of Structure)** in your favor → move stop loss to **BE immediately**
- This locks in profit without cutting the winner short

#### Take Profit (TP)
- TP set at **structural liquidity level** (previous range high/low, swing high/low)
- Minimum TP: **3× the stop distance** (3:1 R:R)
- Conservative mode: **1:4 R:R** (higher win rate needed, but more profitable over time)
- If TP cannot reach 3× without passing resistance → **skip the trade**

#### Emergency Exit
- **-3R cumulative daily loss** → bot stops all trading for the day, sends alert
- **-1R on a single trade** → review position, may close manually

---

### 2.4 Position Sizing

#### Base Sizing (Auto-Compounding)
```
Risk Per Trade = 1-2% of current account balance
Position Size = Risk Per Trade ÷ Stop Distance (in %)
```

Example with $180 balance and 1% risk:
- Account risk = $180 × 0.01 = **$1.80 per trade**
- Stop distance = 1% of price → Position = $1.80 ÷ 0.01 = **$180 notional**
- At 10× leverage, margin required = $18

#### Milestone Sizing (Wall Street Raise)
- After **+10% account growth** → increase position size by 10%
- First scaled-up trade is statistically a loser — prepare for it psychologically
- Used when consistent profitability is demonstrated

---

### 2.5 Daily Operating Routine (Bot-Automated)
1. **Check trend bias** (1H): 50 SMA direction + RSI zone
2. **Scan for setups** (5m/15m): FVG, 3-Candle, CHoCH patterns
3. **Filter by volume**: breakout must confirm with dominant volume side
4. **Calculate structural stop and TP** → verify R:R ≥ 3:1
5. **Execute limit order** at FVG midpoint or 3-Candle confirmation
6. **Monitor**: trail stop to BE at new BOS
7. **Enforce -3R daily loss limit**
8. **Log everything** for later evaluation

---

### 2.6 Key Insights Validated Across All Videos
| Insight | Implication |
|---|---|
| 30% win rate + 3R targets = profitable (+2R over 10 trades) | Win rate is less important than R:R |
| 70% win rate + 2R targets = losing (-1R over 10 trades) | Never accept 2:1 R:R |
| Structural stops (not monetary) are mandatory | Stops must be placed at swing high/low, not arbitrary $ |
| Quality over quantity: 1-3 setups/week is acceptable | Don't overtrade |
| Daily loss limit -3R is the hard stop | No daily profit target — let process generate returns |
| Auto-compounding: risk fixed % of balance | Size grows with account automatically |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         BROWSER                                  │
│   https://fubot.bedikian.cc (React Dashboard)                  │
│   Real-time: WebSocket connection to FastAPI                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTPS / WSS
┌──────────────────────▼──────────────────────────────────────────┐
│                      VPS (10.10.30.25)                           │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    FastAPI (Backend)                      │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │    │
│  │  │  Bot     │  │  API     │  │ WebSocket│  │ Trade  │  │    │
│  │  │ Engine   │  │ Routes   │  │ Server   │  │ Manager│  │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │    │
│  │  │ Signal   │  │ Risk     │  │ Order    │  │ Log    │  │    │
│  │  │ Analyzer │  │ Manager  │  │ Executor │  │ System │  │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │    │
│  └──────────────────────┬──────────────────────────────────┘    │
│                          │                                        │
│  ┌──────────────────────▼──────────────────────────────────┐    │
│  │               PostgreSQL Database                        │    │
│  │  positions | trades | signals | settings | equity_log  │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │           Binance Futures (fapi.binance.com)            │    │
│  │  REST API + WebSocket Market Data + Trade Execution     │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Component Descriptions

| Component | Technology | Responsibility |
|---|---|---|
| **Bot Engine** | Python / FastAPI | Core trading logic — analyzes markets, generates signals |
| **Signal Analyzer** | pandas-ta + custom | Detects FVG, 3-Candle, CHoCH, trend conditions |
| **Risk Manager** | Python | Enforces daily loss limits, position sizing, R:R checks |
| **Order Executor** | python-binance + async | Places/modifies/cancels orders on Binance |
| **Trade Manager** | Python | Tracks open positions, applies BE trailing, monitors TP |
| **WebSocket Server** | FastAPI + WebSocket | Pushes live data to React dashboard |
| **API Routes** | FastAPI | REST endpoints for dashboard, settings, history |
| **Log System** | PostgreSQL | Every signal, order, and trade is logged |
| **React Dashboard** | React + Vite + TS | Real-time trading dashboard |
| **PostgreSQL** | PostgreSQL | Persistent data store |

---

## 4. Tech Stack

### Backend
| Layer | Technology | Version | Notes |
|---|---|---|---|
| Language | Python | 3.11+ | Async-first for WebSocket + exchange calls |
| Framework | FastAPI | latest | REST + WebSocket in one process |
| ORM | SQLAlchemy 2.0 + asyncpg | latest | Async PostgreSQL access |
| Exchange API | python-binance (async) | latest | Futures REST + WebSocket |
| TA Library | pandas-ta | latest | Indicator calculations |
| Validation | pydantic | latest | Settings, schemas, API models |
| Migrations | alembic | latest | DB schema version control |
| Process manager | systemd | VPS-native | Keeps bot alive, auto-restarts |
| WSGI/Server | uvicorn | latest | ASGI server |

### Frontend
| Layer | Technology | Version | Notes |
|---|---|---|---|
| Framework | React | 18+ | Hooks-based |
| Build | Vite | latest | Fast HMR |
| Language | TypeScript | 5+ | Strict mode |
| Routing | React Router | v6 | Dashboard pages |
| State | Zustand | latest | Lightweight, WebSocket-friendly |
| Real-time | native WebSocket | browser | No Socket.io overhead |
| Charts | Lightweight Charts (TradingView) | latest | Official TradingView library |
| Styling | Tailwind CSS | latest | Utility-first |
| HTTP client | fetch API | native | No axios needed |
| Auth | JWT (stored in httpOnly cookie) | — | Dashboard login |

### Infrastructure
| Component | Technology | Notes |
|---|---|---|
| VPS | Existing (10.10.30.25) | Debian 13, same as BBOT |
| Database | PostgreSQL 15+ | Same VPS as BBOT |
| Domain | fubot.bedikian.cc | CNAME to VPS IP |
| SSL | Let's Encrypt (certbot) | HTTPS required for WebSocket |
| Reverse proxy | Nginx | SSL termination + WebSocket upgrade |
| Process control | systemd | fubot.service |

---

## 5. UI/UX Design

### 5.1 Design System
- **Theme**: Dark mode primary (trading dashboards are dark), light mode optional
- **Color palette**:
  - Background: `#0f1117` (near black)
  - Card: `#1a1d27` (dark slate)
  - Primary accent: `#00d4aa` (teal/green — profit, buy)
  - Danger accent: `#ff4757` (red — loss, sell)
  - Warning: `#ffa502` (amber)
  - Text primary: `#e8e8e8`
  - Text muted: `#6b7280`
- **Typography**: System font stack (no external font dependency)
- **Icons**: Lucide React (consistent, lightweight)

### 5.2 Dashboard Pages

#### Page 1: Overview / Home
**URL:** `/`
**Purpose:** At-a-glance bot status and today's performance

**Sections:**
- **Bot Status Card**: ON/OFF toggle, current mode (paper/live), connected account
- **Today's P&L Card**: Realized P&L, unrealized P&L, win rate today, trades today
- **Equity Curve**: Line chart of account balance over time (30-day view)
- **Quick Stats Row**: Total trades, best trade, worst trade, avg R per trade
- **Active Positions Table**: Symbol, side, size, entry price, current price, P&L%, stop, TP, age

#### Page 2: Trade Log
**URL:** `/trades`
**Purpose:** Full history of all executed trades

**Sections:**
- **Filters**: Date range, symbol, side (long/short), outcome (win/loss/breakeven)
- **Trade Table**: Date/time, symbol, side, entry price, exit price, size, P&L ($), P&L (R), strategy type (FVG/3CANDLE/CHOC), notes
- **Export**: CSV download of filtered trades
- **Pagination**: 50 trades per page

#### Page 3: Signal Scanner
**URL:** `/scanner`
**Purpose:** See what the bot is seeing — active signals and pending orders

**Sections:**
- **Active Signals**: Symbol, type, direction, zone price, status (pending/triggered/expired)
- **Pending Orders**: All limit orders waiting to fill — price, size, type, time placed
- **Market Mood**: BTC dominance, funding rates, open interest snapshot
- **Recent Signals Missed**: Signals detected but not traded (reason: R:R不足, trend filter failed, etc.)

#### Page 4: Performance
**URL:** `/performance`
**Purpose:** Detailed performance analytics

**Sections:**
- **Equity Curve**: Interactive TradingView chart with drawdown shading
- **Monthly Returns**: Bar chart, month by month
- **R-Multiple Distribution**: Histogram of trade outcomes in R units
- **Win Rate by Strategy**: FVG vs 3-Candle vs CHoCH performance breakdown
- **Consecutive Loss Streak**: Max drawdown in R
- **Average R per Month**: Key performance metric

#### Page 5: Settings
**URL:** `/settings`
**Purpose:** Configure all trading and bot parameters

**Sections:**
- **API Keys**: Encrypted storage of Binance API key + secret (masked display)
- **Trading Settings**:
  - Risk % per trade (default 1%, range 0.5-2%)
  - Max positions simultaneously (default 3)
  - Leverage per symbol (default 10x BTC, 5x alts)
  - Daily loss limit in R (default -3R)
  - R:R minimum (default 3:1, conservative 4:1)
  - Trend check interval (default 60 seconds)
- **Symbol Settings**: Enable/disable symbols, custom leverage per symbol
- **Strategy Toggles**: Enable/disable FVG / 3-Candle / CHoCH strategies
- **Dashboard Settings**: Theme, notification preferences

#### Page 6: Login
**URL:** `/login`
**Purpose:** Authenticate before accessing dashboard

**Sections:**
- Username + password form
- No registration (single-user, credentials set via env/config)

---

## 6. Database Schema

### Tables

#### `accounts`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| api_key_encrypted | TEXT | AES-encrypted, never plaintext |
| api_secret_encrypted | TEXT | AES-encrypted |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |
| is_active | BOOLEAN | |

#### `symbols`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| symbol | VARCHAR(20) UNIQUE | BTCUSDT, ETHUSDT, etc. |
| enabled | BOOLEAN | |
| max_leverage | INT | |
| position_limit_usdt | NUMERIC | |

#### `settings`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| key | VARCHAR(100) UNIQUE | Setting name |
| value | JSONB | Value + metadata (type, description) |
| updated_at | TIMESTAMPTZ | |

#### `signals`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| symbol | VARCHAR(20) | |
| strategy_type | VARCHAR(20) | FVG, 3CANDLE, CHOC |
| direction | VARCHAR(5) | LONG, SHORT |
| entry_zone_min | NUMERIC | |
| entry_zone_max | NUMERIC | |
| stop_loss | NUMERIC | |
| take_profit | NUMERIC | |
| rr_ratio | NUMERIC | |
| volume_confirmed | BOOLEAN | Volume bias matched direction |
| status | VARCHAR(20) | PENDING, TRIGGERED, EXPIRED, SKIPPED |
| created_at | TIMESTAMPTZ | |
| triggered_at | TIMESTAMPTZ | |
| skip_reason | TEXT | If status=SKIPPED |

#### `positions`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| signal_id | INT FK | Ref to signals table |
| symbol | VARCHAR(20) | |
| side | VARCHAR(5) | LONG, SHORT |
| entry_price | NUMERIC | |
| quantity | NUMERIC | |
| leverage | INT | |
| stop_loss | NUMERIC | |
| take_profit | NUMERIC | |
| status | VARCHAR(20) | OPEN, CLOSED, LIQUIDATED |
| opened_at | TIMESTAMPTZ | |
| closed_at | TIMESTAMPTZ | |

#### `trades`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| position_id | INT FK | Ref to positions |
| symbol | VARCHAR(20) | |
| side | VARCHAR(5) | |
| entry_price | NUMERIC | |
| exit_price | NUMERIC | |
| quantity | NUMERIC | |
| pnl_usdt | NUMERIC | Realized P&L |
| pnl_r | NUMERIC | P&L in R units |
| exit_reason | VARCHAR(30) | TP_HIT, SL_HIT, BE_TRAIL, MANUAL, LIQUIDATION |
| strategy_type | VARCHAR(20) | |
| created_at | TIMESTAMPTZ | |
| closed_at | TIMESTAMPTZ | |

#### `equity_log`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| balance_usdt | NUMERIC | Snapshot of account balance |
| recorded_at | TIMESTAMPTZ | |

#### `daily_summary`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| date | DATE UNIQUE | |
| total_trades | INT | |
| wins | INT | |
| losses | INT | |
| breakeven | INT | |
| total_pnl_r | NUMERIC | |
| total_pnl_usdt | NUMERIC | |
| worst_trade_r | NUMERIC | |
| best_trade_r | NUMERIC | |
| daily_loss_limit_hit | BOOLEAN | |

#### `orders`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| binance_order_id | BIGINT | |
| symbol | VARCHAR(20) | |
| side | VARCHAR(5) | |
| order_type | VARCHAR(10) | LIMIT, MARKET, STOP, TRAILING |
| price | NUMERIC | |
| quantity | NUMERIC | |
| filled_qty | NUMERIC | |
| status | VARCHAR(20) | NEW, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

#### `users`
| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| username | VARCHAR(50) UNIQUE | |
| password_hash | VARCHAR(255) | bcrypt |
| role | VARCHAR(20) | admin |
| created_at | TIMESTAMPTZ | |

---

## 7. API Design (FastAPI Routes)

### REST Endpoints

#### Authentication
```
POST /api/auth/login
  Body: { username, password }
  Response: { token, user: { id, username } }

POST /api/auth/logout
  Response: { ok: true }

GET /api/auth/me
  Response: { user: { id, username } }
```

#### Dashboard (Protected)
```
GET /api/dashboard/overview
  Response: {
    bot_status, today_pnl, open_positions[],
    equity_curve[{date, balance}],
    quick_stats: { total_trades, win_rate, best_trade, worst_trade }
  }

GET /api/dashboard/today
  Response: {
    pnl_r, pnl_usdt, trades_count, wins, losses,
    daily_limit_status: { used_r, limit_r, remaining_r }
  }
```

#### Positions (Protected)
```
GET /api/positions
  Query: ?status=OPEN&symbol=BTCUSDT
  Response: { positions[] }

GET /api/positions/:id
  Response: { position }

PATCH /api/positions/:id
  Body: { stop_loss?, take_profit? }
  Response: { position }
```

#### Trades (Protected)
```
GET /api/trades
  Query: ?page=1&limit=50&symbol=BTCUSDT&side=LONG&outcome=win&from=2026-01-01&to=2026-12-31
  Response: { trades[], total, page, pages }

GET /api/trades/:id
  Response: { trade }

GET /api/trades/stats
  Response: {
    total_trades, win_rate, avg_r,
    monthly_returns[{month, pnl_r}],
    rr_distribution[{bucket, count}]
  }
```

#### Signals (Protected)
```
GET /api/signals
  Query: ?status=PENDING&symbol=BTCUSDT
  Response: { signals[] }

GET /api/signals/active
  Response: { signals[] } — currently active/pending signals

GET /api/signals/missed
  Query: ?days=7
  Response: { signals[] } — skipped signals with reasons
```

#### Settings (Protected)
```
GET /api/settings
  Response: { settings{} } — public settings only (no secrets)

PUT /api/settings/trading
  Body: { risk_percent?, max_positions?, daily_loss_limit_r?, rr_min? }
  Response: { settings }

GET /api/settings/symbols
  Response: { symbols[] }

PUT /api/settings/symbols/:symbol
  Body: { enabled?, max_leverage? }
  Response: { symbol }
```

#### Orders (Protected)
```
GET /api/orders
  Query: ?status=NEW&symbol=BTCUSDT
  Response: { orders[] }

DELETE /api/orders/:id
  Response: { ok } — cancel a pending limit order
```

#### Bot Control (Protected)
```
POST /api/bot/start
  Response: { ok, status: "running" }

POST /api/bot/stop
  Response: { ok, status: "stopped" }

GET /api/bot/status
  Response: { status, mode: "live"|"paper", uptime, signals_today, trades_today }
```

#### Performance (Protected)
```
GET /api/performance/equity
  Query: ?days=365
  Response: { data[{date, balance}] }

GET /api/performance/monthly
  Response: { data[{month, pnl_r, trades, win_rate}] }

GET /api/performance/strategies
  Response: { data[{strategy, trades, win_rate, avg_r}] }
```

### WebSocket Endpoint

```
WS /ws
  Auth: token in first message { type: "auth", token: "..." }
  
  Server → Client messages:
    { type: "position_update", data: { position } }
    { type: "order_update", data: { order } }
    { type: "signal_update", data: { signal } }
    { type: "pnl_update", data: { pnl_r, pnl_usdt, open_count } }
    { type: "bot_status", data: { status, mode } }
    { type: "alert", data: { level: "info"|"warn"|"error", message } }
```

---

## 8. Security Design

### API Key Storage
- Binance API key + secret stored encrypted in PostgreSQL
- Encryption: AES-256-GCM, key derived from `FUBOT_ENCRYPTION_KEY` env var
- Key is never logged, never returned in any API response (masked as `****ABCD`)
- API key has futures trading permission only — no withdrawal permission

### Dashboard Authentication
- JWT tokens stored in httpOnly, secure cookie
- Passwords hashed with bcrypt (min 12 rounds)
- Failed login rate-limited: 5 attempts per 15 minutes per IP
- Single admin user (username + password set via environment variables on first deploy)

### Bot Security
- API key restricted to: enable futures, trade, read-only market data
- No withdrawal permissions on the API key
- All order modifications logged with timestamp
- Emergency stop endpoint requires authentication

### Network Security
- Nginx handles SSL termination (Let's Encrypt)
- WebSocket served over WSS (secure)
- VPS firewall: only ports 22 (SSH), 80 (HTTP), 443 (HTTPS) exposed
- Bot process runs as non-root user

---

## 9. Project Structure

```
/home/vick/fubot/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Environment config + validation
│   ├── database.py                 # Async SQLAlchemy setup
│   ├── encryption.py              # AES-256 key encryption utils
│   │
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── symbol.py
│   │   ├── signal.py
│   │   ├── position.py
│   │   ├── trade.py
│   │   ├── order.py
│   │   ├── equity_log.py
│   │   ├── daily_summary.py
│   │   └── user.py
│   │
│   ├── schemas/                   # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── position.py
│   │   ├── trade.py
│   │   ├── signal.py
│   │   ├── settings.py
│   │   └── bot.py
│   │
│   ├── routers/                   # FastAPI route modules
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── positions.py
│   │   ├── trades.py
│   │   ├── signals.py
│   │   ├── settings.py
│   │   ├── orders.py
│   │   ├── bot.py
│   │   └── performance.py
│   │
│   ├── services/                  # Business logic
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── binance_service.py     # Binance API wrapper
│   │   ├── signal_service.py      # Signal detection (FVG, 3CANDLE, CHOC)
│   │   ├── strategy_service.py    # Entry/exit decision logic
│   │   ├── risk_service.py        # Position sizing, daily limits
│   │   ├── order_service.py       # Order placement, modification
│   │   ├── trade_service.py       # Trade tracking, BE trailing
│   │   └── alert_service.py       # Notifications
│   │
│   ├── core/                      # Core trading components
│   │   ├── __init__.py
│   │   ├── bot_engine.py          # Main trading loop (async)
│   │   ├── market_scanner.py      # Scans symbols for setups
│   │   ├── signal_analyzer.py      # FVG, 3-Candle, CHoCH detection
│   │   ├── position_manager.py    # Tracks open positions
│   │   ├── websocket_manager.py    # Broadcasts to connected clients
│   │   └── daily_reset.py         # Daily loss limit check/reset
│   │
│   ├── indicators/                # Technical analysis
│   │   ├── __init__.py
│   │   ├── sma.py                 # 50 SMA
│   │   ├── rsi.py                 # RSI
│   │   ├── fvg.py                 # Fair Value Gap detection
│   │   ├── three_candle.py        # 3-Candle / Liquidity Sweep
│   │   ├── choch.py               # Change of Character detection
│   │   ├── bos.py                 # Break of Structure
│   │   ├── atr.py                 # ATR for stop/tp sizing
│   │   └── volume_profile.py      # Volume bias analysis
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging_utils.py
│       └── datetime_utils.py
│
├── frontend/
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/                    # API client + WebSocket client
│   │   │   ├── client.ts           # fetch wrapper with auth
│   │   │   ├── websocket.ts        # WebSocket connection manager
│   │   │   └── endpoints.ts        # API route constants
│   │   ├── hooks/                  # React hooks
│   │   │   ├── useAuth.ts
│   │   │   ├── useWebSocket.ts
│   │   │   ├── usePositions.ts
│   │   │   ├── useTrades.ts
│   │   │   └── useDashboard.ts
│   │   ├── store/                  # Zustand stores
│   │   │   ├── authStore.ts
│   │   │   ├── dashboardStore.ts
│   │   │   └── settingsStore.ts
│   │   ├── pages/
│   │   │   ├── Login.tsx
│   │   │   ├── Overview.tsx
│   │   │   ├── TradeLog.tsx
│   │   │   ├── Scanner.tsx
│   │   │   ├── Performance.tsx
│   │   │   └── Settings.tsx
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── Sidebar.tsx
│   │   │   │   └── Header.tsx
│   │   │   ├── dashboard/
│   │   │   │   ├── BotStatusCard.tsx
│   │   │   │   ├── PnLCard.tsx
│   │   │   │   ├── PositionsTable.tsx
│   │   │   │   └── EquityCurve.tsx
│   │   │   ├── trades/
│   │   │   │   └── TradeTable.tsx
│   │   │   ├── signals/
│   │   │   │   └── SignalCard.tsx
│   │   │   ├── charts/
│   │   │   │   └── EquityChart.tsx  # TradingView Lightweight Charts
│   │   │   └── ui/                 # Shared UI components
│   │   │       ├── Button.tsx
│   │   │       ├── Card.tsx
│   │   │       ├── Input.tsx
│   │   │       ├── Table.tsx
│   │   │       └── Badge.tsx
│   │   ├── types/
│   │   │   └── index.ts            # Shared TypeScript types
│   │   └── styles/
│   │       └── globals.css         # Tailwind + custom CSS
│   │
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── tailwind.config.js
│
├── nginx/
│   └── fubot.conf                  # Nginx config with SSL + WS proxy
│
├── scripts/
│   ├── init_db.py                  # Creates DB tables + default data
│   ├── create_admin.py             # Creates first admin user
│   └── setup_ssl.sh                # Certbot SSL setup
│
├── alembic/                        # DB migrations
│   ├── env.py
│   └── versions/
│
├── tests/
│   ├── backend/
│   │   ├── test_signal_analyzer.py
│   │   ├── test_risk_service.py
│   │   └── test_order_service.py
│   └── frontend/
│       └── ...
│
├── docker-compose.yml              # Local development
├── Dockerfile.backend
├── Dockerfile.frontend
├── pyproject.toml
├── requirements.txt
├── .env.example
├── fubot.service                  # systemd service file
└── README.md
```

---

## 10. Step-by-Step Build Plan

### Phase 1: Foundation (Infrastructure & Backend Core)
**Goal**: Bot runs headlessly, can connect to Binance, database is live

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 1.1 | Set up project structure, install Python deps, create `config.py`, `.env.example` | Backend Agent 1 | Project scaffold, config validated |
| 1.2 | Set up PostgreSQL tables with Alembic migrations | Backend Agent 1 | All tables created, migrations work |
| 1.3 | Implement AES-256 encryption for API key storage + `binance_service.py` wrapper | Backend Agent 1 | Encrypted API key storage + Binance connectivity |
| 1.4 | Implement `user.py` model + `auth_service.py` + JWT login endpoint | Backend Agent 1 | `/api/auth/login` works, token issued |
| 1.5 | Set up Nginx with SSL for `fubot.bedikian.cc` + WebSocket proxy | DevOps Agent | HTTPS + WSS working |
| 1.6 | Write `fubot.service` systemd unit, enable and start service | DevOps Agent | `systemctl status fubot` shows active |
| 1.7 | Create admin user via `scripts/create_admin.py` | DevOps Agent | Admin login credentials working |

**Validation**: `curl https://fubot.bedikian.cc/api/auth/me` returns 401 (no token); login with admin credentials returns JWT.

---

### Phase 2: Market Data & Signal Detection
**Goal**: Bot scans markets, detects all signal types, logs signals to DB

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 2.1 | Implement `binance_service.py` — REST market data (klines, orderbook) + WebSocket streams | Backend Agent 2 | `get_klines()`, `get_symbol_info()` working |
| 2.2 | Implement 50 SMA + RSI indicator modules | Backend Agent 2 | Trend bias correctly identified per symbol |
| 2.3 | Implement FVG detection (`indicators/fvg.py`) | Backend Agent 2 | FVGs detected and logged to `signals` table |
| 2.4 | Implement 3-Candle / Liquidity Sweep detection | Backend Agent 2 | 3-Candle patterns detected |
| 2.5 | Implement CHoCH + BOS detection | Backend Agent 2 | Structural breaks identified |
| 2.6 | Implement volume bias analysis (buy/sell volume ratio per candle) | Backend Agent 2 | Volume confirmation filter working |
| 2.7 | Implement `signal_service.py` — orchestrates all indicators, filters by R:R ≥ 3:1, logs to DB | Backend Agent 2 | Signals table populated, skip reasons recorded |
| 2.8 | Write unit tests for all indicator modules | Backend Agent 2 | Tests passing |

**Validation**: `signals` table has records for BTCUSDT with correct strategy types and R:R values.

---

### Phase 3: Order Execution & Risk Management
**Goal**: Bot executes orders, manages positions, enforces risk rules

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 3.1 | Implement `order_service.py` — async limit/market order placement via python-binance | Backend Agent 3 | Orders placed on Binance testnet |
| 3.2 | Implement `risk_service.py` — position sizing (fixed % of balance), R:R validation | Backend Agent 3 | Position size correctly calculated |
| 3.3 | Implement `strategy_service.py` — decides when to trade: signal present + trend aligns + R:R valid + no daily limit breach | Backend Agent 3 | Strategy decisions logged |
| 3.4 | Implement `position_manager.py` — tracks open positions, applies BE trailing (move SL to BE at new BOS) | Backend Agent 3 | SL moved to BE correctly |
| 3.5 | Implement `daily_reset.py` — tracks cumulative daily R, enforces -3R hard stop, resets at 00:00 UTC | Backend Agent 3 | Bot stops at -3R, resets at midnight |
| 3.6 | Implement `alert_service.py` — push notifications (optional: Telegram or email) | Backend Agent 3 | Alerts sent on trade events |
| 3.7 | Paper trading test: run bot for 24h, verify orders, P&L, no unauthorized trades | Backend Agent 3 | All trades logged correctly |

**Validation**: Open a position via dashboard or signal; verify order on Binance; check `positions`, `orders`, `trades` tables all consistent.

---

### Phase 4: Bot Engine (The Real-Time Loop)
**Goal**: Bot runs continuously, scans markets, executes signals

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 4.1 | Implement `bot_engine.py` — main async loop: check signals → evaluate → execute → manage | Backend Agent 2 | Loop runs, calls each service in order |
| 4.2 | Implement `market_scanner.py` — loops through enabled symbols, fetches data, runs signal detection | Backend Agent 2 | Scanner processes all enabled symbols |
| 4.3 | Implement `websocket_manager.py` — broadcasts position/order/pnl updates to connected dashboard clients | Backend Agent 2 | Dashboard receives live updates |
| 4.4 | Implement bot control endpoints: `POST /api/bot/start`, `POST /api/bot/stop`, `GET /api/bot/status` | Backend Agent 1 | Bot can be started/stopped from dashboard |
| 4.75 | Connect bot engine to Binance **live** futures API | Backend Agent 3 | First live trades executed |
| 4.8 | 72-hour stability test: bot runs without crash, all systems operational | Backend Agent 3 | Bot uptime confirmed |

**Validation**: Bot running; dashboard shows live positions and P&L; Telegram alerts firing.

---

### Phase 5: React Dashboard
**Goal**: Fully functional trading dashboard

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 5.1 | Set up React + Vite + TypeScript + Tailwind + Zustand | Frontend Agent | Dev server running |
| 5.2 | Implement Login page + JWT auth flow (store token in cookie) | Frontend Agent | Can log in, token persisted across refresh |
| 5.3 | Implement Sidebar navigation + routing | Frontend Agent | All 5 pages accessible |
| 5.4 | Implement Overview page: BotStatusCard, PnLCard, PositionsTable, EquityCurve | Frontend Agent | Overview renders live data |
| 5.5 | Implement WebSocket connection in React — live P&L, position updates | Frontend Agent | Real-time updates without polling |
| 5.6 | Implement Trade Log page with filters + pagination + CSV export | Frontend Agent | Trade history queryable |
| 5.7 | Implement Scanner page: active signals, pending orders, market mood | Frontend Agent | Live signal monitoring |
| 5.8 | Implement Performance page: equity curve, monthly returns, R distribution (TradingView Lightweight Charts) | Frontend Agent | Charts render correctly |
| 5.9 | Implement Settings page: API key entry (encrypted), trading parameters, symbol config | Frontend Agent | Settings save to backend |
| 5.10 | Polish: responsive layout, loading states, error handling, empty states | Frontend Agent | Dashboard works on mobile + desktop |

**Validation**: All pages load without error; WebSocket updates visible within 1s; charts render historical data.

---

### Phase 6: Integration & Hardening
**Goal**: Everything works together in production

| Step | Task | Agent | Deliverable |
|---|---|---|---|
| 6.1 | End-to-end test: trigger a signal → order placed → position opened → TP hit → P&L logged | All Agents | Full trade flow validated |
| 6.2 | Verify daily loss limit: simulate -3R → bot stops → alert sent | Backend Agent 3 | Loss limit enforced |
| 6.3 | Security audit: API keys encrypted, no secrets in logs, JWT httpOnly, SSL verified | DevOps Agent | Security checklist passed |
| 6.4 | Load test WebSocket: 5+ simultaneous dashboard connections, verify no lag | Frontend Agent | Real-time performance acceptable |
| 6.5 | Deploy to `fubot.bedikian.cc` — Nginx SSL, DNS CNAME, certbot renewal | DevOps Agent | Site live at HTTPS URL |
| 6.6 | Write `README.md` with setup instructions, environment variables, troubleshooting | Backend Agent 1 | Documentation complete |

---

## 11. Feature-by-Feature Development Breakdown

Each feature is a module that can be built independently by a single agent.

### Feature 1: Project Foundation
- **Owner**: Backend Agent 1
- **Files**: `config.py`, `database.py`, `models/`, `schemas/`, `routers/auth.py`
- **APIs**: `POST /api/auth/login`, `GET /api/auth/me`
- **Test**: Login returns valid JWT; DB tables exist

### Feature 2: Binance Service + Encryption
- **Owner**: Backend Agent 1
- **Files**: `encryption.py`, `services/binance_service.py`
- **APIs**: Internal service (no new routes)
- **Test**: API key encrypted/decrypted correctly; Binance connectivity works

### Feature 3: Market Data + Indicators
- **Owner**: Backend Agent 2
- **Files**: `indicators/*.py`, `services/signal_service.py`
- **APIs**: Internal service
- **Test**: Signals appear in DB for BTCUSDT with correct type and R:R

### Feature 4: Order Execution + Risk
- **Owner**: Backend Agent 3
- **Files**: `services/order_service.py`, `services/risk_service.py`, `services/strategy_service.py`
- **APIs**: Internal service
- **Test**: Order placed on Binance; position size = risk% of balance

### Feature 5: Position Manager + BE Trailing
- **Owner**: Backend Agent 3
- **Files**: `services/trade_service.py`, `core/position_manager.py`
- **APIs**: Internal service
- **Test**: Stop moved to BE after new BOS confirmed

### Feature 6: Bot Engine Loop
- **Owner**: Backend Agent 2
- **Files**: `core/bot_engine.py`, `core/market_scanner.py`, `core/daily_reset.py`
- **APIs**: `POST /api/bot/start`, `POST /api/bot/stop`, `GET /api/bot/status`
- **Test**: Bot processes signals and executes trades continuously

### Feature 7: WebSocket Server
- **Owner**: Backend Agent 2
- **Files**: `core/websocket_manager.py`, `routers/` (add WS route to main.py)
- **APIs**: `WS /ws`
- **Test**: Dashboard receives position_update messages within 1s of change

### Feature 8: REST API Completion
- **Owner**: Backend Agent 1
- **Files**: `routers/dashboard.py`, `routers/positions.py`, `routers/trades.py`, `routers/signals.py`, `routers/settings.py`, `routers/orders.py`, `routers/performance.py`
- **APIs**: All REST endpoints in Section 7
- **Test**: All endpoints return correct data with valid JWT

### Feature 9: React Dashboard — Core
- **Owner**: Frontend Agent
- **Files**: `frontend/src/pages/Overview.tsx`, `Login.tsx`, Sidebar, Zustand stores
- **APIs**: All REST endpoints + WS
- **Test**: Dashboard loads, auth works, overview shows live data

### Feature 10: React Dashboard — Charts & Analytics
- **Owner**: Frontend Agent
- **Files**: `frontend/src/pages/Performance.tsx`, `components/charts/EquityChart.tsx`
- **APIs**: `GET /api/performance/*`
- **Test**: Charts render 30-day equity curve; monthly returns bar chart correct

### Feature 11: React Dashboard — Scanner & Settings
- **Owner**: Frontend Agent
- **Files**: `frontend/src/pages/Scanner.tsx`, `Settings.tsx`
- **APIs**: `GET /api/signals`, `GET /api/settings`, `PUT /api/settings/trading`
- **Test**: Scanner shows live signals; settings save and persist

### Feature 12: Production Deployment
- **Owner**: DevOps Agent
- **Files**: `nginx/fubot.conf`, `fubot.service`, `scripts/setup_ssl.sh`
- **APIs**: N/A (infrastructure)
- **Test**: `https://fubot.bedikian.cc` serves dashboard with valid SSL

---

## 12. Configuration Reference

### Environment Variables (`.env`)
```bash
# FUBOT Core
FUBOT_ENV=production                    # development | production
FUBOT_SECRET_KEY=your-jwt-secret        # Min 32 chars, random
FUBOT_ENCRYPTION_KEY=your-aes-key        # 32-byte key for AES-256

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/fubot

# Binance
BINANCE_API_KEY=your-binance-api-key
BINANCE_API_SECRET=your-binance-secret
BINANCE_TESTNET=false                    # true for paper trading first

# Admin User (first run)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=set-strong-password

# VPS / Network
VPS_HOST=10.10.30.25
DOMAIN=fubot.bedikian.cc
```

### Key Trading Parameters (Default)
| Parameter | Default | Description |
|---|---|---|
| `risk_percent` | 1% | Risk per trade as % of balance |
| `max_positions` | 3 | Max simultaneous open positions |
| `daily_loss_limit_r` | -3R | Daily loss hard stop |
| `rr_min` | 3:1 | Minimum risk:reward ratio |
| `check_interval` | 60s | How often bot scans for signals |
| `be_trigger` | new BOS | Move SL to BE on new Break of Structure |
| `leverage_btc` | 10x | Default leverage for BTCUSDT |
| `leverage_alts` | 5x | Default leverage for altcoins |

---

## 13. Known Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| API key compromised | Low | Encrypted storage, no withdrawal perms, no withdrawal permission on key |
| Bot enters bad trade due to signal lag | Medium | 60s check interval + volume confirmation filter |
| Daily loss limit not enforced | Low | Hard-coded in `daily_reset.py`, enforced before every trade |
| VPS downtime | Low | systemd auto-restart, watchdog timer |
| Binance API rate limit | Medium | Async calls with rate limit awareness, retry with backoff |
| WebSocket disconnect from dashboard | Medium | Auto-reconnect in React with exponential backoff |
| Database corruption | Low | PostgreSQL with daily backups, WAL archiving |
| Slippage on market orders | Medium | Always use **limit orders**; market orders only for liquidation exits |

---

## 14. Out of Scope (v1)

- [ ] TradingView chart integration (TradingView indicators run on TradingView only)
- [ ] Multiple bot instances on same account
- [ ] Telegram bot commands
- [ ] Backtesting module (future)
- [ ] Copy trading / signal provider mode
- [ ] Mobile native app

---

## 15. Glossary

| Term | Definition |
|---|---|
| **FVG** | Fair Value Gap — three-candle imbalance zone where price gapped |
| **3-Candle** | Liquidity sweep pattern — C1 range, C2 sweeps it, C3 confirms reversal |
| **CHoCH** | Change of Character — price breaks last higher low (bullish) or lower high (bearish) |
| **BOS** | Break of Structure — price breaks significant high/low confirming trend |
| **BE** | Break Even — stop loss moved to entry price to lock in profit |
| **R** | Unit of risk — 1R = the dollar amount you risk per trade |
| **R:R** | Risk:Reward ratio — TP distance ÷ SL distance |
| **ATR** | Average True Range — volatility measure for stop/target sizing |
| **S/R** | Support / Resistance zones |
| **LW** | Long Wick candle — dominant wick indicating rejection |
| **USDS-M** | USD-Margined Futures — settled in USDT on Binance |
| **Paper trading** | Simulated trading with fake balance |

---

*This document is the single source of truth for the FUBOT project. All agents should read this in full before beginning development. Update this document first if requirements change.*
