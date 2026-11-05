# Production Trade Analysis - 2026-11-05

**Analysis Date**: 2026-11-05
**Data Source**: Production database (contabo01)
**Analysis Period**: 2026-10-20 to 2026-11-04 (35 live trades)
**Purpose**: Identify root causes of persistent trading losses

> **CORRECTION NOTICE (2026-11-05, later same day).** The fee-drag and gross-PnL figures in this document are **superseded** by `analysis/session-symbol-research-2026-11-05.md` §0 and must not be quoted as-is:
>
> - **Fee drag is 15.8862R (0.4539R/trade), not 26.61R (0.758R/trade).** This document computed it as `entry × 0.0011 / abs(entry - positions.stop_loss)`. `stop_loss` is **mutable** — BE-trailing moves it to and past entry — so for the 12 trailed winners it measured a *profit* distance, not the risk. The authoritative 1R is the frozen `positions.initial_risk_distance` (`backend/core/risk_units.py:113-121`, `backend/core/cost_model.py:82-93`). Trade 2 was reported as a "0.01% stop with 7.53R fee drag"; its real basis is 0.606% and its real fee drag 0.182R. Only trade 8 (BNBUSDT LONG, 0.0128% basis) is genuinely degenerate.
> - **Gross PnL is -1.8921R (-0.054R/trade), not +11.62R.** The gross figure here was derived circularly as `net + fee`, so it could not confirm its own thesis. The exchange-anchored gross (stored `pnl_usdt` + modelled taker fee) is ≈ 0 — **the strategy had no measurable gross edge in this sample**; fees are the difference between flat and -14.91R.
> - **"49% of trades had stops tighter than 0.5%" is 31.4% (11/35)** on the frozen risk basis.
> - Two appendix rows do not match the database: **trade 2 is +2.3441R** (not -0.04R) and **trade 20 is -1.0380R / EXTERNAL_CLOSE** (not +0.18R / SL_HIT).
> - Unchanged and still correct: 35 trades, 12W/23L, -14.91R net, -0.426R/trade, 34 SL_HIT / 1 EXTERNAL_CLOSE / 0 TP_HIT, the per-symbol table, the per-day table and the per-week table.

---

## Executive Summary

### Overall Performance
- **Total trades**: 35 (12 wins, 23 losses)
- **Win rate**: 34.3%
- **Net PnL**: **-14.91R (-$11.97)**
- **Average per trade**: **-0.426R**
- **Strategy**: 100% 3CANDLE (FVG and OBFVG disabled)
- **Exit distribution**: 34/35 SL_HIT, 1 EXTERNAL_CLOSE, 0 TP_HIT

### Key Finding
**The strategy is GROSS profitable (+11.62R) but execution costs turn it into a net loss (-14.91R).**

| Metric | Value |
|--------|-------|
| Total Net R | -14.91R |
| Total Gross R (before fees) | +11.62R |
| **Total Fee Drag** | **26.61R** |
| Fee per trade | 0.76R |

### Current Status
- **Scanner**: STOPPED (weekly drawdown guard halted at -5.66R weekly loss on Nov 4)
- **Positions**: Flat (no open positions)
- **Service**: Active, NRestarts=0

---

## Detailed Findings

### 1. Fee Drag Analysis

#### Fee Model
- Round-trip cost: 0.11% (0.045% taker × 2 + 0.01% slippage × 2)
- Reference: `backend/core/cost_model.py`

#### Fee Drag by Stop Distance

| Stop Category | Trades | Avg Fee Drag (R) | Avg Net PnL (R) | Avg Gross PnL (R) |
|--------------|--------|------------------|------------------|-------------------|
| Tight (<0.5%) | 17 | **1.44R** | -0.49R | +0.95R |
| Medium (0.5-1.0%) | 13 | 0.14R | -0.37R | -0.23R |
| Wide (≥1.0%) | 5 | 0.09R | -0.39R | -0.30R |

**Critical Issue**: 49% of trades (17/35) had stops tighter than 0.5%, incurring **1.44R average fee drag** per trade. These tight stops negate any edge the strategy might have.

#### Fee Drag Per Trade (Sample)

