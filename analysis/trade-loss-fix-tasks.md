# Trade Loss Fix Tasks - Agent Assignments

**Created**: 2026-11-05
**Reference**: `analysis/production-trade-analysis-2026-11-05.md`
**Priority**: Stop immediate bleeding → Fix economics → Prove edge

---

## Task Overview

Based on production trade analysis (35 trades, -14.91R net loss), the following tasks are assigned to agents to address fee drag, inadequate stop sizing, and zero TP hits.

---

## Immediate Actions (Priority: HIGH)

### Task 1: Increase ATR Multiplier in 3Candle
**Assigned to**: Strategy Agent
**File**: `backend/indicators/three_candle.py`
**Current**: `atr_stop_mult = 1.5`
**Target**: `atr_stop_mult = 2.5` or `3.0`
**Rationale**: Ensure minimum stop distance of 0.5-0.75% on 5m timeframe
**Expected Impact**: Reduce fee drag from 1.44R to ~0.4R on tight-stop trades
**Verification**: Run backtest comparing stop distance distribution before/after
**Dependencies**: None
**Estimated effort**: 1 hour

### Task 2: Add Minimum Stop Floor
**Assigned to**: Strategy Agent
**File**: `backend/indicators/three_candle.py` or `backend/services/strategy_service.py`
**Implementation**: Add check in signal evaluation:
```python
min_risk_pct = float(self.settings.get("min_risk_pct", 0.005))  # 0.5%
risk_distance = abs(float(entry) - float(signal.stop_loss))
min_risk_price = float(entry) * min_risk_pct
if risk_distance <= 0 or risk_distance < min_risk_price:
    signal.status = "SKIPPED"
    await self._mark_signal(
        signal.db_id, "SKIPPED",
        f"Risk distance {risk_distance:.6f} below min {min_risk_price:.6f}")
    return TradeDecision("SKIP", skip_reason=f"Risk distance too small")
```
**Rationale**: Prevent stops tighter than 0.5% of entry price
**Expected Impact**: Eliminate extreme fee drag (6-7R) trades
**Verification**: Check signal skip reasons for "Risk distance too small"
**Dependencies**: None
**Estimated effort**: 2 hours

### Task 3: Fix rr_min Production Setting
**Assigned to**: DevOps Agent
**File**: Database settings table (production)
**Current**: `rr_min = 4.0`
**Target**: `rr_min = 3.0`
**Rationale**: Align with AGENTS.md specification, improve TP hit probability
**Expected Impact**: More setups qualify, targets are closer, higher TP hit rate
**Verification**: Query settings table to confirm change
**Dependencies**: None (settings hot-reload)
**Estimated effort**: 15 minutes

### Task 4: Reduce Trend Strength Requirement
**Assigned to**: DevOps Agent
**File**: Database settings table (production)
**Current**: `trade_min_trend_strength = STRONG`
**Target**: `trade_min_trend_strength = MODERATE`
**Rationale**: Current setting blocks 27 valid signals without improving win rate
**Expected Impact**: Increase trade frequency without sacrificing quality
**Verification**: Monitor signal skip reasons over 24h
**Dependencies**: None (settings hot-reload)
**Estimated effort**: 15 minutes

---

## Medium Term (Priority: MEDIUM)

### Task 5: Implement Maker Entries
**Assigned to**: Order/Execution Agent
**File**: `backend/services/strategy_service.py` (entry logic)
**Current**: Uses marketable limit orders (GTC)
**Target**: Use GTX (post-only) limit orders for maker fills
**Implementation requirements**:
- Change limit order submission to use `timeInForce: GTX`
- Handle fill rejection when order would cross spread
- Implement fallback to taker order if GTX fails
- Update fill detection logic
**Rationale**: Reduce fees from 0.045% to 0.018% per side
**Expected Impact**: Round-trip cost: 0.11% → 0.074% (33% reduction)
**Risk**: Lower fill rate, requires order lifecycle redesign
**Verification**: Track fill rate before/after, monitor fee savings
**Dependencies**: Order lifecycle understanding
**Estimated effort**: 8 hours

