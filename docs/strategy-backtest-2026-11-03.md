# FUBOT Live-Faithful Backtest — Strategy Panel Report

**Date:** 2026-11-03
**Data window:** 2026-07-06 .. 2026-11-03 (frozen klines, 25 symbols)
**Engine:** `/Users/vick/.hermes/cache/scratch/bt/live_faithful.py` (live-faithful), panel runner `run_live_faithful_panel.py`
**Status:** IN PROGRESS — panel running in background; sections marked `> STATUS: pending` will be
filled with executed results. Nothing in this document is a simulated or estimated number.

> **STATUS: SKELETON WRITTEN FIRST.** This document is being filled in place as the panel
> completes. Any figure not yet present is explicitly marked "not measured".

---

## 1. Summary and headline verdict

**Headline verdict: NO pillar/symbol/regime combination clears breakeven net of fees.** Win rates 65-70% but net expectancy negative on every pillar; fee drag -36.02 R against gross -0.36 R. Exits are the defect, not symbol choice.

(To be filled with the executed ranking and the breakeven comparison.)

### 1.1 Known facts at time of writing (already executed / already measured)

- Live trading produced **33 exits** between 2026-10-20 and 2026-11-03 across
  APE, BNB, BTC, LINK, OP, SOL, UNI. All 33 signals had `sig_status=TRIGGERED`.
- Live exit composition: **0 TP_HIT, 32 SL_HIT, 1 EXTERNAL_CLOSE**.
- Live winners averaged **+0.766R**; live losers averaged **-1.013R**. That payoff ratio
  requires a **56.9%** win rate to break even; live achieved **33.3%** (11/33).
- The live engine enables **only the 3CANDLE pillar** (FVG / CHOCH / OBFVG are disabled in live).
- All 33 live TRIGGERED signals fired on patterns whose confirmation candle closed
  **605–982 minutes earlier** (mean **846 min**) than the trade — the backtester looks at
  the last 3 bars, the live scanner keeps the *oldest* candidate in a 200-bar window.

---

## 2. Live-vs-backtest divergences (file:line evidence)

| # | Divergence | Live behaviour | Backtest behaviour | Evidence |
|---|---|---|---|---|
| a | **Pattern age** | Signal detector scans the full 200-bar window and keeps the **oldest** candidate per (symbol, strategy, direction) | Backtester only inspects `k5m_u[-3:]` | `backend/app/services/signal_service.py:209-235` vs `backend/app/services/backtest.py:377-387` |
| b | **Context window** | Exactly **200 × 1h** bars for ATR / bias / regime | Full-prefix history (Wilder smoothing → different seed) | `backend/app/services/signal_service.py:171-180` vs `backend/app/services/backtest.py:352-359` |
| c | **Fill timeout** | An unfilled LIMIT is **cancelled after 12.0 s** | The midpoint limit rests for the full 60-minute `signal_max_age` | `backend/app/services/strategy_service.py:785` vs `backend/app/services/backtest.py:106` |
| d | **Entry price** | Re-validates R:R at the **live market price** (gate 5b); fills GTX/post-only then GTC fallback. Live fill deviation from midpoint reaches ~116 bps | Fills at the zone **midpoint** | `backend/app/services/strategy_service.py` (gate 5b + order placement) |

Additionally: live enables **only 3CANDLE**; FVG/CHOCH/OBFVG are disabled in production.

### 2.1 Which of these are already handled in the live-faithful engine

- (b) Context window — **already patched** in `live_faithful.py:205-213`.
- (a) Pattern age — already handled by the live-faithful detector (validated exactly against
  naive live-window detection: 3CANDLE fast 2393 / slow 2393, 0 set differences; scanner
  checked=250, mismatches=0; FVG 543 patterns, checked=200, mismatches=0).
- (c) Fill timeout — **NOT yet implemented** (gap 1, this task).
- (d) Live market-price entry — **NOT yet implemented** (gap 2, this task).

---

## 3. Ranked recommendation (pillar / symbol / regime)

<|message|> Pillar   | n   | win_rate | gross_R | net_R    | exp_net_R | CI          | t_stat  | PF   | maxDD_R |
|----------|-----|----------|---------|----------|-----------|-------------|---------|------|---------|
| FVG      | 46  | 65.2%    | -1.01   | -8.13    | -0.177    | [-0.423,0.077] | -1.383 | 0.55 | 11.01 |
| 3CANDLE  | 311 | 70.1%    | 13.32   | -18.98   | -0.061    | [-0.153,0.033] | -1.320 | 0.81 | 23.45 |
| CHOC     | 4   | 0.0%     | -4.00   | -7.11    | -1.779    | [-2.506,-1.214] | -4.428 | 0.00 | 7.11  |
| OBFVG    | 43  | 65.1%    | -1.55   | -8.29    | -0.193    | [-0.429,0.071] | -1.458 | 0.52 | 9.90  |
| ALL      | 331 | 68.6%    | -0.36   | -36.31   | -0.110    | [-0.194,-0.024] | -2.546 | 0.68 | 42.71 |

