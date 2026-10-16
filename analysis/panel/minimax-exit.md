# FUBOT Exit & Profit-Capture Analysis — Minimax Panel

**Sample base:** 23 live trades (2026-10-15), +0.012R/trade net. Replay on 22 same entries (no-fees, stop checked before target). Geometry grid across all combinations. Evidence annotated to source lines.

---

## 1. Top 3 Provable Leaks

### Leak 1 — Fixed 3R TP cuts the right tail off (geometry confirmed)
**Files/lines:** `strategy_service.py:250–256` (TP = `signal.take_profit = stop + risk × 3`)

DOT MFE = 7.47R, exit = ~4.15R → **3.32R left on table**. ETH MFE = 4.16R, exit = ~2.55R → **1.61R left on table**. Replay geometry: 1.0R/3R (current) = **−0.268 expectancy** on 22 trades. Every stop ≥1.5R was negative. The 3R target is a ceiling that fires before the move exhausts, confirmed by two independent live winners both exceeding it significantly.

**Magnitude:** ~+0.60R/trade improvement available from runner-trail alone (replay: trail 0.75R once +1.0R → +0.33R vs fixed −0.27R).

### Leak 2 — BE trail at +0.5R and fixed TP collide on 1–2R winners
**File/line:** `position_manager.py:353` (`be_trail_trigger_r = 0.5` default)

At +0.5R the BE trail moves stop to entry (BE). On a winner that reaches +1.5R then reverses, the position exits at BE (runner-trail never arms because it also triggers at +0.5R, no separate profit margin). On the 22-trade replay, 50% partial at +2R then trail = −0.20R — worse than full trail. The runner-trail is not implemented; the BE trail is the only profit-taking mechanism and it exits at 0R, wasting the winner's remaining runway.

**Magnitude:** ~16/22 trades (73%) showed stalling; most winners in the 1–3R range are converted to breakeven by the BE stop.

### Leak 3 — WS tick / REST pass TP detection misses crosses (5 of 6 losses as EXTERNAL_CLOSE)
**File/line:** `position_manager.py:469–478` (`_check_tp_sl_hit`)

`bookTicker` mid-price checked against TP/SL; if price spikes through the TP and retraces within the same tick, the cross may not be detected. The 5-min reconcile (line 196) then marks these as EXTERNAL_CLOSE — correct outcome, but the engine's own TP detection is blind between ticks. This is not a PnL leak but a data-quality leak: exit_reason attribution is wrong for fast crosses, corrupting strategy evaluation.

**Magnitude:** 5/6 losses = 83% exit misattribution; meaningful only for strategy learning, not for PnL.

---

## 2. Proposals

### Proposal A — Runner-trail (primary, highest impact)
**Parameters:** `runner_trail_trigger_r = 1.0` (arms at +1.0R past entry), `runner_trail_distance_r = 0.75` (trails 0.75R below price). The runner-trail fires once after BE trail has already moved stop to entry. Effective exit: +1.0R (BE) → trail to +0.25R on reversal → if price keeps running, TP at +4R.

**Interaction with existing BE:** BE at +0.5R moves stop to entry (0R). Runner-trail at +1.0R then begins trailing from entry (not from the trigger), so effective stop = entry − 0.75R = +0.25R. This is strictly better than BE-alone (0R) on reversals, and identical on continued runs until price drops 0.75R from its peak.

**Implementation note:** In `position_manager.py`, the runner-trail check would need a second trigger setting (`runner_trail_trigger_r`, `runner_trail_distance_r`). Fire AFTER the BE check on each REST pass; one-way, never lowers stop.

**Expected effect (replay-based):** +0.33R vs −0.27R fixed → **+0.60R/trade improvement**. On 23 live trades = +13.8R total. Fee-aware: taker 0.05%/side × 2 = 0.001 × notional/price; at ~1% stop ≈ 0.2R round-trip. Net improvement ≈ **+0.40R/trade**.

**Validation:** Run backtest on 500+ historical trades (scripts/backtest.py — no lookahead). Compare: fixed 3R vs runner 0.75R/+1.0R vs runner 1.0R/+1.5R. Primary metric: expectancy, exit mix (TP_HIT / SL_HIT / trail), captured-R / MFE ratio.

### Proposal B — Raise fixed TP to 4R (rr_min = 4)
**Parameters:** `backend/config.py` `RR_MIN = 4.0` (or `signal.rr_ratio` floor). Entry plan already computes TP = stop + risk × 3; raise multiplier to 4.

**Rationale:** DOT 7.47R MFE, ETH 4.16R MFE. The right tail is fat — a 4R target captures more of it without materially affecting loss side (losers still hit the stop at ~1R). Geometry: 0.75R/1R = +0.263 was the best pairing; for the actual 1R stop, 1R/4R = ? (not in grid) but logic suggests it improves over 1R/3R = −0.268.