### Task 6: Verify Breakeven Logic
**Assigned to**: Order/Execution Agent
**File**: `backend/core/position_manager.py`
**Current**: BE at 0.5R exists but may not be working
**Task**: Verify breakeven stop is triggered correctly in production
**Checklist**:
- Review BE trigger logic in position manager
- Check if BE is placed on exchange
- Verify BE moves to actual entry price
- Test in paper mode first
**Rationale**: Protect gains from reversals
**Expected Impact**: Convert -1R losses to 0R on partial moves
**Verification**: Paper test showing BE activation
**Dependencies**: None
**Estimated effort**: 2 hours

### Task 7: Add Partial Scale-Out
**Assigned to**: Order/Execution Agent
**File**: `backend/core/position_manager.py`
**Current**: No partial scale-out implemented
**Target**: Close 50% at +1R, move SL to breakeven
**Implementation requirements**:
- Add scale-out trigger at +1R
- Calculate 50% close quantity
- Place partial close order
- Move remaining position SL to breakeven
- Update position tracking for split positions
**Rationale**: Lock in partial profit, reduce risk
**Expected Impact**: Improve risk-adjusted returns
**Verification**: Backtest with partial scale-out logic
**Dependencies**: Breakeven logic verification
**Estimated effort**: 6 hours

### Task 8: Implement Session Filters
**Assigned to**: Research Agent
**File**: `backend/services/signal_service.py`
**Current**: No session filters
**Target**: Add configurable session filters (e.g., avoid Asian session for USD pairs)
**Implementation requirements**:
- Add session filter setting to settings table
- Implement session detection in signal_service
- Skip signals outside allowed session windows
- Add session filter to backtest harness
**Rationale**: Only trade during high-liquidity hours
**Expected Impact**: Higher quality setups, fewer chop trades
**Verification**: Backtest comparing session-filtered vs unfiltered
**Dependencies**: Session time research
**Estimated effort**: 4 hours

---

## Long Term (Priority: LOW)

### Task 9: Implement Paginated Backtest
**Assigned to**: Backtest Agent
**File**: `backend/backtest_data.py`
**Current**: Single-page fetch capped at 1000 bars (3.5 days of 5m data)
**Target**: Implement paginated kline fetching for 45-day window
**Implementation requirements**:
- Add pagination logic to `get_klines_window`
- Track cursor/startTime across pages
- Align all timeframes to same [start, end) window
- Verify no lookahead across page boundaries
- Add regression tests for pagination
**Rationale**: Current 3.5-day sample is insufficient for statistical significance
**Expected Impact**: Determine if strategy has genuine edge with corrected parameters
**Verification**: Successfully fetch 45 days of 5m data without hitting rate limits
**Dependencies**: None
**Estimated effort**: 12 hours

### Task 10: Run Baseline Backtest
**Assigned to**: Backtest Agent
**File**: `scripts/backtest.py` (or create new script)
**Current**: No recent backtest with corrected parameters
**Target**: Run 45-day backtest with current production parameters
**Configuration**:
- Symbols: All 7 production symbols
- Timeframe: 45 days
- Strategy: 3CANDLE only
- Fees: 0.11% round trip
- Parameters: Current production (atr_stop_mult=1.5, rr_min=4.0)
**Rationale**: Establish baseline for comparison
**Expected Impact**: Baseline net R expectancy for comparison
**Verification**: Document results in analysis/
**Dependencies**: Task 9 (paginated backtest)
**Estimated effort**: 2 hours

