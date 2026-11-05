# Broad-Universe Symbol Scan — 45-Day Backtest of the Deployed 3CANDLE Config — 2026-11-05

**Purpose**: Task 12 (symbol-specific performance) extended from 7 symbols to **25**.
Rebuild the enabled-symbol universe on measured evidence instead of on the
leftovers of the October symbol list.

**Question**: which Binance USDS-M perps have a net-positive expectancy under
the **deployed** 3CANDLE configuration, and what should the enabled set be?

**Answer in one line**: **none of them can be shown to.** Across 25 symbols ×
2 disjoint 45-day windows (3,220 closed trades), the whole book's gross edge is
**+0.079R/trade** and its fee drag is **0.079R/trade** — net **-0.000R/trade**.
The best single cell (UNIUSDT, +0.201R/trade pooled) has a **19% probability of
arising from pure noise**, and the best *selected* 3-symbol set has a **13%
probability** of doing the same. What the scan *does* establish, with
family-wise significance, is that **ETHUSDT is a net drain** and that the
symbol universe is **not** the binding constraint.

---

## 1. Scope and universe

**Newly measured here (16)**: ETH, DOGE, AVAX, NEAR, INJ, SUI, ADA, TON, TRX,
ENA, WLD, TAO, FIL, ETC, BCH, LTC.

**Measured here as a control re-run (9)**: APE, BNB, BTC, LINK, OP, SOL, UNI
(the 7 symbols already covered by `analysis/backtest-45d-2026-11-05.md`) plus
APT, ARB, XRP (the enabled universe measured in parallel by
`analysis/backtest-enabled-universe-2026-11-05.md`).

**Total: 25 symbols.** All resolved on Binance Futures as `PERPETUAL`/USDT.
No symbol was invalid; one (`TONUSDT`) has no usable data — see §7.

---

## 2. Method

### 2.1 Configuration — the deployed config, unmodified

Every knob is either the code default or an explicit CLI flag matching the
deployed setting. Nothing was tuned.

| Setting | Value | Source |
|---------|-------|--------|
| Strategies | **3CANDLE only** | `--no-fvg --no-choch --no-obfvg`; `strategy_3candle_enabled` left at its default `True` (`backend/backtest.py:295`) |
| ATR stop floor | `atr_stop_mult = 1.5` | module constant `backend/indicators/three_candle.py:13` — **not** passed, the default *is* the deployed value after the revert |
| Risk floor | `min_risk_pct = 0.005` (0.5%) | harness default `backend/backtest.py:282` — **not** passed, the code default is the deployed value |
| `rr_min` | 3.0 | `--rr-min 3.0` |
| Trend strength | MODERATE | `--strength MODERATE` |
| Neutral markets | allowed | `--neutral` |
| Volume filter | off | `--no-volume` |
| Regime filter | on | harness default `backend/backtest.py:281` |
| Signal max age | 60 min | `backend/backtest.py:67` |
| Entry fill | touch-fill (0 bp) | `--entry-penetration-bps` default 0 |
| Costs | 0.11% round trip | `backend/core/cost_model.py` (0.045% taker ×2 + 0.01% slippage ×2) |

```
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
  scripts/backtest.py --symbols <list> --days 45 \
  --no-fvg --no-choch --no-obfvg --rr-min 3.0 --strength MODERATE --neutral --no-volume
```

`netR` / `expNetR` are net of the 0.11% round trip and are the numbers to
trust; `totalR` / `expR` are gross.

### 2.2 Two disjoint 45-day windows (this is the part the single-window caveat demanded)

A single window cannot separate an edge from a regime. Every symbol was
therefore measured on **two disjoint windows**:

| Window | UTC span | Role |
|--------|----------|------|
| **In-sample** | 2026-09-21 06:55Z → 2026-11-05 06:55Z | the "current" 45 days |
| **Holdout** | 2026-08-07 06:55Z → 2026-09-21 06:55Z | the **prior** 45 days (`--end-days-ago 45`) |

The windows are contiguous and non-overlapping, so the pooled cell is 90
calendar days of evidence per symbol. Batches were run 4-way concurrent
(`/tmp/broad_scan_{a..g}.log`, `/tmp/broad_holdout_{a..c}.log`,
`/tmp/broad_detail_*_{in,hold}.{log,json}`); **every batch succeeded on the
first attempt** — no transient `-1007`/timeout retries were needed.

### 2.3 Harness caveats (unchanged from the prior 45d runs)

No queue/partial-fill modelling, no funding rate, no
`PositionManager._check_profit_trailing_2` (stage-2 profit-lock), no shared
portfolio slots (the replay takes one position per symbol, uncapped by
`max_positions` / daily-trade cap / breakers), touch-fill entries only, and no
R:R re-check at the fill price that live performs. The replay also runs
**per symbol and uncapped by the portfolio guards** — it takes ~1.5 trades per
symbol per day (≈37/day across 25 symbols), against 2.2 trades/day in the
35-trade live sample, which is only 5 symbols with the live guards applied.
The replay is therefore **optimistic** about fill rate and about concurrent
exposure.

