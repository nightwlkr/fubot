# NWERSIASF backtest — 2026-11-05

Standalone research harness `scripts/nwersiasf_backtest.py` (mirrors
`scripts/bbrsi_backtest.py`; reuses `backend/backtest_data.py` paginated
fetching and `backend/core/cost_model.py` LIVE cost model, 0.11% round trip
charged in R via `round_trip_in_r`). Source: geraked/tradingview
`strategies/NWERSIASF.pine` (MIT), `repaint=false` endpoint path only.

## Ported rules (verified against Pine source)

- NWE: h=8.0, mult=3.0, 500-bar lookback. `w(i)=exp(-(i²)/(h²·2))`,
  i=0..499 over closes; `center=Σ close[i]·w(i)/Σw(i)`;
  `mae=SMA(|close-center|,499)·3.0`; `upper/lower=center±mae`.
  First evaluable bar ≈ index 997 (500 closes + 499 MAE window).
- RSI(5) Wilder/RMA; ASF `avgTR=SMA(TR,14)`, m=0.5 →
  `longSL=signal_low−0.5·avgTR`, `shortSL=signal_high+0.5·avgTR`
  (SL anchored to the signal bar, fixed before the next-bar-open fill).
- LONG: `low[1]<lower[1] and close>open and close<lower+0.5·(upper−lower)
  and rsi[1]<30`; SHORT mirrors (`high[1]>upper[1]`, `close<open`,
  `close>upper−0.5·(upper−lower)`, `rsi[1]>70`).
- Entry at next bar open; `TP=fill±1.5·(fill−SL)` (bbrsi convention — the
  Pine computes TP off signal `close`, we use the actual fill). One
  position per symbol; signals while in position ignored (pyramiding=1).
- Exit sim identical to bbrsi: SL-before-TP within a bar, gap-through fills
  at the adverse open, 0.11% RT in R.

## Results — 45d window ending ~2026-11-05, 7 production symbols

### 15m — 479 trades

| symbol | trades | netR | expNetR |
|---|---|---|---|
| APEUSDT | 58 | -4.35 | -0.075 |
| BNBUSDT | 72 | -12.19 | -0.169 |
| BTCUSDT | 74 | -19.18 | -0.259 |
| LINKUSDT | 69 | -7.43 | -0.108 |
| OPUSDT | 60 | -12.88 | -0.215 |
| SOLUSDT | 77 | -21.00 | -0.273 |
| UNIUSDT | 69 | -19.29 | -0.280 |
| **TOTAL** | **479** | **-96.33** | **-0.201** |

win% 40.3, grossR **-2.99**, feeR 93.34 (≈0.195R/trade), expNetR **-0.201**.
Stop distance: min 0.127%, median 0.717%, p90 1.832%, max 4.336%.
Exits: sl 285 / tp 192 / sl_gap 1 / tp_gap 1.

### 1h — 9 trades (INCONCLUSIVE, n<20)

| symbol | trades | netR | expNetR |
|---|---|---|---|
| APEUSDT | 0 | 0 | — |
| BNBUSDT | 2 | +0.27 | +0.135 |
| BTCUSDT | 1 | -1.22 | -1.224 |
| LINKUSDT | 1 | +1.45 | +1.452 |
| OPUSDT | 0 | 0 | — |
| SOLUSDT | 3 | -0.75 | -0.249 |
| UNIUSDT | 2 | +2.81 | +1.403 |
| **TOTAL** | **9** | **+2.56** | **+0.284** |

win% 55.6, grossR +3.50, feeR 0.94 (≈0.104R/trade — wider stops). Stop
distance median 1.212%. Exits: tp 5 / sl 4. The 1h signal rate (~0.03
trades/symbol/day) is far too low to validate on a 45d window.

### 5m — 1440 trades (run for completeness; predicted fee-dominated)

| symbol | trades | netR | expNetR |
|---|---|---|---|
| APEUSDT | 187 | -8.89 | -0.048 |
| BNBUSDT | 239 | -140.56 | -0.588 |
| BTCUSDT | 232 | -168.59 | -0.727 |
| LINKUSDT | 208 | -45.12 | -0.217 |
| OPUSDT | 205 | -40.38 | -0.197 |
| SOLUSDT | 239 | -59.63 | -0.250 |
| UNIUSDT | 130 | +9.98 | +0.077 |
| **TOTAL** | **1440** | **-453.19** | **-0.315** |

win% 43.5, grossR **+125.00** (positive gross edge!), feeR **578.19**
(≈0.402R/trade), expNetR **-0.315**. Stop distance: min 0.021%, median
0.406%, p90 0.982%, max 10.555%. Exits: sl 814 / tp 626.

## Fee-drag analysis (kill criterion: 0.5·ATR stop vs 0.11% RT)

The strategy's stop is `0.5 × ATR(14)` — structurally tighter than the
deployed config's 1.5×ATR. Cost in R ≈ 0.0011 / stop_fraction:

- **5m**: median stop 0.406% → ~0.27R minimum cost; observed avg 0.402R
  because sub-0.1% stops (min 0.021%) cost >5R each. Gross edge +0.087R/trade
  is real but less than half the fee drag → net -0.315. **Fee-dominated, as
  predicted.**
- **15m**: median stop 0.717% → ~0.15R minimum; observed 0.195R/trade.
  Gross edge is **negative** (-0.006R/trade) — at 15m the strategy has no
  gross edge at all, so fees are not even the binding constraint.
- **1h**: median stop 1.212% → ~0.09R cost; observed 0.104R/trade. n=9 —
  cannot distinguish luck from edge.

## Verdict

**FAIL — not recommended for the live scanner.** No cell is both
net-positive and adequately sampled:

- 15m (n=479): net -96.33R, expNetR -0.201, negative gross — dead on arrival.
- 5m (n=1440): positive gross (+125R) but fees consume 578R — the tight
  0.5·ATR stop makes it structurally fee-bound at this cost model.
- 1h (n=9): +0.284 expNetR but far below the 20-trade evidence floor;
  would need a multi-window run (~6+ months) to reach significance, and the
  signal rate implies weeks of flat scanning.

The kill criterion is confirmed on both axes: the 0.5·ATR stop is too tight
for the 0.11% round-trip cost at 5m (and marginal at 15m), AND the 15m gross
edge is absent. Unlike BBRSI (candidate #1, also failed), NWERSIASF at 5m at
least shows positive gross expectancy — if execution costs ever dropped
(maker entries, fee tier) it would be worth revisiting; under current
account-calibrated costs it is rejected.

## Caveats

- TP uses actual fill price vs Pine's signal-close `inn`; direction of bias
  is small either way (entry is next-bar open, typically near signal close).
- OHLC cannot order SL/TP within a bar; SL checked first (conservative).
- Still-forming bars dropped by `backtest_data.align_window`; ~997-bar warmup
  eats ~10 days of the 45d 15m window per symbol.
- Single 45d window, no train/holdout split — but with 479/1440-trade cells
  the sign of the result is not window-fragile.
- Not committed; harness is a standalone script, no backend code touched.