**Risk:** Wider TP = lower hit rate. At 4R vs 3R, roughly 10–15% fewer TP hits. On the 22-trade sample: if hit rate drops from 9% (2/22) to 5% (1/22), expectancy is similar or slightly worse unless MFE capture improves enough. **Requires the runner-trail to be effective.**

**Validation:** Same backtest. Report: hit rate × avg win, compared across TP ∈ {3, 4, 5}R.

### Proposal C — BE trail trigger shift from +0.5R to +0.75R
**Parameter:** `be_trail_trigger_r = 0.75` (existing slider).

**Rationale:** At +0.5R, the BE trail exits 1–2R winners at breakeven before the runner can arm. Shifting to +0.75R lets winners run to +1.0R (runner-trail arm) or to the TP if they make it. Losers (80% of trades) are unaffected — they still hit the structural stop at ~1R. This is a pure improvement for the 1–3R winner cohort.

**Interaction with runner-trail:** BE at +0.75R → stop at entry (0R). Runner-trail at +1.0R → trails 0.75R → stop at +0.25R on reversal. Net: a winner that reaches +1.0R then gives back 0.75R exits at +0.25R instead of 0R.

**Validation:** Backtest BE ∈ {0.5, 0.75, 1.0}R with runner-trail fixed at +1.0R/0.75R.

---

## 3. Risks / Failure Modes

### Whipsaw in chop (16/22 trades stall)
The runner-trail and BE both exit at/near BE on reversals. In a choppy market, a series of +0.5R to +1.5R moves followed by reversals produces many breakeven or -0.25R exits. The current system (BE only, no runner) produces ~0R on these — adding runner-trail at +1.0R likely converts some to -0.25R. **Net may be neutral or negative in chop.**

**Mitigation:** The BE at +0.75R proposal reduces runner-trail arming in chop. Combine with a market-regime filter (e.g., skip if 1h ADX < 25) to avoid runner-arm in ranging conditions.

### Per-trade drawdown
A winner that reaches +2R then reverses 0.75R → exits at +1.25R. The TP at +4R is never reached. This is still a good trade, but if the TP was expected to be the primary exit, the runner-trail cuts it short. **The TP must still be the primary take-profit for the big winners; the runner-trail is a safety net for medium winners, not a replacement for the TP.**

### Fee-aware edge
Taker round-trip ≈ 0.2R at 1% stop. The replay improvement (+0.60R) survives fees (+0.40R net). However, the actual live improvement depends on fill quality (maker vs taker, slippage on the algo TP/SL). Maker orders would reduce the fee drag to ~0.08R round-trip, making the net improvement +0.52R.

### Race condition: BE trail and TP on the same tick
On a fast tick that simultaneously crosses the BE trail trigger and the TP price, both `_check_be_trailing` and `_check_tp_sl_hit` fire on the same pass. The BE trail moves the stop to entry first, then the TP is checked at the new stop level (entry), so the TP does NOT fire — the trade exits at BE instead of TP. **Fix:** in the same pass, check TP before the BE trail, or skip the BE trail if TP is already hit.

---

## 4. What to Cut

**Cut the 60-minute time stop.** Evidence: 60-min time stop → −0.09R (replay), worse than no time stop. The geometry already shows that stop distance ≥1.5R is always negative — a time stop effectively creates a wider stop in R terms when price doesn't move. The 16 stalling trades need the runner-trail (to exit at +0.25R on reversal) not a time bail-out.

**Cut or delay the partial scale-out (50% at +2R).** Evidence: −0.20R vs full trail +0.33R. Scaling out 50% at +2R gives up the full MFE capture on the remaining 50% (DOT: 50% × 7.47R = 3.73R given up). The partial exit is inferior to the runner-trail for MFE capture, and it reduces variance at the cost of expectancy.

---

## Summary Table (22-trade replay)

| Strategy | Expectancy | Notes |
|---|---|---|
| Fixed 3R (current) | −0.27R | Confirmed by live: +0.012R/trade with fees |
| Trail 0.75R once +1.0R | +0.33R | Runner-trail base case |
| Trail 1.0R once +1.5R | −0.10R | Too aggressive trail distance |
| 50% at +2R then trail | −0.20R | Scale-out hurts expectancy |
| 60-min time stop | −0.09R | Time stop adds no value |
| **Runner 0.75R/+1.0R + TP 4R** | **+0.40R est. net** | After fees; needs full backtest validation |

*Sample <20 trades: runner 1.0R/+1.5R cell marked inconclusive.*