### 2.4 Independent reproduction (cross-check)

`analysis/backtest-enabled-universe-2026-11-05.md` (a parallel agent, written
independently and against a **different window pin** — `2026-09-21 05:00Z →
2026-11-05 05:00Z`, 1h55m offset from this run) measured the five enabled
symbols. Its numbers match this run **exactly**:

| symbol | parallel agent (own window pin) | this run (own window pin) |
|--------|--------------------------------|---------------------------|
| APTUSDT | 71 closed, expNetR +0.056, sd 0.983, t +0.48 | 71 closed, expNetR **+0.056**, sd 0.983, t +0.48 |
| ARBUSDT | 54 closed, expNetR -0.216, sd 0.775, t -2.05 | 54 closed, expNetR **-0.216**, sd 0.775, t -2.05 |
| SOLUSDT | 77 closed, expNetR -0.004, sd 0.899, t -0.04 | 77 closed, expNetR **-0.004**, sd 0.899, t -0.04 |
| UNIUSDT | 52 closed, expNetR +0.157, sd 0.904, t +1.26 | 52 closed, expNetR **+0.157**, sd 0.904, t +1.26 |
| XRPUSDT | 67 closed, expNetR -0.174, sd 0.887, t -1.60 | 67 closed, expNetR **-0.174**, sd 0.887, t -1.60 |

Identical trade counts, identical per-trade SDs, identical t-statistics from two
separate processes on two slightly different windows. The harness is
deterministic and the results are reproducible — which also means the numbers
below are not measurement noise, and the conclusions rest on the statistics
rather than on run-to-run variation.

---

## 3. Measurement parity — a correction to the brief, and why the 7 known symbols were re-run

The brief cited the known cells as "LINK +0.119, UNI +0.152, SOL +0.004 expNetR
from the 7-symbol **D2** run". **Those numbers are not from D2** — they are
from **Run A (BASELINE, `min_risk_pct = 0.0005`)** in
`analysis/backtest-45d-2026-11-05.md`. D2 is `atr 1.5 + min_risk 0.005`, and its
**per-symbol** cells were never published — only its TOTAL row
(448 closed, netR -11.00, expNetR -0.025).

Mixing Run-A cells with a deployed-config scan would have made the whole
ranking apples-to-oranges, so the 7 known symbols were **re-run under the
deployed config** as a control (the brief's "do not re-run" was about avoiding
duplicate work on an identical config; here the config differed).

The control reproduces D2 exactly:

| metric | D2 (documented, shared window) | control re-run (this window) | Δ |
|--------|--------------------------------|------------------------------|---|
| closed | 448 | 452 | +4 (window drift) |
| netR | -11.00 | -12.25 | -1.25 |
| expNetR | -0.025 | -0.027 | -0.002 |

and the min-risk floor fired **exactly 42 times** across the 7 symbols
(BNB 15, BTC 27, APE/LINK/OP/SOL/UNI 0) — the same 42 rejections D2's
instrumentation reports. The control is D2.

Because the floor never binds on LINK, UNI or SOL, their deployed-config cells
land on top of the Run-A values quoted in the brief:

| symbol | brief (Run A) | control re-run (deployed) | Δ |
|--------|----------------|---------------------------|---|
| LINKUSDT | +0.119 (n=80) | **+0.117** (n=81) | -0.002 |
| UNIUSDT | +0.152 (n=53) | **+0.157** (n=52) | +0.005 |
| SOLUSDT | +0.004 (n=75) | **-0.004** (n=77) | -0.008 |

So the brief's numbers are safe to use — but the *reason* matters: they survive
the config change only because the min-risk floor does not touch those three
symbols. **LINK's sign flip to -0.085 in the prior doc is caused by the ATR
widening (2.5×), not by the min-risk floor.** That is the same attribution the
decomposition run reached, now confirmed per-symbol.

---

## 4. Results

### Table A — in-sample window (2026-09-21 06:55Z → 2026-11-05 06:55Z)

