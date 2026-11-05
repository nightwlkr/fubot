# Exit-Parameter Sweep — 2026-11-05

**Purpose**: test whether any *exit* configuration improves net expectancy on the
7-symbol universe, given the finding in `analysis/backtest-enabled-universe-2026-11-05.md`
that only 1 of 321 trades reached the 3R target (68% exit at the BE trail) and
that gross edge ≈ fee drag. Research only — **no production setting, order,
service or repo file was changed; nothing was committed.**

**Question**: the "exit structure" is the blocker. Can re-shaping the exit fix it?

**Answer: no.** No exit configuration in this sweep produces a meaningfully
better `expNetR` than the **-0.025** deployed baseline. The best swept cell is
**+0.006** — which sits at the **0.1st percentile** of the pure-noise null for
the best of 11 cells (i.e. *worse* than what noise alone would typically
produce). The sweep also uncovered a **floating-point defect** in the harness
that made the raw `rr_min=2.5` cell look like the only positive configuration in
the entire study; it is an artifact, not a strategy.

---

## 1. Method

**Shared-window replay** (the repo's own precedent — `analysis/backtest-45d-2026-11-05.md`
§"Method note — one shared data window"): one candle set per symbol is fetched
and replayed through N `Backtester` instances, so every config sees byte-identical
data. Two runs separated in time drift by 1–2 trades because `window_for_days()`
anchors the end to the last closed 5m bar at wall-clock time, which would have
made a cross-cell comparison partly noise.

| Parameter | Value |
|---|---|
| Window | 2026-09-21 06:05 → 2026-11-05 06:05 UTC (45d, all three intervals pinned to one `[start, end)`) |
| Universe | APEUSDT, BNBUSDT, BTCUSDT, LINKUSDT, OPUSDT, SOLUSDT, UNIUSDT |
| Strategies | 3CANDLE only (`--no-fvg --no-choch --no-obfvg`) |
| `atr_stop_mult` | 1.5 (deployed) |
| `min_risk_pct` | 0.005 (deployed) |
| `rr_min` | 3.0 baseline (deployed) |
| Trend | MODERATE, neutral allowed, volume filter off |
| Fees | 0.11% round trip (`backend/core/cost_model.py`) |
| Fills | touch-fill at the zone midpoint (`entry_penetration_bps=0`) |