| Stop % | Fee Drag (R) | Net PnL (R) | Gross PnL (R) |
|--------|--------------|-------------|---------------|
| 0.01% | 7.53R | -0.04R | +7.49R |
| 0.02% | 6.29R | -0.19R | +6.10R |
| 0.04% | 2.63R | -0.09R | +2.54R |
| 0.06% | 1.74R | -1.65R | +0.09R |
| 0.14% | 0.79R | -1.39R | -0.59R |
| 0.20% | 0.55R | +0.86R | +1.41R |
| 0.69% | 0.16R | -1.10R | -0.95R |
| 0.90% | 0.12R | +1.09R | +1.21R |
| 1.49% | 0.07R | +2.34R | +2.42R |

**Pattern**: Trades with tighter stops have dramatically higher fee drag. Even when the gross edge is positive, fees consume it entirely.

---

### 2. Stop Distance Analysis

#### Stop Distance Statistics
- **Average stop distance**: 0.64% of entry price
- **Range**: 0.01% - 1.60%
- **Median**: ~0.36%

#### Problem: 3Candle Stop Sizing

Current implementation in `backend/indicators/three_candle.py`:
```python
atr_stop_mult = 1.5  # Multiplier for minimum stop distance
```

**Issue**: On 5m candles, adjacent candles can have tiny ranges. The structural stop (sweep wick) is often only 0.01-0.02% from entry. Even with the 1.5× ATR floor, many stops remain dangerously tight.

**Examples from production**:
- Trade 2: 0.01% stop (7.53R fee drag)
- Trade 29: 0.02% stop (6.29R fee drag)
- Trade 32: 0.04% stop (2.63R fee drag)

These stops are so tight that normal market noise triggers them immediately, guaranteeing a loss plus the full fee drag.

---

### 3. Exit Performance

#### Exit Distribution
- **SL_HIT**: 34/35 (97.1%)
- **EXTERNAL_CLOSE**: 1/35 (2.9%)
- **TP_HIT**: 0/35 (0%)

**Critical Issue**: With 3R minimum R:R and planned R:R ratios of 4-12, the fact that NO trade hit take profit is alarming.

#### Planned vs Actual R:R

| Trade | Planned R:R | Stop % | Actual R:R | Result |
|-------|-------------|--------|------------|--------|
| 2 | 454.6:1 | 0.01% | -0.04R | SL_HIT |
| 29 | 355.1:1 | 0.02% | -0.19R | SL_HIT |
| 32 | 78.8:1 | 0.04% | -0.09R | SL_HIT |
| 30 | 24.0:1 | 0.14% | -1.39R | SL_HIT |
| 25 | 10.2:1 | 0.22% | +0.58R | SL_HIT |
| 34 | 3.4:1 | 0.91% | -1.03R | SL_HIT |

**Observation**: The planned R:R ratios are often wildly unrealistic (454:1, 355:1) due to razor-thin stops. This is a calculation artifact - when stop distance approaches zero, R:R goes to infinity.

---

### 4. Performance by Symbol

#### Symbol Performance Table

| Symbol | Side | Trades | Wins | Losses | Net R | Avg R |
|--------|------|--------|------|--------|-------|-------|
| LINKUSDT | SHORT | 1 | 1 | 0 | +1.09R | +1.09R |
| **BTCUSDT** | LONG | 4 | 2 | 2 | +0.42R | +0.10R |
| SOLUSDT | LONG | 3 | 1 | 2 | -1.17R | -0.39R |
| APEUSDT | LONG | 1 | 0 | 1 | -1.22R | -1.22R |
| APEUSDT | SHORT | 4 | 2 | 2 | -1.98R | -0.49R |
| SOLUSDT | SHORT | 3 | 0 | 3 | -2.21R | -0.74R |
| **BNBUSDT** | SHORT | 2 | 0 | 2 | -2.39R | -1.20R |
| **BNBUSDT** | LONG | 3 | 1 | 2 | -2.49R | -0.83R |
| **BTCUSDT** | SHORT | 4 | 1 | 3 | -2.73R | -0.68R |
| UNIUSDT | LONG | 1 | 0 | 1 | -0.04R | -0.04R |
| UNIUSDT | SHORT | 2 | 1 | 1 | -0.16R | -0.08R |
| LINKUSDT | LONG | 4 | 2 | 2 | -0.42R | -0.11R |
| OPUSDT | SHORT | 2 | 1 | 1 | -0.56R | -0.28R |
| OPUSDT | LONG | 1 | 0 | 1 | -1.05R | -1.05R |