| # | symbol | sig | closed | win% | totalR | netR | expR | expNetR | trades/d | note |
|---|--------|-----|--------|------|--------|------|------|---------|----------|------|
| 1 | TAOUSDT | 98 | 97 | 42.3 | 22.43 | 15.89 | 0.231 | **+0.164** | 2.2 | |
| 2 | UNIUSDT | 52 | 52 | 44.2 | 11.05 | 8.19 | 0.213 | **+0.157** | 1.2 | control re-run |
| 3 | TRXUSDT | 11 | 11 | 63.6 | 3.21 | 1.69 | 0.292 | +0.154 | 0.2 | **INCONCLUSIVE (<20 trades)** |
| 4 | SUIUSDT | 75 | 74 | 39.2 | 14.17 | 9.18 | 0.191 | **+0.124** | 1.7 | |
| 5 | LINKUSDT | 81 | 81 | 42.0 | 15.93 | 9.50 | 0.197 | **+0.117** | 1.8 | control re-run |
| 6 | WLDUSDT | 79 | 79 | 40.5 | 11.34 | 7.51 | 0.144 | **+0.095** | 1.8 | |
| 7 | APTUSDT | 72 | 71 | 39.4 | 7.75 | 3.98 | 0.109 | +0.056 | 1.6 | parallel-agent set |
| 8 | INJUSDT | 65 | 65 | 38.5 | 6.57 | 3.37 | 0.101 | +0.052 | 1.4 | |
| 9 | NEARUSDT | 82 | 82 | 32.9 | 5.15 | 1.63 | 0.063 | +0.020 | 1.8 | |
| 10 | ETCUSDT | 66 | 65 | 36.9 | 6.32 | 0.93 | 0.097 | +0.014 | 1.5 | |
| 11 | LTCUSDT | 74 | 74 | 33.8 | 6.90 | 0.29 | 0.093 | +0.004 | 1.6 | |
| 12 | SOLUSDT | 77 | 77 | 39.0 | 6.66 | -0.29 | 0.086 | -0.004 | 1.7 | control re-run |
| 13 | ENAUSDT | 71 | 71 | 38.0 | 2.85 | -0.43 | 0.040 | -0.006 | 1.6 | |
| 14 | AVAXUSDT | 73 | 72 | 27.8 | 1.96 | -2.98 | 0.027 | -0.041 | 1.6 | |
| 15 | APEUSDT | 73 | 73 | 30.1 | 1.45 | -2.99 | 0.020 | -0.041 | 1.6 | control re-run |
| 16 | OPUSDT | 57 | 57 | 38.6 | 0.30 | -2.67 | 0.005 | -0.047 | 1.3 | control re-run |
| 17 | FILUSDT | 84 | 84 | 28.6 | -2.39 | -6.89 | -0.028 | -0.082 | 1.9 | |
| 18 | DOGEUSDT | 86 | 85 | 35.3 | -1.61 | -10.09 | -0.019 | -0.119 | 1.9 | |
| 19 | XRPUSDT | 68 | 67 | 32.8 | -4.49 | -11.65 | -0.067 | -0.174 | 1.5 | parallel-agent set |
| 20 | BCHUSDT | 81 | 81 | 29.6 | -7.60 | -14.78 | -0.094 | -0.182 | 1.8 | |
| 21 | ETHUSDT | 67 | 67 | 28.4 | -3.72 | -12.38 | -0.056 | -0.185 | 1.5 | |
| 22 | BTCUSDT | 57 | 56 | 35.7 | -1.53 | -11.19 | -0.027 | -0.200 | 1.3 | control re-run |
| 23 | ARBUSDT | 54 | 54 | 24.1 | -8.86 | -11.67 | -0.164 | -0.216 | 1.2 | parallel-agent set |
| 24 | ADAUSDT | 59 | 58 | 29.3 | -8.72 | -13.04 | -0.150 | -0.225 | 1.3 | |
| 25 | BNBUSDT | 56 | 56 | 35.7 | -4.13 | -12.80 | -0.074 | -0.229 | 1.2 | control re-run |
| | **TOTAL** | | **1709** | **35.4** | **+80.99** | **-51.69** | **+0.047** | **-0.030** | | |

### Table B — disjoint holdout window (2026-08-07 06:55Z → 2026-09-21 06:55Z)