### Task 11: Run Corrected Backtest
**Assigned to**: Backtest Agent
**File**: `scripts/backtest.py`
**Current**: No backtest with corrected parameters
**Target**: Run 45-day backtest with corrected parameters
**Configuration**:
- Symbols: All 7 production symbols
- Timeframe: 45 days
- Strategy: 3CANDLE only
- Fees: 0.11% round trip
- Parameters: Corrected (atr_stop_mult=2.5, rr_min=3.0, stop_floor=0.5%)
**Rationale**: Measure improvement from immediate fixes
**Expected Impact**: Compare net R expectancy vs baseline
**Verification**: Document results in analysis/
**Dependencies**: Tasks 1, 2, 3, 9, 10
**Estimated effort**: 2 hours

### Task 12: Test Symbol-Specific Performance
**Assigned to**: Research Agent
**File**: Analysis document or new backtest script
**Current**: One-size-fits-all parameters
**Target**: Analyze why BTC LONG wins (+0.10R/trade) while BNB loses (-1.20R SHORT, -0.83R LONG)
**Research questions**:
- Does BTC have better volatility characteristics for 3Candle?
- Does BNB have unfavorable price action patterns?
- Are certain symbols better suited to specific session times?
- Should losing symbols be disabled?
**Rationale**: BTC LONG shows edge, BNB shows loss, symbol-specific tuning needed
**Expected Impact**: Recommendation to disable/enable specific symbols
**Verification**: Statistical analysis of symbol performance
**Dependencies**: Task 10 (baseline backtest)
**Estimated effort**: 6 hours

### Task 13: Add Per-Symbol Configuration
**Assigned to**: Strategy Agent
**File**: Database schema and settings service
**Current**: No per-symbol configuration
**Target**: Add per-symbol ATR multipliers, R:R requirements, enable/disable flags
**Implementation requirements**:
- Add symbol_settings table (symbol, atr_mult, rr_min, enabled)
- Update signal_service to read per-symbol settings
- Update settings API to manage symbol-specific settings
- Add migration for new table
**Rationale**: Optimize parameters for winning symbols, disable losers
**Expected Impact**: Better risk-adjusted returns
**Verification**: Backtest with per-symbol settings
**Dependencies**: Task 12 (symbol performance analysis)
**Estimated effort**: 8 hours

### Task 14: Enable OBFVG for Paper Testing
**Assigned to**: DevOps Agent
**File**: Database settings table
**Current**: `strategy_obfvg_enabled = false`
**Target**: `strategy_obfvg_enabled = true` in paper mode only
**Rationale**: Only strategy with positive backtest (+0.315R/trade)
**Expected Impact**: Gather real-paper data on OBFVG performance
**Caveat**: Sample size was 84 trades over 3.5 days (inconclusive)
**Verification**: Monitor paper OBFVG trades for 2 weeks
**Dependencies**: None
**Estimated effort**: 15 minutes

### Task 15: Extend OBFVG Validation Window
**Assigned to**: Backtest Agent
**File**: `scripts/strategy_compare.py` or backtest harness
**Current**: OBFVG measured over 3.5 days (84 trades)
**Target**: Run OBFVG backtest over 45 days with paginated data
**Configuration**:
- Strategy: OBFVG only
- Timeframe: 45 days
- Symbols: All 7 production symbols
- Fees: 0.11% round trip
- Parameters: Production OBFVG settings
**Rationale**: Determine if OBFVG has genuine edge with proper sample size
**Expected Impact**: Statistical confidence in OBFVG performance
**Verification**: Document results in analysis/
**Dependencies**: Task 9 (paginated backtest)
**Estimated effort**: 2 hours

### Task 16: Tune Regime Filter Parameters
**Assigned to**: Research Agent
**File**: Analysis document or backtest script
**Current**: ADX < 20 blocks as RANGING, blocks 31 signals in 7 days
**Target**: Test different ADX thresholds (15, 18, 20, 22, 25)
**Research questions**:
- Is ADX < 20 too strict?
- Does MODERATE regime (ADX 20-25) produce better trades?
- Should Ranging filter be disabled entirely?
**Rationale**: Current filter may be too strict, reducing trade frequency without improving quality
**Expected Impact**: Optimal ADX threshold for signal quality
**Verification**: Backtest comparison across ADX thresholds
**Dependencies**: Task 10 (baseline backtest)
**Estimated effort**: 4 hours