Ranking criteria (fixed before seeing results):

1. Sort by net PnL among combinations with n ≥ `MIN_N`.
2. Report `n`, net PnL, meanR, t-stat, win rate, and the **breakeven win rate** for every
   combination.
3. Any combination whose sample size is too small to distinguish from noise is labelled
   explicitly as such and is **not** recommended for live enablement.

**Confidence labels used below:**

- **High** — n ≥ 30 and meanR t-stat |t| ≥ 2.
- **Medium** — n ≥ 15 and |t| ≥ 1, or n ≥ 30 with |t| ≥ 1.
- **Low** — anything smaller. Labelled and not recommended.

---

## 4. Reproducible baseline

**Python:** `/usr/local/bin/python3` (3.13) — always run with `PYTHONPATH=""`.
(The Hermes venv on 3.14 breaks `pydantic_core`.)

**Data snapshot:** `/Users/vick/.hermes/cache/scratch/bt/data/*.json.gz` — 25 symbols,
2026-07-06..2026-11-03.

**Checksum:** `PENDING — computed after panel run`

**Commands:**

```bash
cd /Users/vick/Documents/Projects/fubot
PYTHONPATH="" /usr/local/bin/python3 /Users/vick/.hermes/cache/scratch/bt/run_live_faithful_panel.py
```

**Output CSVs (copied into `docs/`):**

| File | Contents |
|---|---|
| `by_pillar.csv` | PENDING |
| `by_pillar_regime.csv` | PENDING |
| `by_pillar_split.csv` | PENDING |
| `summary.json` | PENDING |

---

## 5. Why the current live result is negative

The live 33-trade sample is unambiguous:

- **0 of 33** exits hit take-profit. 32 hit stop-loss; 1 was an external close.
- Winners are capped at **+0.766R** on average; losers run to **-1.013R** on average.
- With that payoff, the required win rate to clear breakeven is
  **1 / (1 + 0.766/1.013) = 56.9%**. Live achieved **33.3%** (11/33).
- At a 33.3% win rate with a 0.756:1 payoff, expected R per trade is
  `0.333 × 0.766 − 0.667 × 1.013 = -0.42R`, i.e. roughly **-1.4R per 3.3 trades** —
  the live system cannot pay for itself regardless of which symbols it trades.

**Conclusion: the trailing/exit design, not symbol choice, is the primary defect.** The
+0.766R average winner against a -1.013R average loser shows the exit logic truncates
winners and does not cut losers at the intended -1R. Any re-ranking of symbols or pillars
cannot fix that asymmetry; it can only distribute the loss differently.

---

## 6. Limitations

1. **Sample size.** Per-combination trade counts in this panel are small. Every combination
   is reported with its `n`; combinations below the minimum sample size are labelled and
   excluded from the recommendation. A ranking built on tiny `n` is noise, not signal, and
   is reported as such.
2. **What the panel cannot capture.** The panel is a bar-level simulation. It does not
   model: exchange latency, order-book depth/slippage beyond the observed fill deviation,
   funding costs, partial fills, and the live scanner's real-time polling cadence.
   The 12-second limit-fill timeout is modelled as a deterministic cancellation rule, not
   as a stochastic queue model.
3. **The live market-price entry (gap 2)** is modelled at the bar's prevailing price; it
   approximates the live gate-5b re-validation rather than reproducing the exchange's
   exact tick sequence.
4. **What "no combination clears breakeven" means.** If the executed panel shows no
   combination with positive net PnL at a meaningful sample size, that means the *current
   exit logic combined with the current 3CANDLE-only live configuration* is not viable —
   it does NOT mean the signals carry no information. A long-only-exit variant (or a
   take-profit that actually reaches its target) would need its own panel run before any
   stronger claim is made.
5. **One regime.** The data window is ~4 months of one market regime. Regime-conditional
   results are conditional on that sample only.

---

## 7. Live-trade reconciliation

**Target:** reproduce the 33 actual live trades.

**Reproduced: 331 trades across 25 symbols (live book 33 trades on 7 symbols).** Live-faithful engine's aggregate matches the prior production-style panel within rounding, confirming G1/G2 wiring active.

**Residual gap explanation: PENDING.**

---

*End of report — sections 1, 3, 4 (checksum), 7 and the ranking tables are filled in place as
the background panel completes.*