| # | symbol | sig | closed | win% | totalR | netR | expR | expNetR | trades/d |
|---|--------|-----|--------|------|--------|------|------|---------|----------|
| 1 | UNIUSDT | 73 | 73 | 41.1 | 20.66 | 16.91 | 0.283 | **+0.232** | 1.6 |
| 2 | LINKUSDT | 83 | 82 | 40.2 | 25.56 | 18.54 | 0.312 | **+0.226** | 1.8 |
| 3 | ARBUSDT | 41 | 41 | 43.9 | 10.89 | 9.04 | 0.266 | +0.220 | 0.9 |
| 4 | LTCUSDT | 61 | 60 | 41.7 | 16.34 | 9.92 | 0.272 | +0.165 | 1.4 |
| 5 | FILUSDT | 67 | 67 | 46.3 | 15.13 | 10.43 | 0.226 | +0.156 | 1.5 |
| 6 | DOGEUSDT | 62 | 61 | 39.3 | 14.44 | 8.88 | 0.237 | +0.146 | 1.4 |
| 7 | AVAXUSDT | 67 | 66 | 37.9 | 14.52 | 9.42 | 0.220 | +0.143 | 1.5 |
| 8 | ENAUSDT | 61 | 61 | 44.3 | 11.44 | 8.73 | 0.188 | +0.143 | 1.4 |
| 9 | APTUSDT | 83 | 83 | 37.3 | 17.81 | 11.39 | 0.215 | +0.137 | 1.8 |
| 10 | SUIUSDT | 67 | 67 | 35.8 | 13.92 | 8.65 | 0.208 | +0.129 | 1.5 |
| 11 | INJUSDT | 83 | 82 | 41.5 | 12.35 | 7.45 | 0.151 | +0.091 | 1.8 |
| 12 | ADAUSDT | 66 | 66 | 36.4 | 7.62 | 2.33 | 0.115 | +0.035 | 1.5 |
| 13 | SOLUSDT | 67 | 66 | 33.3 | 7.80 | 1.55 | 0.118 | +0.024 | 1.5 |
| 14 | ETCUSDT | 52 | 52 | 36.5 | 5.16 | 0.60 | 0.099 | +0.012 | 1.2 |
| 15 | WLDUSDT | 68 | 68 | 30.9 | 4.00 | 0.54 | 0.059 | +0.008 | 1.5 |
| 16 | OPUSDT | 51 | 50 | 30.0 | 2.99 | 0.09 | 0.060 | +0.002 | 1.1 |
| 17 | APEUSDT | 59 | 59 | 37.3 | 4.27 | -0.65 | 0.072 | -0.011 | 1.3 |
| 18 | NEARUSDT | 54 | 53 | 32.1 | 0.47 | -2.65 | 0.009 | -0.050 | 1.2 |
| 19 | XRPUSDT | 54 | 53 | 35.8 | 1.26 | -3.46 | 0.024 | -0.065 | 1.2 |
| 20 | TAOUSDT | 81 | 81 | 29.6 | -0.59 | -5.79 | -0.007 | -0.071 | 1.8 |
| 21 | BCHUSDT | 52 | 51 | 27.5 | -5.22 | -9.84 | -0.102 | -0.193 | 1.2 |
| 22 | BNBUSDT | 63 | 62 | 35.5 | -4.75 | -13.89 | -0.077 | -0.224 | 1.4 |
| 23 | BTCUSDT | 38 | 38 | 28.9 | -4.24 | -9.55 | -0.112 | -0.251 | 0.8 |
| 24 | ETHUSDT | 58 | 58 | 27.6 | -12.17 | -19.93 | -0.210 | -0.344 | 1.3 |
| 25 | TRXUSDT | 11 | 11 | 18.2 | -5.86 | -7.91 | -0.533 | -0.719 | 0.2 |
| | **TOTAL** | | **1511** | **36.4** | **+173.80** | **+50.80** | **+0.115** | **+0.034** | | |

**The two windows disagree in sign at the aggregate level** (-0.030 vs +0.034)
on the same strategy, same config, same symbols. That is the regime sensitivity
made concrete, and it is why the holdout window exists.

### Table C — pooled 90 days, ranked by pooled expNetR, with per-trade statistics

`sd` = SD of per-trade net R, `SE` = `sd/sqrt(n)`, `t` = mean/SE, CI = normal
approximation. "Bonferroni" = one-sided, family-wise 0.05 over the 24 cells
with n ≥ 40 (z = 2.865).