---

## Task Dependencies

```
Immediate (can start now):
- Task 1: Increase ATR multiplier
- Task 2: Add minimum stop floor
- Task 3: Fix rr_min setting
- Task 4: Reduce trend strength
- Task 6: Verify breakeven logic

Medium (after immediate):
- Task 5: Maker entries (independent, but lower priority)
- Task 7: Partial scale-out (depends on Task 6)
- Task 8: Session filters (independent)

Long (after medium):
- Task 9: Paginated backtest (foundational for rest)
- Task 10: Baseline backtest (depends on Task 9)
- Task 11: Corrected backtest (depends on Tasks 1,2,3,9,10)
- Task 12: Symbol analysis (depends on Task 10)
- Task 13: Per-symbol config (depends on Task 12)
- Task 14: OBFVG paper test (independent, can do now)
- Task 15: OBFVG backtest (depends on Task 9)
- Task 16: Regime tuning (depends on Task 10)
```

---

## Execution Order Recommendation

### Phase 1: Stop Bleeding (Week 1)
1. Task 3: Fix rr_min setting (15 min)
2. Task 4: Reduce trend strength (15 min)
3. Task 1: Increase ATR multiplier (1 hour)
4. Task 2: Add minimum stop floor (2 hours)
5. Task 10: Baseline backtest (after Task 9, can run in parallel)

### Phase 2: Fix Economics (Week 2-3)
6. Task 9: Paginated backtest (12 hours) - priority
7. Task 6: Verify breakeven logic (2 hours)
8. Task 11: Corrected backtest (2 hours)
9. Task 5: Maker entries (8 hours) - if budget allows
10. Task 7: Partial scale-out (6 hours) - after Task 6

### Phase 3: Prove Edge (Week 4+)
11. Task 12: Symbol analysis (6 hours)
12. Task 16: Regime tuning (4 hours)
13. Task 13: Per-symbol config (8 hours) - if Task 12 recommends
14. Task 14: OBFVG paper test (15 min) - can start now
15. Task 15: OBFVG backtest (2 hours) - after Task 9
16. Task 8: Session filters (4 hours) - if other fixes insufficient

---

## Success Criteria

### Phase 1 Success
- Stop distance >0.5% on 90%+ of trades
- Fee drag per trade <0.3R (down from 0.76R)
- More setups qualify (MODERATE trend allowed)
- Baseline backtest completed with 45-day data

### Phase 2 Success
- Breakeven logic verified working
- Corrected backtest shows net-positive expectancy
- Maker entries implemented (if budget allows)
- Partial scale-out implemented (if BE works)

### Phase 3 Success
- Symbol-specific recommendations documented
- Regime filter optimized or disabled
- OBFVG performance validated with proper sample size
- Strategy with proven edge (net-positive over 45 days)

---

## Agent Coordination Notes

### Strategy Agent
- Focus on Tasks 1, 2, 13
- Coordinate with Backtest Agent on parameter testing
- Consult Research Agent on symbol-specific behavior

### Backtest Agent
- Priority: Task 9 (paginated backtest) is blocking
- Run Tasks 10, 11, 15 for comparison
- Provide results to Research Agent for analysis

### Order/Execution Agent
- Focus on Tasks 5, 6, 7
- Test in paper mode before production
- Coordinate with DevOps on settings changes

### Research Agent
- Support Strategy Agent with symbol analysis (Task 12)
- Test regime filter parameters (Task 16)
- Provide recommendations for Task 13

### DevOps Agent
- Quick wins: Tasks 3, 4, 14 (settings changes)
- Monitor production after parameter changes
- Coordinate with all agents on deployments

---

## Documentation Updates

After each task completion:
1. Update relevant AGENTS.md files
2. Document results in analysis/
3. Update this task list with status
4. Note any new issues discovered
5. Record deploy decisions in DECISION.md

---

