# Regime-conditional expectancy — 45d × 2 windows × 7 symbols (2026-11-05)

**Question.** Does 3CANDLE expectancy differ by the market regime it fired in,
and does the live regime GATE (which blocks `RANGING` and strong-regime
counter-trend entries) already capture that difference — or is there residual
conditional edge the gate misses?

**Harness change.** `BacktestTrade` now carries `regime`, `adx`, `er` and
`bias_score`, frozen at DETECTION time from the same cached 1h
`analyze_market_regime` the filter chain reads (`backend/backtest.py`). `er`
is the Kaufman Efficiency Ratio over the same closed 1h closes —
`|close_t − close_{t−20}| / Σ|Δclose|` via the new `efficiency_ratio()` helper.
`BacktestResult.regime_breakdown()` buckets closed-trade stats by the tag and
is included in `summary()`. No live code path touched; `signal_service` is
unchanged.

**Config (deployed):** 3CANDLE only (`--no-fvg --no-choch --no-obfvg`),
`rr_min` 3.0, `trade_min_trend_strength` MODERATE, neutral allowed, volume
filter off, ATR stop floor 1.5 (module default), `min_risk_pct` 0.005
(default), midpoint fills, taker+taker 0.11% round-trip cost model.
Universe: APE, BNB, BTC, LINK, OP, SOL, UNI — the 7-symbol research set.
Windows: **recent** = last 45d; **prior** = the disjoint 45d before it
(`--end-days-ago 45`). All buckets below have n ≥ 100 except where noted;
none is inconclusive by the n<20 rule.

## Per-regime expNetR

| Regime bucket | Recent trades | Recent expNetR | Prior trades | Prior expNetR |
|---|---|---|---|---|
| STRONG_BEAR | 184 | **-0.023** | 116 | **-0.041** |
| STRONG_BULL | 104 | **-0.071** | 162 | **+0.204** |
| WEAK_TREND  | 161 | **+0.003** | 154 | **-0.102** |
| RANGING     | 0   | — (gate blocks all) | 0 | — |

Pooled across both windows: STRONG_BULL +0.096 (n=266), WEAK_TREND -0.048
(n=315), STRONG_BEAR -0.030 (n=300).

- Recent window net: -4.23 - 7.43 + 0.49 = **-11.17R** over 449 trades.
- Prior window net: -4.76 + 32.96 - 15.68 = **+12.52R** over 432 trades.

## ER(20) split (Kaufman threshold 0.3)

| Window | ER < 0.3 | ER ≥ 0.3 |
|---|---|---|
| Recent | -0.023 (n=308) | -0.029 (n=141) |
| Prior  | -0.106 (n=288) | **+0.298** (n=144) |

Median ER ≈ 0.20–0.22 in both windows. In the prior window high-ER trades
carried essentially all the profit (+42.9R of +12.5R total net; the low-ER
half lost -30.4R). In the recent window the split is flat-to-negative in
both halves.

## Verdict

1. **Expectancy does NOT concentrate in a stable regime bucket.** The best
   bucket flips sign between windows: WEAK_TREND is +0.003 recent but -0.102
   prior; STRONG_BULL is -0.071 recent but +0.204 prior. Only STRONG_BEAR is
   consistently negative (-0.023 / -0.041) — but it is negative in BOTH
   windows at roughly fee-drag magnitude, i.e. no gross edge to protect.
   There is no regime bucket a gate could keep that is reliably positive.

2. **The existing regime GATE already captures the only robust
   conditional difference.** RANGING produced zero trades in both windows —
   `ranging_regime` skip counts were 658 (recent) and 424 (prior). Whatever
   chop-cost the gate avoids is already avoided. The residual variation
   among the three allowed buckets is window-dependent noise, not a
   stable conditional edge: pooled per-bucket expNetR is dominated by the
   prior-window STRONG_BULL cell (+32.96R), which is exactly the kind of
   single-window concentration the two-window design exists to discount.

3. **ER(20) shows the same instability.** A +0.298 expNetR high-ER cell in
   the prior window collapses to -0.029 in the recent window. ER is
   correlated with the STRONG_BULL prior cell (trending markets have high
   ER), so it is measuring the same window effect, not an independent edge.
   Not actionable as a filter on this evidence.

4. **Bottom line:** regime-conditional expectancy exists in the data but is
   NOT stable across disjoint windows. No regime-gate change is supported.
   The binding constraint remains the one already documented — pooled gross
   edge ≈ fee drag — not regime selection.

## Caveats

- Regime is tagged at DETECTION (signal bar close on the 1h context), not at
  fill — correct for "what state did the signal fire in", and identical to
  what the live gate reads.
- Trades are single-position-per-symbol serial replays; no portfolio slots.
- OHLC fills, touch-fill baseline (0bp penetration), modeled 0.11% RT cost.
- Exit simulation omits `_check_profit_trailing_2` (see `backend/AGENTS.md`),
  so absolute expNetR values are approximate; the per-bucket COMPARISON is
  the claim, and it is consistent within a run.
- RANGING is untestable here by construction — the gate removes it before
  entry. Measuring RANGING expectancy would require a gate-disabled
  counterfactual run (cheap follow-up if ever wanted).

## Reproduce

```
PYTHONPATH=. python3.13 /tmp/regime_bt.py   # driver aggregates regime_breakdown()
# per-symbol: scripts/backtest.py --symbols ... --days 45 --no-fvg --no-choch
#   --no-obfvg --rr-min 3.0 --strength MODERATE --neutral --no-volume
#   [--end-days-ago 45 for the prior window]
```