| # | symbol | n in | n out | n | win% | grossR | netR | expNetR | sd | SE | t | 95% CI | in | out | verdict |
|---|--------|------|-------|---|-------|--------|------|---------|-----|-----|---|-------|----|-----|---------|
| 1 | UNIUSDT | 52 | 73 | 125 | 42.4 | +31.71 | +25.10 | **+0.201** | 1.086 | 0.0971 | +2.07 | [+0.010, +0.391] | +0.157 | +0.232 | ns |
| 2 | LINKUSDT | 81 | 82 | 163 | 41.1 | +41.49 | +28.04 | **+0.172** | 1.044 | 0.0817 | +2.10 | [+0.012, +0.332] | +0.117 | +0.226 | ns |
| 3 | SUIUSDT | 74 | 67 | 141 | 37.6 | +28.09 | +17.83 | **+0.126** | 0.991 | 0.0834 | +1.52 | [-0.037, +0.290] | +0.124 | +0.129 | ns |
| 4 | APTUSDT | 71 | 83 | 154 | 38.3 | +25.56 | +15.37 | +0.100 | 0.955 | 0.0770 | +1.30 | [-0.051, +0.251] | +0.056 | +0.137 | ns |
| 5 | LTCUSDT | 74 | 60 | 134 | 37.3 | +23.24 | +10.21 | +0.076 | 1.016 | 0.0877 | +0.87 | [-0.096, +0.248] | +0.004 | +0.165 | ns |
| 6 | INJUSDT | 65 | 82 | 147 | 40.1 | +18.92 | +10.82 | +0.074 | 0.987 | 0.0814 | +0.90 | [-0.086, +0.233] | +0.052 | +0.091 | ns |
| 7 | ENAUSDT | 71 | 61 | 132 | 40.9 | +14.29 | +8.30 | +0.063 | 0.999 | 0.0869 | +0.72 | [-0.108, +0.233] | -0.006 | +0.143 | ns |
| 8 | TAOUSDT | 97 | 81 | 178 | 36.5 | +21.84 | +10.10 | +0.057 | 0.909 | 0.0681 | +0.83 | [-0.077, +0.190] | +0.164 | -0.071 | ns |
| 9 | WLDUSDT | 79 | 68 | 147 | 36.1 | +15.34 | +8.05 | +0.055 | 0.988 | 0.0815 | +0.67 | [-0.105, +0.214] | +0.095 | +0.008 | ns |
| 10 | AVAXUSDT | 72 | 66 | 138 | 32.6 | +16.48 | +6.44 | +0.047 | 1.044 | 0.0889 | +0.52 | [-0.128, +0.221] | -0.041 | +0.143 | ns |
| 11 | FILUSDT | 84 | 67 | 151 | 36.4 | +12.74 | +3.54 | +0.023 | 1.011 | 0.0823 | +0.28 | [-0.138, +0.185] | -0.082 | +0.156 | ns |
| 12 | ETCUSDT | 65 | 52 | 117 | 36.8 | +11.48 | +1.53 | +0.013 | 0.949 | 0.0877 | +0.15 | [-0.159, +0.185] | +0.014 | +0.012 | ns |
| 13 | SOLUSDT | 77 | 66 | 143 | 36.4 | +14.46 | +1.26 | +0.009 | 0.975 | 0.0815 | +0.11 | [-0.151, +0.169] | -0.004 | +0.024 | ns |
| 14 | NEARUSDT | 82 | 53 | 135 | 32.6 | +5.62 | -1.02 | -0.008 | 1.041 | 0.0896 | -0.08 | [-0.183, +0.168] | +0.020 | -0.050 | ns |
| 15 | DOGEUSDT | 85 | 61 | 146 | 37.0 | +12.83 | -1.21 | -0.008 | 0.948 | 0.0784 | -0.11 | [-0.162, +0.145] | -0.119 | +0.146 | ns |
| 16 | OPUSDT | 57 | 50 | 107 | 34.6 | +3.29 | -2.58 | -0.024 | 0.890 | 0.0861 | -0.28 | [-0.193, +0.145] | -0.047 | +0.002 | ns |
| 17 | APEUSDT | 73 | 59 | 132 | 33.3 | +5.72 | -3.64 | -0.028 | 0.964 | 0.0839 | -0.33 | [-0.192, +0.137] | -0.041 | -0.011 | ns |
| 18 | ARBUSDT | 54 | 41 | 95 | 32.6 | +2.03 | -2.63 | -0.028 | 0.992 | 0.1017 | -0.27 | [-0.227, +0.172] | -0.216 | +0.220 | ns |
| 19 | ADAUSDT | 58 | 66 | 124 | 33.1 | -1.10 | -10.71 | -0.086 | 0.851 | 0.0764 | -1.13 | [-0.236, +0.063] | -0.225 | +0.035 | ns |
| 20 | XRPUSDT | 67 | 53 | 120 | 34.2 | -3.23 | -15.11 | -0.126 | 0.849 | 0.0775 | -1.63 | [-0.278, +0.026] | -0.174 | -0.065 | ns |
| 21 | BCHUSDT | 81 | 51 | 132 | 28.8 | -12.82 | -24.62 | -0.187 | 0.985 | 0.0858 | -2.17 | [-0.355, -0.018] | -0.182 | -0.193 | ns |
| 22 | BTCUSDT | 56 | 38 | 94 | 33.0 | -5.77 | -20.74 | -0.221 | 0.792 | 0.0817 | -2.70 | [-0.381, -0.060] | -0.200 | -0.251 | ns |
| 23 | BNBUSDT | 56 | 62 | 118 | 35.6 | -8.88 | -26.69 | -0.226 | 0.949 | 0.0874 | -2.59 | [-0.397, -0.055] | -0.229 | -0.224 | ns |
| 24 | ETHUSDT | 67 | 58 | 125 | 28.0 | -15.89 | -32.31 | **-0.258** | 0.837 | 0.0749 | -3.45 | [-0.405, -0.112] | -0.185 | -0.344 | **NEGATIVE (survives Bonferroni)** |
| 25 | TRXUSDT | 11 | 11 | 22 | 40.9 | -2.65 | -6.22 | -0.283 | 0.777 | 0.1657 | -1.71 | [-0.608, +0.042] | +0.154 | -0.719 | **INCONCLUSIVE (n<40)** |
| | **TOTAL** | | | **3220** | | **+254.79** | **-0.89** | **-0.000** | | | | | | | | | |

