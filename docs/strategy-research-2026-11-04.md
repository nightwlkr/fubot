# Strategy Research Plan - 2026-11-04

## Current Strategy Performance

| Strategy | Net R/Trade | Trades | Status |
|----------|-------------|--------|--------|
| FVG | -0.397R | 84 trades | DISABLED in production |
| 3-CANDLE | +0.315R | 84 trades | ACTIVE (with live trend gate) |
| CHoCH | 0 trades | 0 trades | DISABLED |
| OBFVG | +0.315R | 84 trades | ACTIVE (with live trend gate) |

**Key Observations**:
- FVG is negative in live trading despite backtest promise
- 3-CANDLE and OBFVG are positive but with thin evidence (3.5 days, 84 trades)
- CHoCH is too selective (0 trades in 24h)
- All strategies use structural stops with ATR floors
- R:R ratio validation (default 3.0-4.0)
- Trend filters are critical (trend bias, ADX, volume)

## Available Indicators

### Structural
- **FVG** - Fair Value Gap (3-candle imbalance)
- **CHoCH** - Change of Character (trend shift)
- **BOS** - Break of Structure (trend confirmation)
- **Order Block** - Supply/demand zones
- **Pivots** - Highs/lows detection
- **Structural Analysis** - Combined trend + CHoCH + BOS

### Trend
- **Trend Bias** - UPTREND/DOWNTREND/RANGING
- **ADX** - Trend strength (14-period)
- **SMA** - Moving averages (50, 100, 200)
- **Regime Analysis** - Market state classification

### Momentum
- **RSI** - Relative Strength Index (14-period)
- **RSI Zones** - OVERBOUGHT (>70), OVERSOLD (<30), NEUTRAL

### Volatility
- **ATR** - Average True Range (14-period)
- **ATR Floors** - Minimum stop distance (1.5× ATR)

### Volume
- **Volume Profile** - Volume concentration
- **Volume Confirmation** - Filter based on volume spikes

### Anchors
- **VWAP** - Volume Weighted Average Price
- **Fibonacci** - 50%, 61.8% retracements

## New Strategy Opportunities

### 1. BOS-Based Entries
**Rationale**: You have BOS detection but no dedicated strategy using it. BOS is a reliable trend confirmation signal.

**Entry Logic**:
- Bullish BOS: Price breaks above previous pivot high
- Bearish BOS: Price breaks below previous pivot low
- Entry at BOS confirmation price
- Structural stop at broken level
- TP at 3× R:R from risk distance

**Expected Performance**: Moderate positive expectancy (historically 0.5-1.5R/trade)

**Timeframe**: 5m or 15m (trend confirmation on shorter timeframe)

---

### 2. RSI Mean Reversion
**Rationale**: Complement structural trend-following with momentum mean reversion. RSI extremes often precede reversals.

**Entry Logic**:
- Bullish: RSI(14) < 30 (oversold) + price at lower BB or VWAP
- Bearish: RSI(14) > 70 (overbought) + price at upper BB or VWAP
- Structural stop at 1.5× ATR from entry
- TP at 2× R:R (less aggressive than trend-following)

**Expected Performance**: Lower risk, more trades, smaller R per trade

**Timeframe**: 1h (RSI is smoother, less noise)

---

### 3. Volume-Confirmation Breakout
**Rationale**: Volume confirms the strength of breakouts. Pure price breakouts without volume are often false signals.

**Entry Logic**:
- Price breaks above/below significant level (SMA 50, BOS, pivot high/low)
- Volume > 1.5× 20-period average volume
- Structural stop at 1.5× ATR from entry
- TP at 3× R:R

**Expected Performance**: Higher quality entries, fewer false breakouts

**Timeframe**: 5m or 15m

---

### 4. VWAP Bounce (Trend Following)
**Rationale**: VWAP is institutional anchor. Price deviations from VWAP often reverse.

**Entry Logic**:
- Bullish: Price bounces OFF lower VWAP (distance < 0.5%)
- Bearish: Price rejects upper VWAP (distance > 0.5%)
- Structural stop at 1.5× ATR from entry
- TP at 2× R:R

**Expected Performance**: Consistent mean-reversion within trends

**Timeframe**: 15m or 1h

---

### 5. Multi-Timeframe Trend Filter
**Rationale**: Use higher timeframe to confirm trend before taking trades on lower timeframe.

**Entry Logic**:
- **Higher TF (1h)**: Only trade in direction of trend (UPTREND/BOS)
- **Lower TF (5m)**: Take entries on confirmed trend continuation
- Structural stop at 1.5× ATR from entry
- TP at 3× R:R

**Expected Performance**: Better filter on ranging markets, fewer counter-trend trades

**Timeframe**: 5m entries, 1h trend filter

---

### 6. Volatility-Targeted Entries
**Rationale**: Scale stop size with volatility. Tighter stops in calm markets, wider in volatile periods.

**Entry Logic**:
- ATR(14) < 0.5% of price → Tight stops (1× ATR)
- ATR(14) between 0.5-1.5% → Normal stops (1.5× ATR)
- ATR(14) > 1.5% → Wide stops (2× ATR)
- TP at 3× R:R (adjusted for volatility)

**Expected Performance**: Better risk-adjusted returns, fewer whipsaws

**Timeframe**: 5m or 15m

---

### 7. Composite Trend-Following
**Rationale**: Combine multiple trend signals for higher confidence.

**Entry Logic**:
- Requires 2+ of: BOS, CHoCH, ADX > 25, Trend = UPTREND/DOWNTREND
- Entry at signal price
- Structural stop at 1.5× ATR from entry
- TP at 3× R:R

**Expected Performance**: Fewer but higher-quality entries

**Timeframe**: 5m or 15m

---

### 8. OB + RSI Confluence
**Rationale**: Order Block (supply/demand) + RSI extreme = high-probability reversal.

**Entry Logic**:
- Bullish: RSI < 30 AND price in bullish OB zone
- Bearish: RSI > 70 AND price in bearish OB zone
- Entry at OB midpoint
- Structural stop at 1.5× ATR from entry
- TP at 2× R:R

**Expected Performance**: High-quality mean-reversion entries

**Timeframe**: 5m or 15m

---

## Research Priorities

### High Priority (Test First)
1. **BOS-Based Entries** - Simple, reliable signal, likely positive
2. **RSI Mean Reversion** - Classic strategy, easy to implement
3. **Volume-Confirmation Breakout** - Volume filters are powerful

### Medium Priority
4. **VWAP Bounce** - Institutional anchor, consistent behavior
5. **Multi-Timeframe Trend Filter** - Improves existing strategies

### Low Priority (Longer Testing)
6. **Volatility-Targeted Entries** - Complex, needs careful tuning
7. **Composite Trend-Following** - Requires calibration of signal weights
8. **OB + RSI Confluence** - Similar to OBFVG, incremental improvement

## Testing Plan

1. **Backtest each strategy** on 60-day historical data
2. **Compare** against current active strategies (3-CANDLE, OBFVG)
3. **Metrics**: Net R/trade, win rate, max drawdown, Sharpe ratio
4. **Statistical significance**: Ensure >50 trades for meaningful results
5. **Deploy to paper mode** for live validation (72h minimum)

## Cost Considerations

Using `build-trading-strategies` skill:
- Each strategy creation: $1-4.50 (most expensive tool)
- Each backtest: $0.001
- Total estimated: $10-20 for 5-10 strategies

**Optimization**: Start with 3 high-priority strategies, validate, then iterate.