**Key Insights**:
- **BTC LONG is the only consistently profitable side** (+0.10R/trade)
- **BNB is losing on both sides** (-1.20R SHORT, -0.83R LONG)
- **BTC SHORT is significantly underperforming** (-0.68R/trade)
- **No symbol shows a clear edge** - best is BTC LONG at marginal profitability

---

### 5. Daily Performance

#### Daily PnL Table

| Date | Trades | Wins | Losses | Net R | Net USDT | Notes |
|------|--------|------|--------|-------|----------|-------|
| Nov 4 | 3 | 0 | 3 | -3.40R | -$3.71 | Daily loss limit hit |
| Nov 3 | 2 | 0 | 2 | -2.18R | -$2.50 | |
| Nov 2 | 1 | 0 | 1 | -0.09R | -$0.16 | |
| Nov 1 | 1 | 0 | 1 | -1.13R | -$1.16 | |
| Oct 29 | 3 | 1 | 2 | -0.66R | +$0.43 | |
| Oct 28 | 7 | 5 | 2 | +0.24R | +$0.84 | **Best day** |
| Oct 27 | 4 | 3 | 1 | +0.82R | +$1.27 | |
| Oct 26 | 4 | 1 | 3 | -2.78R | -$3.48 | |
| Oct 25 | 2 | 1 | 1 | -0.28R | -$0.55 | |
| Oct 24 | 2 | 0 | 2 | -3.14R | -$0.63 | |
| Oct 23 | 1 | 0 | 1 | -1.25R | -$0.93 | |
| Oct 22 | 3 | 0 | 3 | -3.36R | -$15.32 | **Worst day** |
| Oct 20 | 2 | 1 | 1 | +2.30R | +$13.93 | First profitable day |

**Recent Losing Streak**: Last 4 days (Nov 1-4) = -6.80R (7 trades, 0 wins)

**Pattern**: Performance is highly inconsistent. The best day (Oct 28) had 5 wins, but the worst day (Oct 22) had 0 wins despite 3 trades.

---

### 6. Weekly Drawdown Analysis

#### Weekly Performance

| Week Starting | Trades | Net R | Weekly Limit | Status |
|---------------|--------|-------|--------------|--------|
| Nov 2, 2026 | 6 | -5.66R | -5R | **BREACHED** - scanner halted |
| Oct 26, 2026 | 19 | -3.52R | -5R | Within limit |
| Oct 19, 2026 | 10 | -5.73R | -5R | **BREACHED** |

**Observation**: The weekly guard is functioning correctly, halting trading when the -5R limit is breached. However, this means the bot is only actively trading ~1 week per month before being stopped.

---

### 7. Signal Pipeline Health

#### Signal Statistics (Last 7 Days)

| Status | Count | Percentage |
|--------|-------|------------|
| EXPIRED | 17,155 | 99.4% |
| SKIPPED | 107 | 0.6% |
| TRIGGERED | 10 | 0.06% |
| PENDING | 2 | 0.01% |

**Interpretation**: The scanner is generating many signals, but 99.4% expire before being executed. Only 10 signals in 7 days resulted in trades.

#### Skip Reasons (Top 10)

| Skip Reason | Count |
|-------------|-------|
| Trend filter: MODERATE trend below STRONG | 27 |
| Trend filter: BEARISH | 23 |
| Regime: ranging (ADX 19.0) | 18 |
| Regime: ranging (ADX 19.8) | 8 |
| Regime filter: Counter-trend long in strong bear regime | 7 |
| Regime: ranging (ADX 16.2) | 4 |
| Regime: ranging (ADX 15.7) | 3 |
| Trend filter: BULLISH | 3 |
| Regime: ranging (ADX 17.9) | 3 |
| Regime: ranging (ADX 18.9) | 2 |