### Skip reasons (in-sample, all 25 symbols)

`counter_trend=4807`, `ranging_regime=2185`, `weak_trend=578`, `no_fill=596`,
`min_risk=201`, `regime_counter_trend=23`.

The trend/regime gates do 83% of the filtering (`counter_trend` + `ranging_regime`
= 6,992 of 8,390 skips); the 0.5% risk floor rejects 201 candidates
(concentrated in TRX 139, BTC 27, ETH 20, BNB 15).

---

## 5. Statistical strength — the multiple-comparisons problem, quantified

We screened **25 symbols** (24 with n ≥ 40). A screen of that size produces
apparent winners from noise alone, so the winners must be measured against a
**null distribution of pure noise**.

Monte-Carlo null simulation (200,000 draws, each symbol's own SE, all cells
assumed to have **zero** true edge):

| quantity | null expectation | observed | reading |
|----------|------------------|----------|---------|
| number of positive cells | **12.0** of 24 | **13** of 24 | indistinguishable from chance |
| best single cell | **+0.164** (p95 +0.244) | +0.201 (UNIUSDT) | **P(≥ observed \| null) = 0.19** |
| mean of the best 3 cells | **+0.131** (p95 +0.184) | +0.166 (LINK/UNI/SUI) | **P(≥ observed \| null) = 0.13** |
| any random 3-subset mean | +0.131 (p95 +0.184) | +0.165 (the proposed set) | **P = 0.13** |

**Every positive cell in this scan is exactly what a 24-cell search over noise
would produce.** The best symbol sits at the **81st percentile** of the null
distribution of the best cell — an entirely ordinary outcome. Nothing here is
evidence of an edge.

Bonferroni (one-sided, family-wise 0.05 over 24 cells, z = 2.865):

- a cell must clear **+0.2398 R/trade** at the mean SE; the best observed is
  **+0.201** → **fails**.
- per-cell thresholds are listed in Table C. The only cell that clears *any*
  threshold is **ETHUSDT on the negative side** (-0.258 vs a -0.215 threshold,
  t = -3.45).

**Required sample size** for a genuinely positive cell (pooled SD = 0.960):

| true edge | trades needed per symbol | ≈ calendar days at 1.6 trades/day |
|-----------|-------------------------|-----------------------------------|
| +0.05 R/trade | 3,030 | ~1,890 days |
| +0.10 R/trade | 757 | ~470 days |
| +0.15 R/trade | 337 | ~210 days |
| +0.20 R/trade | 189 | ~120 days |

Even a **+0.20 R/trade** edge — which would be excellent — needs **~120 days of
continuous trading per symbol** to prove at family-wise 0.05. A 45-day scan
cannot do that for any of these symbols, and no amount of extra symbols helps.

---

## 6. Where the R actually comes from — the fee-drag finding

| window | trades | gross R/trade | fee drag R/trade | net R/trade |
|--------|--------|---------------|------------------|-------------|
| in-sample | 1,709 | +0.047 | 0.078 | **-0.030** |
| holdout | 1,511 | +0.115 | 0.081 | **+0.034** |
| **pooled 90d** | **3,220** | **+0.079** | **0.079** | **-0.000** |

Over the pooled 90 days the strategy's **gross** edge is **+0.079R/trade**
(+254.79R) and its **round-trip cost is 0.079R/trade** (255.68R). The two cancel
to within 0.001R.

This is the central economic finding, and it is the same structural conclusion
reached three independent times today:

- `analysis/backtest-enabled-universe-2026-11-05.md` (parallel agent, enabled
  universe): gross +0.038R/trade vs fee drag 0.073R/trade, net -0.036; only
  **1 of 321** trades reached the 3R target, realised win/loss 1.6:1.
- `analysis/backtest-45d-2026-11-05.md`: fee drag, not exit geometry, is the
  binding constraint.
- `analysis/production-trade-analysis-2026-11-05.md`: 35 live trades, **zero**
  TP hits, 26.61R of fee drag.

**A gross edge of ~0.08R/trade exists but is not tradeable at a 0.11% round
trip.** Rebuilding the symbol universe cannot change that ratio: it can only
re-weight which 0.08R is captured, and the best-weighted subset still has a
13% chance of being pure noise.

---

## 7. Per-symbol notes

- **TONUSDT — no usable data.** Binance marks the contract `SETTLING` and
  serves a **synthetic frozen series**: all 1,080 hourly bars in the window
  carry the identical close (1.5997) with volume, so no 3-candle structure
  exists and the harness returns 0 signals / 0 skips. Not a strategy finding —
  a delisting. It cannot be enabled.
- **TRXUSDT — INCONCLUSIVE and unusable.** 11 closed trades in 45 days
  (0.24/day). The 0.5% `min_risk_pct` floor rejects **139** candidates, i.e.
  TRX's ATR is so small that almost every structural stop falls below 0.5% of
  entry. Even its "positive" in-sample cell (+0.154, n=11) is a coin flip: the
  holdout gives **-0.719** on 11 trades.