**Validation.** The replay reproduces the published control exactly. The
uncorrected baseline run gives **451 closed, expNetR -0.027**, matching
`analysis/backtest-broad-universe-2026-11-05.md` ("a control re-run under the
deployed config reproduces D2's TOTAL (452 closed, -0.027 expNetR)"), and the
per-symbol cells match: LINK **+0.117** (published +0.117), SOL **-0.004**
(published -0.004), UNI **+0.161** (published +0.157 — 1-trade window drift).

**CLI cross-check.** The four `rr_min` cells were also run through the real CLI
(`scripts/backtest.py --rr-min …`) in a separate process with its own fetch.
The CLI reproduced the shared-window numbers **exactly** for every cell:

| rr_min | CLI (separate process) | Shared-window replay |
|---|---|---|
| 2.0 | 449 closed, gross 31.15, net -10.70, expNetR **-0.024** | 449 / 31.15 / -10.70 / -0.024 |
| 2.5 | 378 closed, gross 38.57, net 3.52, expNetR **+0.009** | 378 / 38.57 / 3.52 / +0.009 |
| 3.0 | 451 closed, gross 29.71, net -12.23, expNetR **-0.027** | 451 / 29.71 / -12.23 / -0.027 |
| 4.0 | 448 closed, gross 31.08, net -10.62, expNetR **-0.024** | 448 / 31.08 / -10.62 / -0.024 |

**Working-tree note.** During this study the working tree carried an unrelated
uncommitted change to `backend/backtest.py` (a `backtest_entry_mode="maker_edge"`
experiment, plus regime tagging). It is purely additive and its default is
`"midpoint"`, so the path measured here is unchanged. No file in the repo was
edited by this study.

---

## 2. Knob reachability

The harness reads more knobs than the CLI exposes. Per the brief, no repo file
was edited to widen the CLI; instead the trailing knobs were passed through the
**settings dict that `Backtester.__init__` already reads**
(`backend/backtest.py:376-379`) — the harness's own documented A/B channel
("Pass `None` values to disable", `backend/backtest.py:374-379`). The CLI does
**not** support extra settings; that limitation is recorded here rather than
worked around by editing `scripts/backtest.py`.

| Knob | Harness reads it | CLI-exposed | Swept |
|---|---|---|---|
| `rr_min` | `backend/backtest.py:381` | **yes** — `--rr-min` (`scripts/backtest.py:32`) | yes |
| `be_trail_trigger_r` | `backend/backtest.py:377` | no | yes (settings dict) |
| `profit_lock_trigger_r` | `backend/backtest.py:378` | no | yes (settings dict) |
| `profit_lock_trail_r` | `backend/backtest.py:379` | no | yes (settings dict) |
| `profit_lock_trigger_r2` / `profit_lock_trail_r2` | **no** — the harness omits `_check_profit_trailing_2` entirely | no | **skipped — unreachable** |
| `runner_trail_trigger_r` / `runner_trail_distance_r` | **no** — not in the harness at all | no | **skipped — unreachable** |
| `stale_trade_hours` | **no** — `_check_stale_trade` is not modelled | no | **skipped — unreachable** |

The three unreachable groups are live exit rules (`backend/core/position_manager.py:829-830`,
`:871-872`, `:910-917`). Their absence is a **caveat on the absolute numbers**
(§8), not a reason to skip the sweep.

---

## 3. Harness defect found: `rr_min` rejects setups on floating-point noise

This must be read before the results table, because it invalidates the first
version of the sweep.

`evaluate_three_candle_entry` **defines** the target from `rr_min`:

```
backend/indicators/three_candle.py:151    tp = entry + (risk * rr_min)
backend/indicators/three_candle.py:158    tp = entry - (risk * rr_min)
backend/indicators/three_candle.py:160    rr = abs(tp - entry) / risk
backend/indicators/three_candle.py:167    "is_valid": risk > 0 and rr >= rr_min,
```

In exact arithmetic `rr` is **identically** `rr_min`, so `rr >= rr_min` is a
**tautology** — the check can never reject anything. Floating-point rounding
breaks it: the recovered `rr` can land a hair *below* `rr_min` (observed
deltas ~ -2e-15), and the setup is then silently rejected in **two** places:
`is_valid` at `three_candle.py:167`, and `_passes_filters`'s `rr < self.rr_min`
at `backend/backtest.py:605`.

Measured rejection rate (instrumenting the indicator, 4063 setups evaluated
per config):

| `rr_min` | setups rejected by the tautological check | trades (raw) | trades (corrected) |
|---|---|---|---|
| 2.0 | 10 (0.25%) | 449 | 452 |
| **2.5** | **982 (24.17%)** | **378** | **451** |
| 3.0 | 23 (0.57%) | 451 | 452 |
| 4.0 | 27 (0.66%) | 448 | 452 |

**The raw `rr_min=2.5` cell was not a strategy configuration** — it was
"rr_min 2.5 with 24% of setups randomly deleted by float noise", and it
produced the only positive `expNetR` in the raw sweep (**+0.009**). Repairing
the tautology (restoring the exact `rr_ratio`) in a throwaway patch collapses
that cell back onto its neighbours (**-0.026**). All results below are
**artifact-corrected**; the raw run is kept for the record.

Two consequences beyond this study:

- **The live scanner loses the same setups.** `evaluate_three_candle_entry` is
  the production path, so at the deployed `rr_min=3.0` roughly **0.57% of
  setups are rejected by float noise** (and ~0.66% at 4.0). Small, but it is a
  real, free loss of signals in production.
- The defect is **not** the reason the strategy is unprofitable — see §6, where
  the corrected numbers show the exit structure is simply not the binding
  constraint either.

---

## 4. Results (artifact-corrected)

All 11 cells, one shared data window. `TP` / `BE` / `SL` are exit-reason
counts (`TP_HIT` / `BE_STOP` / `SL_HIT`).

| # | config | closed | win% | TP | BE | SL | grossR | netR | **expNetR** | t | 95% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | **BASELINE** rr3.0 | 452 | 38.1% | 4 | 298 | 150 | +30.80 | -11.19 | **-0.025** | -0.56 | [-0.111, +0.061] |
| 1 | rr_min 2.0 | 452 | 38.1% | **21** | 281 | 150 | +30.13 | -11.86 | **-0.026** | -0.61 | [-0.111, +0.059] |
| 2 | rr_min 2.5 | 451 | 38.1% | 8 | 293 | 150 | +30.12 | -11.78 | **-0.026** | -0.60 | [-0.112, +0.059] |
| 3 | rr_min 4.0 | 452 | 38.1% | 1 | 301 | 150 | +31.49 | -10.49 | **-0.023** | -0.53 | [-0.110, +0.063] |
| 4 | be_trail 0.75 | 420 | 45.2% | 5 | 240 | 175 | +32.04 | -7.17 | **-0.017** | -0.33 | [-0.117, +0.083] |
| 5 | be_trail 1.0 | 388 | 49.7% | 7 | 187 | 194 | +22.35 | -14.10 | **-0.036** | -0.63 | [-0.150, +0.077] |
| 6 | be_trail **off** | 388 | 49.7% | 7 | 187 | 194 | +22.35 | -14.10 | **-0.036** | -0.63 | [-0.150, +0.077] |
| 7 | pl trigger 0.75 / trail 0.25 | 481 | 48.9% | 2 | 325 | 154 | +47.07 | +2.66 | **+0.006** | +0.14 | [-0.073, +0.084] |
| 8 | pl trigger 1.5 / trail 0.5 | 418 | 25.6% | 4 | 277 | 137 | +24.24 | -14.43 | **-0.035** | -0.71 | [-0.129, +0.060] |
| 9 | pl trigger 1.0 / trail 0.25 | 459 | 38.1% | 2 | 305 | 152 | +40.09 | -2.48 | **-0.005** | -0.12 | [-0.090, +0.079] |
| 10 | pl trigger 1.5 / trail 0.75 | 406 | 25.1% | **14** | 261 | 131 | +35.64 | -1.87 | **-0.005** | -0.09 | [-0.109, +0.100] |

**Paired analysis** (same entry bar, baseline trades matched 1:1; isolates the
exit change from the entry-set change):

| config | matched | new | dropped | mean ΔnetR | SD | SE | t (paired) |
|---|---|---|---|---|---|---|---|
| rr_min 2.0 | 452 | 452 | 0 | -0.0015 | 0.117 | 0.0055 | **-0.27** |
| rr_min 2.5 | 451 | 451 | 1 | -0.0015 | 0.061 | 0.0029 | **-0.52** |
| rr_min 4.0 | 452 | 452 | 0 | +0.0015 | 0.048 | 0.0023 | **+0.68** |
| be_trail 0.75 | 415 | 420 | 37 | +0.0019 | 0.458 | 0.0225 | **+0.09** |
| be_trail 1.0 | 376 | 388 | 76 | -0.0050 | 0.612 | 0.0315 | **-0.16** |
| be_trail off | 376 | 388 | 76 | -0.0050 | 0.612 | 0.0315 | **-0.16** |
| pl 0.75 / 0.25 | 446 | 481 | 6 | +0.0087 | 0.367 | 0.0174 | **+0.50** |
| pl 1.5 / 0.5 | 411 | 418 | 41 | -0.0263 | 0.359 | 0.0177 | **-1.48** |
| pl 1.0 / 0.25 | 449 | 459 | 3 | +0.0179 | 0.229 | 0.0108 | **+1.66** |
| pl 1.5 / 0.75 | 400 | 406 | 52 | +0.0067 | 0.499 | 0.0250 | **+0.27** |

**Per-symbol `expNetR`** (baseline vs the two best cells; every cell clears the
20-trade inconclusive floor at the symbol level):

| symbol | baseline | rr 2.0 | rr 2.5 | rr 4.0 | be 0.75 | pl 0.75/0.25 | pl 1.0/0.25 |
|---|---|---|---|---|---|---|---|
| APEUSDT | -0.041 | -0.039 | -0.042 | -0.042 | -0.023 | +0.000 | -0.031 |
| BNBUSDT | -0.229 | -0.235 | -0.238 | -0.231 | -0.159 | -0.240 | -0.217 |
| BTCUSDT | -0.200 | -0.200 | -0.200 | -0.200 | -0.105 | -0.137 | -0.126 |
| LINKUSDT | +0.117 | +0.108 | +0.120 | +0.116 | +0.125 | +0.067 | +0.080 |
| OPUSDT | -0.028 | -0.029 | -0.021 | -0.028 | -0.051 | +0.052 | +0.021 |
| SOLUSDT | -0.004 | -0.006 | -0.010 | +0.009 | -0.069 | +0.095 | +0.040 |
| UNIUSDT | +0.161 | +0.173 | +0.161 | +0.161 | +0.139 | +0.134 | +0.155 |

---

## 5. Which knobs moved expectancy, and in which direction

**`rr_min` — moved nothing.** The hypothesis was "closer targets should raise
the TP-hit rate". The hypothesis is **confirmed for the mechanism and refuted
for the outcome**:

- TP hits move exactly as predicted: **21 → 8 → 4 → 1** for `rr_min`
  2.0 → 2.5 → 3.0 → 4.0.
- `expNetR` does not move at all: **-0.026, -0.026, -0.025, -0.023**. The whole
  2.0→4.0 range spans **0.003 expNetR**, and the paired t-statistics
  (-0.27 / -0.52 / +0.68) are indistinguishable from zero.

The reason is visible in the exit economics (§6): the BE/profit-lock trail
already harvests the winners, so moving the fixed target only changes *which*
trade ends the episode, not how much is banked. At `rr_min=2.0` the 17 extra
TP hits pay **+2.15R** each instead of the trail's ~+0.5R, but they also stop
the same trades from running further, and the two effects cancel to within
0.7R of gross R across 452 trades.

**`be_trail_trigger_r` — moved the wrong way.** Later BE (0.75) is nominally the
best of the BE family (-0.017 vs -0.025) but paired t = **+0.09**: it is noise.
Disabling BE entirely (cell 6) is clearly **worse** (-0.036). Note cells 5 and
6 are **bit-identical** — with `profit_lock_trigger_r = 1.0`, raising
`be_trail_trigger_r` to 1.0 makes the BE rule **dead code**, because the
harness's exit branch is an `elif` (`backend/backtest.py:708-720`): the
profit-lock branch claims every bar at ≥1.0R, so the BE band
`[be_trigger, pl_trigger)` is empty. Live does **not** have this structure
(`backend/core/position_manager.py:722-731` calls the stages sequentially), so
in production `be_trail_trigger_r = 1.0` is **not** equivalent to disabling BE.
This cell is a harness artifact, not a live recommendation.

**`profit_lock_trigger_r` / `profit_lock_trail_r` — moved something, but not
enough to matter.** The two "early-and-tight" variants (trigger 0.75 / trail
0.25 → **+0.006**; trigger 1.0 / trail 0.25 → **-0.005**) are the only cells
above the baseline. Their paired effects are the largest in the sweep
(+0.0087 and +0.0179 mean ΔnetR) but the paired t-statistics (**+0.50** and
**+1.66**) do not clear any reasonable threshold once 10 comparisons are
accounted for (Bonferroni t ≈ 3.0 at α = 0.05). Pushing the trigger later
(trigger 1.5) is clearly worse at trail 0.5 (-0.035, paired t = -1.48) but
neutral at trail 0.75 (-0.005) — the lever that matters is **trail distance**,
not trigger, and a wider trail only pays if the winners can actually reach the
further stop.

---

## 6. Why no exit configuration can work: the economics

Baseline exit-reason decomposition (452 closed trades, corrected run):

| exit reason | n | share | mean R | total R |
|---|---|---|---|---|
| `BE_STOP` (trailed stop) | 298 | 66% | **+0.564** | +168.0 |
| `SL_HIT` (full stop) | 150 | 33% | **-1.000** | -150.0 |
| `TP_HIT` (target) | **4** | **0.9%** | +3.200 | +12.8 |
| **gross** | 452 | | **+0.068 R/trade** | **+30.8** |
| fees (0.11% round trip) | 452 | | **-0.093 R/trade** | -42.0 |
| **net** | 452 | | **-0.025 R/trade** | **-11.19** |

Three facts follow, and they close the question:

1. **The target is nearly irrelevant.** 4 of 452 trades reach it. The strategy
   does not have an exit problem in the sense of "the target is never paid" —
   it has a *harvesting* problem, and the BE trail is already doing the
   harvesting (66% of trades, +168R gross).
2. **Gross edge is smaller than the round-trip cost.** +0.068 R/trade gross vs
   0.093 R/trade of fees. **Fees are 136% of the gross edge** (0.0929 ÷ 0.0681
   = 1.36×). No re-shaping of the exit changes the fee term — it is fixed by the
   stop distance, which none of these knobs touches (fee drag is
   **0.0923–0.0939 R/trade in every single cell**).
3. **Every trailing variant trades one term for the other.** Raising the BE
   trigger improves the average winner (BE_STOP mean +0.564R → +0.796R at 0.75,
   → +1.039R with BE off) but converts scratches into full -1R stops
   (SL_HIT 150 → 175 → 194), so gross R does not improve. Tightening the
   profit-lock trail does raise gross R (+30.8 → +40.1/+47.1) but also pulls
   the trail in so far that the winners are cut at ~+0.6R — and it does so on
   *more* trades, so it is partly a "trade less" effect rather than an edge.

**Conclusion: the binding constraint is the gross edge (+0.068 R/trade) against
a fixed cost (0.093 R/trade), and it is not reachable by exit geometry.** The
unreachable live exit rules (second-stage profit lock, runner trail, stale-trade
close) would only make live *more* aggressive at harvesting — they cannot raise
the gross edge.

---

## 7. Selection bias: the best cell is worse than noise

With 11 cells swept on one window, the best cell is expected to look good even
under the null. Under the null (each cell's expectancy drawn from a normal with
the observed per-cell SE, mean SE = 0.0479), a 200,000-draw Monte-Carlo gives:

| statistic | value |
|---|---|
| null median of the **max** cell | **+0.0745** |
| null 95th percentile of the max cell | +0.1294 |
| null 99th percentile of the max cell | +0.1586 |
| **observed best cell** | **+0.0060** |
| percentile of the observed best | **0.1%** |
| P(best cell ≥ observed \| pure noise) | **0.999** |

**The best swept configuration is *worse* than the median outcome of pure
noise across 11 cells.** The sweep found nothing — not a marginal edge, not
even a noise-level winner. The strongest single signal in the whole study is
`pl trigger 1.0 / trail 0.25` with a paired t of **+1.66**, which is exactly
what 10 independent comparisons produce by chance.

---

## 8. Caveats

- **One 45-day window.** 2026-09-21 → 2026-11-05 only. The broad-universe study
  found **11 of 25 symbols flip sign between adjacent 45-day windows**
  (`analysis/backtest-broad-universe-2026-11-05.md`), so a single window is a
  single regime sample and can neither confirm nor refute a small effect.
- **Selection bias.** 11 cells on one window. The §7 null quantifies it: the
  best cell here is a *noise* result. **Only a change that improves expectancy
  by a wide margin — not by the +0.03 that separates the best cell from the
  baseline — should be recommended, and nothing here clears that bar.**
- **The harness under-models live exits.** Live runs BE → profit-lock →
  second-stage profit lock → runner trail → stale-trade close
  (`backend/core/position_manager.py:722-734`); the harness models only BE and
  the first-stage profit lock, and models them as an `elif`
  (`backend/backtest.py:708-720`) where live runs them sequentially. Live also
  closes stale profitable trades (`stale_trade_hours`,
  `backend/core/position_manager.py:910-917`). So live is *more* aggressive at
  harvesting than this replay shows: absolute TP-hit counts here are optimistic
  and absolute net R is optimistic.
- **Unmodelled filters.** The historical funding filter
  (`funding_rate_filter_enabled`, default true) and the shared portfolio slot
  budget are not modelled; the replay is one position per symbol.
- **OHLC fills.** No queue position or partial fills; stop gaps fill at the
  adverse bar open; the entry candle can stop a trade but cannot earn a target
  or update a trailing extreme.
- **The float correction is a diagnostic patch, not a shipped fix.** It was
  applied in a throwaway script to make the cells comparable. The underlying
  defect (§3) is in `backend/indicators/three_candle.py:160-167` and is
  **still present in the live path** — it silently discards ~0.6% of setups at
  the deployed `rr_min=3.0`.

---

## 9. Verdict

**No exit configuration is meaningfully better than the -0.025 baseline.**

- `rr_min` is **flat** across 2.0→4.0 (span 0.003 expNetR) despite moving the
  TP-hit rate from 21 to 1. Closer targets raise the hit rate and change
  nothing else — the BE trail already harvests the winners.
- `be_trail_trigger_r` at 0.75 is nominally best (-0.017) but paired t = +0.09;
  disabling BE is worse (-0.036). `be_trail_trigger_r = 1.0` is a harness-only
  dead-code configuration, not a live option.
- The best profit-lock variant (`trigger 0.75 / trail 0.25`, **+0.006**) is
  inside the noise band, sits at the **0.1st percentile** of the null
  best-of-11 distribution, and rests on a subset of trades that differ in
  entry as well as exit.
- The mechanism is arithmetic, not statistical: **gross +0.068 R/trade versus
  a fixed 0.093 R/trade round-trip cost**. The exit can decide *where* a
  winner is banked; it cannot change *whether* there is an edge.

**Recommendation: change nothing.** Keep the deployed exit configuration
(`rr_min` 3.0, `be_trail_trigger_r` 0.5, `profit_lock_trigger_r` 1.0,
`profit_lock_trail_r` 0.5). The scanner stays halted pending a net-positive
*entry* configuration, consistent with the broad-universe conclusion that
"the symbol universe is not the binding constraint; maker entries / target
reachability are" (`analysis/backtest-broad-universe-2026-11-05.md`).

**Two follow-ups this study did produce, both independent of the sweep result:**

1. **Fix the float artifact** in `backend/indicators/three_candle.py:160-167`
   (and the mirrored check at `backend/backtest.py:605`) so `rr` is compared
   against `rr_min` with a tolerance, or `rr_ratio` is set to `rr_min`
   directly. It currently discards setups in production (~0.6% at the deployed
   `rr_min=3.0`) and it made a research cell look real. **This is an
   engineering defect, not a strategy change, and it should be fixed on its own
   merits — but it needs the user's approval and the test suite.**
2. **The exit model gap.** Live's second-stage profit lock, runner trail and
   stale-trade close are absent from the harness. Until they are modelled, no
   backtest can compare a *live* exit policy against another one, and
   `backend/AGENTS.md` already says so. This study's cells are still a valid
   ranking of the three knobs the harness does model.

---

## 10. Reproduction

| Artifact | Path |
|---|---|
| Raw sweep log (pre-correction) | `/tmp/sweep_shared_window.log` |
| Corrected sweep log | `/tmp/sweep_corrected2.log` |
| CLI cross-check log (4 runs) | `/tmp/sweep_cli_rrmin.log` |
| Sweep driver (raw) | `/tmp/fubot_exit_sweep.py` |
| Sweep driver (artifact-corrected) | `/tmp/fubot_exit_sweep_corrected.py` |
| Float-artifact probe | `/tmp/fubot_rr_float_probe.py` |
| Machine-readable results | `/tmp/sweep_results.json`, `/tmp/sweep_results_corrected.json` |

**Run counts**: 12 configs in the primary shared-window sweep (11 variants +
1 uncorrected baseline for validation), 4 CLI cross-check runs, and 1
instrumentation probe across 4 `rr_min` values — 20 config-runs total. The
primary sweep needed **one** candle fetch for all 12 configs; across the whole
study 8 candle fetches were made (the raw sweep, two correction iterations, the
probe, and the 4 CLI cross-check runs). This is well inside the "≤ ~12 runs"
budget in terms of data-fetch cost; the extra config-runs are pure CPU replay
over already-fetched candles.

**Reproduce the baseline** (should print 452 closed, expNetR -0.025):

```
PYTHONPATH=. python3.13 scripts/backtest.py \
  --symbols APEUSDT,BNBUSDT,BTCUSDT,LINKUSDT,OPUSDT,SOLUSDT,UNIUSDT \
  --days 45 --rr-min 3.0 --no-fvg --no-choch --no-obfvg \
  --strength MODERATE --neutral --no-volume \
  --min-risk-pct 0.005 --atr-stop-mult 1.5
```