**Key Insights**:
- **STRONG trend requirement** blocks 27 MODERATE signals (25% of skips)
- **Ranging filter** blocks 31 signals (29% of skips)
- **Counter-trend rejection** blocks 7 signals (6% of skips)

These filters are aggressive but not necessarily improving quality - the 10 trades that did execute still lost at -0.426R/trade.

---

### 8. Current Settings

#### Production Settings (from database)

| Setting | Value | Note |
|---------|-------|------|
| risk_percent | 1.0% | Per-trade risk |
| max_positions | 3 | Max concurrent positions |
| daily_loss_limit_r | -3.0 | Daily drawdown limit |
| weekly_drawdown_limit_r | -5.0 | Weekly drawdown limit |
| rr_min | 4.0 | **Minimum R:R** (should be 3.0 per AGENTS.md) |
| trade_min_trend_strength | STRONG | Aggressive filter |
| regime_filter_enabled | true | ADX-based regime filter |
| strategy_3candle_enabled | true | Active strategy |
| strategy_fvg_enabled | false | Disabled (correctly) |
| strategy_obfvg_enabled | false | Disabled |

**Issue**: `rr_min` is set to 4.0 in production but AGENTS.md specifies it should be 3.0. This higher R:R requirement makes it harder for setups to qualify and pushes targets further away, reducing TP hit probability.

---

## Root Causes

### Primary Cause: Fee Drag

**Evidence**:
- Gross PnL: +11.62R
- Net PnL: -14.91R
- Fee Drag: 26.61R
- Fee per trade: 0.76R

**Mechanism**:
- Fee drag is inversely proportional to stop distance
- Tight stops (<0.5%) cause 4-5x higher fee drag
- 49% of trades had dangerously tight stops
- Fees consume the entire gross edge plus additional capital

**Root**: Taker entry (0.045%) + taker exit (0.045%) + slippage (0.01% × 2) = 0.11% round trip. This is too expensive for the tight stop distances the strategy generates.

---

### Secondary Cause: Inadequate Stop Sizing

**Evidence**:
- Average stop: 0.64% (range 0.01-1.60%)
- 17/35 trades (49%) had stops <0.5%
- Some stops were 0.01-0.02% (effectively zero)
- These trades had 6-7R fee drag

**Mechanism**:
- 3Candle uses 1.5× ATR multiplier on 5m candles
- 5m candles often have tiny ranges (0.05-0.1%)
- Structural stop (sweep wick) is even tighter
- ATR floor doesn't provide adequate buffer

**Root**: The 1.5× multiplier is insufficient for 5m timeframe volatility.

---

### Tertiary Cause: Zero Take Profit Hits

**Evidence**:
- 0/35 trades hit TP
- 34/35 trades hit SL
- All trades had planned R:R of 3-12
- No breakeven/trailing protection noted

**Mechanism**:
- Liquidity sweeps are often exhaustion moves
- Price reverses immediately after sweep
- No profit-lock mechanism to protect gains
- Targets are too far relative to reversal probability

**Root**: The strategy assumes sweeps lead to continuation, but in practice they're often reversal points without protection.

---

### Contributing Cause: Aggressive Filters

**Evidence**:
- STRONG trend requirement blocks 27 MODERATE signals
- Ranging filter blocks 31 signals
- Counter-trend rejection blocks 7 signals
- Despite filters, executed trades still lose

**Mechanism**:
- Filters reduce trade frequency but don't improve quality
- MODERATE trends may be valid for 3Candle patterns
- Ranging filter may be too strict (ADX < 20)

**Root**: Filter settings are tuned to theory rather than empirical performance.

---

### Contributing Cause: Symbol Bias

**Evidence**:
- BTC LONG: +0.10R/trade (marginal profit)
- BTC SHORT: -0.68R/trade (significant loss)
- BNB LONG: -0.83R/trade
- BNB SHORT: -1.20R/trade

**Mechanism**:
- Some symbols may not suit 3Candle pattern
- BTC appears to have an upward bias
- BNB appears to have unfavorable volatility
- No symbol-specific parameter adjustment