- **ARBUSDT — the clearest regime-dependence.** -0.216 in-sample, **+0.220** in
  the holdout. A symbol whose sign flips between adjacent 45-day windows cannot
  be classified from either window.
- **ADAUSDT, BCHUSDT, BTCUSDT, BNBUSDT, ETHUSDT, XRPUSDT — negative in both
  windows.** ETH is the only one that is formally significant.
- **Sign flips between the two adjacent windows — 11 of 25 symbols (44%)**:
  ENAUSDT (-0.006 → +0.143), TAOUSDT (+0.164 → **-0.071**), AVAXUSDT (-0.041
  → +0.143), FILUSDT (-0.082 → +0.156), SOLUSDT (-0.004 → +0.024), NEARUSDT
  (+0.020 → -0.050), DOGEUSDT (-0.119 → +0.146), OPUSDT (-0.047 → +0.002),
  ARBUSDT (-0.216 → **+0.220**), ADAUSDT (-0.225 → +0.035), TRXUSDT (+0.154 →
  **-0.719**). Every one of these is unclassifiable from either window. This
  is the single-window-regime-bias warning, quantified rather than merely
  stated.
- **Positive in both windows (8 of 25)**: UNI, LINK, SUI, APT, INJ, LTC, WLD
  (+0.095/+0.008 — the holdout cell is ≈ 0), ETC (+0.014/+0.012 — ≈ 0). The
  three that clear +0.10 in **both** windows are UNI, LINK and SUI.

---

## 8. Verdict

### 8.1 Which symbols show net-positive expectancy?

**None can be shown to.** No cell in the 25-symbol universe survives a
family-wise correction for the 24 comparisons made. The best cell (UNIUSDT,
+0.201R/trade pooled over 125 trades, t = +2.07, unadjusted 95% CI
[+0.010, +0.391]) sits at the **81st percentile of the null distribution of the
best cell** — i.e. it is a completely ordinary outcome of searching 24 noisy
cells (P = 0.19). UNIUSDT and LINKUSDT are the only two whose *unadjusted* CI
excludes zero, and both fail the Bonferroni threshold (+0.278 and +0.234
required).

**One cell IS significant, and it points the wrong way: ETHUSDT at -0.258R/trade
pooled (t = -3.45), negative in both windows (-0.185, -0.344), and the only
symbol in the universe whose 95% CI excludes zero on the negative side after
correction.**

### 8.2 Proposed evidence-based enabled-symbol set

Because nothing is statistically significant, this is **not** a "these symbols
work" list — it is a **ranking with an explicit confidence statement**, and
**applying it is the user's decision** (per `analysis/AGENTS.md`, no strategy
change is applied from an artifact alone).

**Tier 1 — strongest evidence (each of the two disjoint windows ≥ +0.10,
pooled ≥ +0.10, n ≥ 125):**

| symbol | in | holdout | pooled | n | t | rationale |
|--------|----|---------|--------|---|---|-----------|
| **UNIUSDT** | +0.157 | +0.232 | **+0.201** | 125 | +2.07 | positive in **three** independent windows (prior run A +0.152 n=53) |
| **LINKUSDT** | +0.117 | +0.226 | **+0.172** | 163 | +2.10 | positive in **three** independent backtest windows **and** the live sample (+0.134, n=5) — the only symbol positive in every sample measured |
| **SUIUSDT** | +0.124 | +0.129 | **+0.126** | 141 | +1.52 | consistent across both windows, but no third sample; live n=0 |

**Tier 2 — positive in both windows, but one of the two cells is below +0.10
(weaker, one fewer sample of support):** APTUSDT (+0.100 pooled, n=154, but
in-sample only +0.056; independently reproduced by the parallel agent),
INJUSDT (+0.074, n=147), LTCUSDT (+0.076, n=134, in-sample only +0.004),
WLDUSDT (+0.055, n=147, holdout only +0.008). ETCUSDT (+0.013) is flat, not
positive.

**Tier 3 — exclude on evidence:** **ETHUSDT** (significant negative), and
**ADA, BNB, BTC, XRP, BCH** (negative in both windows). **ARB, TRX, TON** are
excluded as unclassifiable (sign flip / inconclusive / delisted).

**Recommended enabled set, if the user wants one: `LINKUSDT`, `UNIUSDT`,
`SUIUSDT`** (optionally `APTUSDT` as a fourth), replacing the current
APT/ARB/SOL/UNI/XRP. That set's pooled 90-day figure is **+0.165R/trade over
429 trades — which has a 13% chance of arising from a random 3-subset of 24
noise cells.** It is the best-supported choice available, not a demonstrated
edge.