**Task List Status**: Phase 1 in progress
- Task 1 DONE (local commit `da597ca`, not yet deployed): `atr_stop_mult` 1.5→2.5 in `backend/indicators/three_candle.py`; regression test updated; 698 tests pass.
- Task 2 DONE (local, pending commit): `min_risk_pct` default 0.05%→0.5% in `strategy_service.py` + `backtest.py`. Guard already existed; production has no `min_risk_pct` row so the new default applies on next deploy.
- Task 3 DONE (production, hot-reloaded): `rr_min` 4.0→3.0 via `PUT /api/settings/trading`.
- Task 4 DONE (production, hot-reloaded): `trade_min_trend_strength` STRONG→MODERATE via `PUT /api/settings/trading`.
- Housekeeping: prod `atr_stop_mult` setting updated 1.5→2.5 for UI consistency (dead config for live 3CANDLE — module constant rules).
- Task 9 DONE (already implemented): `backend/backtest_data.py` provides paginated, interval-aware fetching pinned to one `[start, end)` window; `tests/backend/test_backtest_trust.py` pins pagination (45d → 12,960 5m bars), window alignment, and no-lookahead. Verified 2026-11-05.
- Task 10/11 DONE (local commit `b67fd00`): 45-day baseline (deployed: atr_stop_mult=1.5, min_risk_pct=0.0005) vs corrected (atr_stop_mult=2.5, min_risk_pct=0.005) backtests on the 7 production-traded symbols; results → `analysis/backtest-45d-2026-11-05.md`. **Verdict: the stop-floor changes made net expectancy WORSE** (expNetR -0.045 → -0.089; 472 → 300 closed trades). The deploy decision on Tasks 1-2 is now the user's and is still open.
- Task 12 DONE (read-only research, 2026-11-05): per-symbol analysis of the 35 live trades + cross-validation against the 45-day backtest → `analysis/session-symbol-research-2026-11-05.md`. **Result: no symbol reaches significance on the live sample (smallest p = 0.174), but BTCUSDT and BNBUSDT are net-negative in BOTH independent samples** (live n=13, backtest n=134) and LINKUSDT is positive in both (live n=5, backtest n=80). Live per-symbol averages: LINK +0.134R (n=5), UNI -0.068R (n=3), BTC -0.289R (n=8), OP -0.535R (n=3), SOL -0.564R (n=6), APE -0.639R (n=5), BNB -0.976R (n=5). **Do not build Task 13 (per-symbol config) from the live sample** — every live cell is under the 20-trade inconclusive floor; calibrate against the backtest cells (n=32-80) instead.
- Task 8 RESEARCHED, IMPLEMENTATION DEFERRED (2026-11-05): session/time-of-day analysis of the same 35 trades → `analysis/session-symbol-research-2026-11-05.md`. Asian 00-07 UTC is -0.739R/trade (n=14) vs London-core 08-16 UTC +0.016R/trade (n=17); raw permutation p = 0.034 but the Bonferroni threshold over the ~50 slices examined is ~0.001, and a 174,632-signal cross-check finds no session effect on signal→trade conversion (chi-square 4.82, df 4, p ~ 0.30). **Verdict: insufficient evidence — do NOT ship a session filter from this data.** Next step is a `--session` filter in `backend/backtest.py` measured on the 45-day window, then re-measure out of sample.
- **Production-universe warning (2026-11-05)**: the enabled symbol set is now APT/ARB/SOL/UNI/XRP, while APT, ARB and XRP have **zero trades in the entire database** and were not in the 45-day backtest universe. 60% of the live universe is unmeasured; the honest expected expectancy of the current live book is unknown, not the backtest's -0.045R.
**Next Action**: Deploy `da597ca`+ to production so Tasks 1-2 take effect (deploy decision open after the 45d verdict); record Task 10/11 + Task 8/12 results in DECISION.md; re-run the 45-day backtest on the *enabled* universe before any live restart
**Review Date**: After Phase 1 completion