**Root**: One-size-fits-all parameters don't account for symbol-specific behavior.

---

## Recommendations

### Immediate Actions (Stop Bleeding)

#### 1. Increase ATR Multiplier
**File**: `backend/indicators/three_candle.py`
**Change**: `atr_stop_mult = 1.5` → `atr_stop_mult = 2.5` or `3.0`
**Rationale**: Ensure minimum stop distance of 0.5-0.75% on 5m timeframe
**Expected Impact**: Reduce fee drag from 1.44R to ~0.4R on tight-stop trades

#### 2. Add Minimum Stop Floor
**File**: `backend/indicators/three_candle.py` or `backend/services/strategy_service.py`
**Change**: Add check: `if risk_distance < entry_price * 0.005: skip trade`
**Rationale**: Prevent stops tighter than 0.5% of entry price
**Expected Impact**: Eliminate extreme fee drag (6-7R) trades

#### 3. Fix rr_min Setting
**File**: Database settings table
**Change**: `rr_min = 4.0` → `rr_min = 3.0`
**Rationale**: Align with AGENTS.md specification, improve TP hit probability
**Expected Impact**: More setups qualify, targets are closer, higher TP hit rate

#### 4. Reduce Trend Strength Requirement
**File**: Database settings table
**Change**: `trade_min_trend_strength = STRONG` → `trade_min_trend_strength = MODERATE`
**Rationale**: Current setting blocks 27 valid signals without improving win rate
**Expected Impact**: Increase trade frequency without sacrificing quality

---

### Medium Term (Fix Economics)

#### 5. Implement Maker Entries
**File**: `backend/services/strategy_service.py` entry logic
**Change**: Use GTX (post-only) limit orders instead of marketable limits
**Rationale**: Reduce fees from 0.045% to 0.018% per side
**Expected Impact**: Round-trip cost: 0.11% → 0.074% (33% reduction)
**Risk**: Lower fill rate, requires order lifecycle redesign

#### 6. Add Breakeven Logic
**File**: `backend/core/position_manager.py`
**Change**: Implement breakeven stop at +0.5R profit
**Rationale**: Protect gains from reversals
**Expected Impact**: Convert -1R losses to 0R on partial moves
**Implementation**: Already has BE at 0.5R, verify it's working

#### 7. Add Partial Scale-Out
**File**: `backend/core/position_manager.py`
**Change**: Close 50% at +1R, move SL to breakeven
**Rationale**: Lock in partial profit, reduce risk
**Expected Impact**: Improve risk-adjusted returns
**Status**: Not currently implemented

#### 8. Reduce Trade Frequency
**File**: Database settings table
**Change**: Add session filters (avoid Asian session for USD pairs)
**Rationale**: Only trade during high-liquidity hours
**Expected Impact**: Higher quality setups, fewer chop trades
**Status**: Session filters not currently implemented

---

### Long Term (Prove Edge)

#### 9. Run Proper Backtest
**File**: `backend/backtest.py`
**Change**: Implement paginated kline fetching for 45-day window
**Rationale**: Current 3.5-day sample is insufficient for statistical significance
**Expected Impact**: Determine if strategy has genuine edge with corrected parameters
**Status**: Backtest infrastructure exists but pagination needs implementation

#### 10. Test Symbol-Specific Settings
**File**: Database settings table or config
**Change**: Add per-symbol ATR multipliers, R:R requirements
**Rationale**: BTC LONG shows edge, BNB shows loss, symbol-specific tuning needed
**Expected Impact**: Disable losing symbols, optimize winning symbols
**Status**: No per-symbol configuration exists

#### 11. Evaluate OBFVG Strategy
**File**: Database settings table
**Change**: Enable `strategy_obfvg_enabled = true` for paper testing
**Rationale**: Only strategy with positive backtest (+0.315R/trade)
**Expected Impact**: Better edge if validation window is extended
**Caveat**: Sample size was 84 trades over 3.5 days (inconclusive)