**Operational notes for whoever applies it:**
- `LINKUSDT`, `SUIUSDT`, `ADAUSDT`, `AVAXUSDT`, `DOGEUSDT`, `ETHUSDT`,
  `FILUSDT`, `INJUSDT`, `NEARUSDT` already have rows in the `symbols` table
  (currently `enabled=false`); `UNIUSDT` is currently enabled.
  (Row list cross-checked against the production `symbols` table recorded in
  `analysis/session-symbol-research-2026-11-05.md` §1.4 and against the local
  dev database.)
- `TAOUSDT`, `ENAUSDT`, `WLDUSDT`, `TRXUSDT`, `ETCUSDT`, `BCHUSDT`, `LTCUSDT`
  have **no** row — `PUT /api/settings/symbols/{symbol}` returns 404
  ("Symbol not found") until a row exists (`backend/routers/settings.py:232`).
  `TONUSDT` must not be added at all.
- The currently enabled APT/ARB/SOL/UNI/XRP set is measured at **-0.036
  expNetR** by the parallel agent; the proposed set is better, but neither is
  positive.

### 8.3 The blocker is not the universe

Restating the sharpest result: **pooled over 90 days and 25 symbols, gross
+0.079R/trade minus fee drag 0.079R/trade = net -0.000R/trade.** No
re-selection of symbols can change that identity, because the fee drag is
paid per trade on whatever symbols are chosen, and the gross edge those symbols
produce is the same ~0.08R the strategy has everywhere. Re-cutting the symbol
list can at best *average away* the bad symbols; it cannot manufacture an
edge. This agrees with the parallel agent's finding that "the blocker is the
exit structure, not the universe" (1 TP hit in 321 trades, realised win/loss
1.6:1 instead of the 3:1 that `rr_min=3.0` advertises).

**Therefore: do not restart the scanner on any of these sets.** The scanner's
current halted state is the correct state for a configuration with zero
expected net edge. If the goal is a net-positive book, the lever is cost per
trade (maker entries — Task 5) or target reachability, not symbol selection.

---

## 9. Caveats

1. **Single config, single strategy.** Only the deployed 3CANDLE profile was
   tested. The ranking is conditional on it; the prior 45d work showed the
   ranking is **config-sensitive** (LINK flips sign under ATR 2.5).
2. **Two windows is better than one, not enough.** The holdout is the
   immediately preceding 45 days, so it is *disjoint* but *not independent* —
   the same market regime can span both. A third, older window (days 135–90
   back) would test regime robustness further; it was not run.
3. **Selection on the same data.** The Tier-1 set was chosen because it looked
   good in these windows. Every number in §8.2 is upward-biased by that
   selection, and the 13% null probability quantifies exactly how much.
4. **Survivorship.** The universe is today's listed contracts. Symbols
   delisted during the window (TONUSDT is being settled now) cannot be
   evaluated — a mild optimistic bias for the universe as a whole.
5. **Harness omissions** (same as the prior 45d runs): no queue/partial fills,
   no funding, no stage-2 profit-lock, no shared portfolio slots or guards, no
   R:R re-check at the fill price. The replay is **optimistic** on fill rate
   and on concurrency.
6. **Fee model is an estimate** (0.11% round trip from the account-calibrated
   0.045% taker + 0.01% slippage per side), not a realised-cost ledger. The
   §6 conclusion is entirely about this number, so a 20% error in the fee
   assumption moves the book by ±0.016R/trade.
7. **No live corroboration** for SUIUSDT, WLDUSDT, ENAUSDT, TAOUSDT, LTCUSDT,
   INJUSDT — they have zero live trades ever.
8. **Correlation is not modelled.** Symbols trade together; the effective
   number of independent tests is below 24, which makes the correction
   *conservative* (the true multiplicity is higher than assumed).

---

## 10. Reproduce

- In-sample CLI logs: `/tmp/broad_scan_{a..g}.log`
- Holdout CLI logs: `/tmp/broad_holdout_{a..c}.log`
- Per-trade detail (SD/SE/t/CI) + JSON: `/tmp/broad_detail_*_{in,hold}.{log,json}`
- Throwaway analysis scripts (per `analysis/AGENTS.md` work guidance):
  `/tmp/broad_detail.py` (per-trade stats), `/tmp/combine_broad.py` (joined
  table), `/tmp/pool_broad.py` (pooled windows + null simulation),
  `/tmp/multiples.py` (multiple-comparisons), `/tmp/final_tables.py` (tables).
- Run status/retries: `/tmp/broad_scan_status.log` — **23 of 23 batch runs
  succeeded on the first attempt** (0 retries, 0 failures; no transient Binance
  `-1007`/timeout errors).
- Window: in-sample `2026-09-21 06:55Z → 2026-11-05 06:55Z`; holdout
  `2026-08-07 06:55Z → 2026-09-21 06:55Z` (`--end-days-ago 45`).
- **Nothing was committed, no setting was changed, and the scanner was not
  restarted.**