#### 12. Re-evaluate FVG with OBFVG Filter
**File**: `backend/services/signal_service.py`
**Change**: Never re-enable plain FVG (was -0.397R/trade)
**Rationale**: OBFVG (OB-confluent FVG) is different population
**Expected Impact**: N/A (keeping plain FVG disabled)
**Status**: Correctly disabled in production

---

## Next Steps for Agents

### For Strategy Agent
1. Review `backend/indicators/three_candle.py` - evaluate stop sizing logic
2. Test different ATR multipliers (2.0, 2.5, 3.0) in backtest
3. Implement minimum stop floor (0.5% of entry price)
4. Add per-symbol parameter support

### For Backtest Agent
1. Implement paginated kline fetching in `backend/backtest_data.py`
2. Run 45-day backtest with current parameters (baseline)
3. Run 45-day backtest with corrected parameters (ATR 2.5×, rr_min 3.0)
4. Compare net-of-fee expectancy across configurations
5. Test OBFVG strategy with extended validation window

### For Order/Execution Agent
1. Research maker entry implementation (GTX post-only)
2. Design order lifecycle for resting limit orders
3. Implement partial scale-out logic
4. Verify breakeven stop is functioning correctly

### For Research Agent
1. Analyze symbol-specific performance (why BTC LONG wins, BNB loses)
2. Test session filters (avoid low-liquidity hours)
3. Research regime filter parameters (ADX threshold tuning)
4. Evaluate whether MODERATE trends should be allowed

### For DevOps Agent
1. Update production `rr_min` setting from 4.0 to 3.0
2. Update `trade_min_trend_strength` from STRONG to MODERATE
3. Monitor daily/weekly guard logs
4. Ensure weekly guard reset works correctly after parameter changes

---

## Data Sources

### Database Queries Used

All queries executed on production database (contabo01):

```sql
-- Total live trades
SELECT COUNT(*) FROM trades WHERE mode = 'live';

-- Trade list with details
SELECT id, symbol, strategy_type, side, entry_price, exit_price, 
       quantity, pnl_usdt, pnl_r, exit_reason, closed_at
FROM trades WHERE mode = 'live' ORDER BY closed_at DESC;

-- Performance summary
SELECT COUNT(*) as total_trades,
       COUNT(*) FILTER (WHERE pnl_r > 0) as wins,
       COUNT(*) FILTER (WHERE pnl_r < 0) as losses,
       ROUND(SUM(pnl_r), 2) as total_r,
       ROUND(SUM(pnl_usdt), 2) as total_usdt,
       ROUND(AVG(pnl_r), 4) as avg_r_per_trade
FROM trades WHERE mode = 'live';

-- By symbol/side
SELECT symbol, side, COUNT(*) as trades,
       COUNT(*) FILTER (WHERE pnl_r > 0) as wins,
       COUNT(*) FILTER (WHERE pnl_r < 0) as losses,
       ROUND(SUM(pnl_r), 2) as total_r,
       ROUND(AVG(pnl_r), 4) as avg_r
FROM trades WHERE mode = 'live'
GROUP BY symbol, side ORDER BY total_r DESC;

-- Stop distance analysis
SELECT ABS(p.entry_price - p.stop_loss) / p.entry_price * 100 as stop_pct,
       ABS(p.take_profit - p.entry_price) / p.entry_price * 100 as target_pct,
       ABS(p.take_profit - p.entry_price) / 
           NULLIF(ABS(p.entry_price - p.stop_loss), 0) as planned_rr,
       t.pnl_r
FROM positions p JOIN trades t ON p.id = t.position_id
WHERE p.mode = 'live';

-- Signal pipeline health
SELECT status, COUNT(*) as count
FROM signals WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY status;

-- Skip reasons
SELECT skip_reason, COUNT(*) as count
FROM signals WHERE status = 'SKIPPED' 
AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY skip_reason ORDER BY count DESC LIMIT 10;

-- Current settings
SELECT key, value FROM settings 
WHERE key IN ('risk_percent', 'max_positions', 'daily_loss_limit_r', 
              'weekly_drawdown_limit_r', 'rr_min', 'trade_min_trend_strength',
              'regime_filter_enabled', 'strategy_3candle_enabled',
              'strategy_fvg_enabled', 'strategy_obfvg_enabled');
```

### Fee Calculation Method

Fee drag calculated as:
```
fee_drag_r = ROUND_TRIP_FEE / stop_distance_pct
ROUND_TRIP_FEE = 0.0011 (0.11%)
stop_distance_pct = ABS(entry_price - stop_loss) / entry_price
```

This matches the formula in `backend/core/cost_model.py`:
```python
def round_trip_in_r(self, entry: float, risk_dist: float) -> float:
    if risk_dist <= 0:
        return float("inf")
    return (entry * self.round_trip) / risk_dist
```

---

## Appendix: Trade-by-Trade Details

### Full Trade List (Production)

| ID | Symbol | Side | Entry | Exit | Qty | PnL (R) | PnL ($) | Exit Reason | Stop % | Planned R:R |
|----|--------|------|-------|------|-----|---------|---------|-------------|--------|-------------|
| 37 | SOLUSDT | SHORT | 92.87 | 94.13 | 1.11 | -1.08 | -$1.51 | SL_HIT | 1.36% | 3.40:1 |
| 36 | BTCUSDT | SHORT | 77352 | 77888 | 0.002 | -1.10 | -$1.18 | SL_HIT | 0.69% | 4.74:1 |
| 35 | APEUSDT | LONG | 0.1097 | 0.10875 | 930 | -1.22 | -$1.02 | SL_HIT | 0.91% | 7.10:1 |
| 34 | OPUSDT | SHORT | 0.11179 | 0.112785 | 976.1 | -1.03 | -$1.00 | SL_HIT | 0.89% | 6.86:1 |
| 33 | APEUSDT | SHORT | 0.1102 | 0.1112 | 1247 | -1.15 | -$1.50 | SL_HIT | 0.91% | 5.13:1 |
| 32 | SOLUSDT | SHORT | 95.36 | 95.325 | 1.81 | -0.09 | -$0.16 | SL_HIT | 0.04% | 78.81:1 |
| 31 | LINKUSDT | LONG | 11.552 | 11.467 | 12.05 | -1.13 | -$1.16 | SL_HIT | 0.74% | 6.57:1 |
| 30 | BTCUSDT | SHORT | 80792 | 80904 | 0.002 | -1.39 | -$0.31 | SL_HIT | 0.14% | 23.98:1 |
| 29 | LINKUSDT | LONG | 11.573 | 11.575 | 11.62 | -0.19 | -$0.12 | SL_HIT | 0.02% | 355.09:1 |
| 28 | UNIUSDT | SHORT | 7.114 | 7.045 | 13.0 | +0.91 | +$0.86 | SL_HIT | 0.98% | 7.66:1 |
| 27 | LINKUSDT | LONG | 11.512 | 11.554 | 9.51 | +0.23 | +$0.33 | SL_HIT | 0.36% | 11.69:1 |
| 26 | SOLUSDT | LONG | 100.98 | 101.965 | 1.84 | +1.01 | +$1.70 | SL_HIT | 0.98% | 3.11:1 |
| 25 | BTCUSDT | LONG | 80472 | 80652 | 0.001 | +0.58 | +$0.14 | SL_HIT | 0.22% | 10.15:1 |
| 23 | OPUSDT | SHORT | 0.11126 | 0.11069 | 1235.6 | +0.47 | +$0.78 | SL_HIT | 0.51% | 8.87:1 |
| 22 | APEUSDT | SHORT | 0.122 | 0.1219 | 1130 | +0.03 | +$0.05 | SL_HIT | 0.08% | 60.57:1 |
| 21 | SOLUSDT | SHORT | 100.87 | 101.985 | 1.54 | -1.05 | -$1.80 | SL_HIT | 1.10% | 3.19:1 |
| 20 | APEUSDT | SHORT | 0.1225 | 0.12235 | 727 | +0.18 | +$0.25 | SL_HIT | 0.33% | 17.63:1 |
| 19 | APEUSDT | SHORT | 0.1225 | 0.12205 | 727 | +0.18 | +$0.25 | SL_HIT | 0.33% | 17.63:1 |
| 18 | BTCUSDT | SHORT | 80064 | 80518 | 0.001 | -1.10 | -$0.50 | SL_HIT | 0.56% | 3.93:1 |
| 17 | LINKUSDT | SHORT | 11.398 | 11.296 | 9.25 | +1.09 | +$0.90 | SL_HIT | 0.90% | 5.18:1 |
| 16 | BNBUSDT | LONG | 740.41 | 743.26 | 0.25 | +0.65 | +$0.62 | SL_HIT | 0.38% | 6.43:1 |
| 15 | LINKUSDT | LONG | 11.47 | 11.582 | 9.87 | +0.67 | +$0.96 | SL_HIT | 0.98% | 4.60:1 |
| 14 | BTCUSDT | LONG | 80463 | 80169 | 0.002 | -1.27 | -$0.74 | SL_HIT | 0.36% | 7.18:1 |
| 13 | SOLUSDT | LONG | 101.37 | 100.265 | 1.71 | -1.10 | -$2.06 | SL_HIT | 1.09% | 3.28:1 |
| 12 | UNIUSDT | SHORT | 6.831 | 6.9405 | 14.0 | -1.08 | -$1.64 | SL_HIT | 1.60% | 3.99:1 |
| 11 | BTCUSDT | SHORT | 80974 | 80814 | 0.002 | +0.86 | +$0.17 | SL_HIT | 0.20% | 10.57:1 |
| 10 | BNBUSDT | SHORT | 737.65 | 740.18 | 0.25 | -1.14 | -$0.72 | SL_HIT | 0.34% | 7.38:1 |
| 9 | BNBUSDT | LONG | 739.07 | 737.49 | 0.25 | -1.49 | -$0.59 | SL_HIT | 0.21% | 12.10:1 |
| 8 | BNBUSDT | LONG | 739.78 | 740.245 | 0.25 | -1.65 | -$0.04 | SL_HIT | 0.06% | 53.14:1 |
| 7 | BNBUSDT | SHORT | 741.05 | 743.945 | 0.26 | -1.25 | -$0.93 | SL_HIT | 0.38% | 6.39:1 |
| 5 | BTCUSDT | LONG | 83042 | 82781 | 0.006 | -1.23 | -$1.92 | SL_HIT | 0.31% | 9.34:1 |
| 4 | SOLUSDT | LONG | 106.30 | 105.34 | 4.41 | -1.08 | -$4.57 | SL_HIT | 0.90% | 4.55:1 |
| 3 | OPUSDT | LONG | 0.12899 | 0.126915 | 4073.1 | -1.05 | -$8.83 | SL_HIT | 1.60% | 4.53:1 |
| 2 | BTCUSDT | LONG | 81774 | 82994 | 0.012 | +2.34 | +$13.93 | SL_HIT | 1.49% | 9.34:1 |
| 1 | UNIUSDT | LONG | 7.204 | 7.2045 | 1.0 | -0.04 | $0.00 | SL_HIT | 0.01% | 454.56:1 |

---

## References

### Related Files
- `backend/indicators/three_candle.py` - 3Candle pattern detection and stop sizing
- `backend/indicators/fvg.py` - FVG pattern detection (disabled)
- `backend/core/cost_model.py` - Fee model and cost calculations
- `backend/services/signal_service.py` - Signal generation and filtering
- `backend/services/strategy_service.py` - Signal evaluation and execution
- `backend/core/bot_engine.py` - Main trading loop
- `analysis/panel/DECISION.md` - Previous strategy review decisions

### Related Documentation
- `AGENTS.md` - Project overview and current status
- `backend/AGENTS.md` - Backend-specific contracts and guidance
- `analysis/AGENTS.md` - Analysis artifacts ownership

### Previous Analysis
- `analysis/panel/DECISION.md` - Cycle 1 & 2 strategy reviews (2026-10-16, 2026-10-17)
- Panel conclusion: "Fee drag, not exit geometry, is the binding constraint"
- Backtest showed all configurations net-negative after fees

---

**Document Status**: Complete
**Next Review**: After implementing immediate fixes and running corrected backtest
**Owner**: FUBOT project team
