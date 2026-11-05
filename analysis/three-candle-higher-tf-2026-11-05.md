# 3CANDLE at higher timeframes — 15m and 1h detection (2026-11-05)

Research-only experiment: does moving 3CANDLE detection/execution from 5m to
15m or 1h fix the fee-drag problem that dominates the deployed 5m strategy?

**Harness:** `scripts/three_candle_tf_backtest.py` (new, research-only — no
live-path changes). It reuses the production detector
(`detect_three_candles` / `evaluate_three_candle_entry`), the `Backtester`
filter chain (`_passes_filters`), live fill semantics (`fill_at_midpoint`,
`rr_at_fill`, 60-minute signal expiry), and `_resolve_exit` (stop-before-
target, gap at adverse open, entry candle cannot earn target or update
trailing). Scan clock = close of each detect-interval bar; pattern = last 3
closed detect bars. Exits simulate on the SAME interval's bars.

**Context choice:** trend/regime/ATR move up one timeframe, mirroring live's
5m→1h ratio: **15m detection uses 1h context** (the same context live
already uses), **1h detection uses 4h context** (next standard interval;
keeps SMA200 reachable inside 45d). ATR for the stop floor is therefore the
context interval's ATR(14) — identical construction to live.

**Config:** deployed mirror — 3CANDLE only, `atr_stop_mult=1.5`,
`min_risk_pct=0.005`, `rr_min=3.0`, MODERATE strength, regime filter on,
neutral allowed, volume off, BE + profit-lock trailing. Cost model: 0.11%
round trip (`backend/core/cost_model.py`). Window: 45 days ending at the
last closed bar; effective trading days exclude the 60-bar context warmup
(≈42.5d for 15m/1h-ctx, ≈35d for 1h/4h-ctx). Universe: the 7-symbol
research set plus APT/ARB/SUI/XRP (11 total). One retry on transient
Binance errors; no fetch failures.

## Results — pooled

| interval | ctx | closed | win% | grossR | netR | expR | expNetR | fee drag (R/trade) |
|----------|-----|--------|------|--------|------|------|---------|--------------------|
| 5m (baseline, 7 sym, `backtest-45d` run D2) | 1h | 448 | 38.2% | +30.75 | -11.00 | +0.069 | **-0.025** | ~0.079 (median) |
| **15m** (11 sym) | 1h | 330 | 39.1% | +33.93 | +4.13 | +0.103 | **+0.013** | 0.090 |
| **15m** (7 sym subset) | 1h | 211 | — | +23.00 | +2.69 | +0.109 | **+0.013** | 0.096 |
| **1h** (11 sym) | 4h | 36 | 41.7% | +3.82 | +2.07 | +0.106 | **+0.058** | 0.049 |
| **1h** (7 sym subset) | 4h | 20 | — | +2.55 | +1.41 | +0.127 | **+0.070** | 0.057 |

Cells with <20 closed trades are inconclusive — **every 1h per-symbol cell
is inconclusive** (1–5 trades each; ~0.09 trades/day/symbol).

## 15m per-symbol (45d, deployed config)

| symbol | closed | win% | grossR | netR | expNetR | med stop % | fee drag R | tr/d |
|--------|--------|------|--------|------|---------|-----------|------------|------|
| SUIUSDT | 33 | 48.5% | +13.27 | +10.99 | **+0.333** | 1.47% | 0.069 | 0.78 |
| LINKUSDT | 46 | 45.7% | +13.82 | +9.88 | **+0.215** | 1.30% | 0.086 | 1.08 |
| BNBUSDT | 22 | 45.5% | +6.86 | +2.96 | +0.134 | 0.58% | 0.177 | 0.52 |
| OPUSDT | 23 | 52.2% | +4.34 | +3.08 | +0.134 | 2.25% | 0.055 | 0.54 |
| UNIUSDT | 29 | 48.3% | +2.72 | +1.13 | +0.039 | 1.99% | 0.055 | 0.68 |
| APEUSDT | 35 | 31.4% | +2.86 | +0.47 | +0.013 | 1.56% | 0.068 | 0.82 |
| APTUSDT | 30 | 33.3% | -1.51 | -3.28 | -0.109 | 1.99% | 0.059 | 0.71 |
| ARBUSDT | 16 | 31.2% | -0.55 | -1.52 | -0.095 | 1.87% | 0.061 | 0.38 ⚠ |
| XRPUSDT | 40 | 35.0% | -0.29 | -4.76 | -0.119 | 0.99% | 0.112 | 0.94 |
| SOLUSDT | 36 | 27.8% | -3.43 | -7.21 | -0.200 | 1.03% | 0.105 | 0.85 |
| BTCUSDT | 20 | 30.0% | -4.17 | -7.61 | -0.380 | 0.64% | 0.172 | 0.47 |

⚠ ARBUSDT n=16 <20 → inconclusive. BTCUSDT n=20 is at the threshold —
treat as borderline.

## 1h per-symbol (45d, deployed config) — ALL CELLS INCONCLUSIVE (n ≤ 5)

| symbol | closed | win% | expNetR | med stop % | fee drag R |
|--------|--------|------|---------|-----------|------------|
| APEUSDT | 4 | 75.0% | +0.552 | 3.80% | 0.031 |
| BNBUSDT | 5 | 80.0% | +0.445 | 1.39% | 0.079 |
| XRPUSDT | 5 | 60.0% | +0.356 | 1.69% | 0.061 |
| LINKUSDT | 3 | 33.3% | +0.013 | 2.16% | 0.046 |
| APTUSDT | 4 | 25.0% | -0.122 | 5.16% | 0.022 |
| ARBUSDT | 4 | 0.0% | -0.026 | 4.54% | 0.026 |
| SUIUSDT | 3 | 33.3% | -0.174 | 3.20% | 0.037 |
| BTCUSDT | 3 | 33.3% | -0.241 | 1.09% | 0.100 |
| SOLUSDT | 2 | 50.0% | -0.398 | 2.45% | 0.047 |
| UNIUSDT | 2 | 0.0% | -0.757 | 3.78% | 0.029 |
| OPUSDT | 1 | 0.0% | -0.032 | 3.45% | 0.032 |

Dominant skip reasons at 1h: `no_fill` (the 60-minute signal expiry gives the
midpoint LIMIT roughly one bar of life) and `counter_trend`/`ranging_regime`
from the 4h context.

## Fee-drag hypothesis — verdict

**Partially confirmed, and not for the reason hypothesized.**

- The hypothesis was: wider structural stops at higher TFs → same 0.11%
  cost is a smaller fraction of R → fee drag in R falls.
- **At 15m the mechanism did NOT operate.** Median stop distance stayed
  ≈1.3–2.3% of entry (vs 1.37% at 5m) and pooled fee drag was 0.090–0.096
  R/trade — if anything slightly WORSE than the 5m baseline's ~0.079.
  Reason: the ATR stop floor is `1.5 × ATR(14)` of the CONTEXT interval,
  and both the 5m and 15m runs use 1h context — so the stop floor is
  identical by construction. The pattern geometry (c1 range / c2 wick)
  widens less than expected because entries still rest at the c1-midpoint
  zone and the ATR floor dominates.
- **At 1h the mechanism DID operate** — median stops 1.4–5.2%, pooled fee
  drag 0.049 R/trade (~40% below the 5m baseline) — exactly the predicted
  direction. But 36 trades across 11 symbols in ~35 effective days is far
  too few to conclude anything about expectancy; every cell is
  inconclusive.
- **What actually improved at 15m was gross expectancy**, not cost: expR
  rose +0.069 → +0.103/+0.109, lifting net from -0.025 to ≈+0.013.
  Whether that survives a disjoint holdout window is untested — this is a
  single 45d window on the same period as prior research.

## Verdict

- **15m**: modest improvement over the 5m baseline (expNetR ≈ +0.013 vs
  -0.025, ~0.7–0.8 trades/day/symbol vs ~1.4). Still essentially
  break-even, with the same symbol split (LINK/SUI/BNB/OP positive;
  BTC/SOL/XRP negative). Not a demonstrated edge — but the direction is
  the best the strategy has shown. Candidate for a disjoint-window
  holdout (`--end-days-ago 45`) and/or paper-mode evaluation before any
  live consideration; per-symbol enablement would be the user's call.
- **1h**: fee drag fell as hypothesized but trade frequency collapsed
  (~0.09/day/symbol) — statistically inconclusive. A longer window (90d+)
  or relaxed `signal_max_age_minutes` would be needed; neither was tested
  here because both change live-mirrored semantics.
- **No live config change is supported by this evidence.** The deployed
  5m scanner stays as-is; weekly guard halt unchanged.

## Caveats

- Same harness omissions as `backtest-45d-2026-11-05.md`: OHLC-based fills
  (no queue/partial fills), no portfolio slot sharing beyond one position
  per symbol, exit policy models BE + first-stage profit-lock only.
- 15m `no_fill` counts are material (10–36 per symbol): the 60-minute
  expiry is ~4 bars at 15m vs 12 at 5m, so fewer midpoint touches convert.
- Single window; the 15m gross-edge improvement needs out-of-sample
  confirmation before it means anything.
- BTCUSDT 15m cell sits exactly at the n=20 boundary.
- The `rr_min` tautology defect recorded in `exit-param-sweep-2026-11-05.md`
  applies here too (`evaluate_three_candle_entry` discards ~0.57% of
  setups at the deployed `rr_min=3.0` on float noise) — identical exposure
  to the 5m baseline, so the comparison stands, but absolute trade counts
  are marginally understated at every interval.

## Reproduce

```bash
PYTHONPATH=. python3 scripts/three_candle_tf_backtest.py --interval 15m --days 45 \
    --json-out /tmp/tf_15m.json
PYTHONPATH=. python3 scripts/three_candle_tf_backtest.py --interval 1h --days 45 \
    --json-out /tmp/tf_1h.json
```

## Follow-up runs (2026-11-05, post-commit)

### 15m disjoint holdout (days 45–90, same 7 symbols)

`--days 45 --end-days-ago 45 --interval 15m`: 201 closed, grossR +9.35,
netR -9.57, **expNetR -0.048**. The recent-window +0.013 did NOT replicate
out-of-sample — 15m is rejected. Per-symbol: LINK +0.283, OP +0.125,
UNI +0.046 positive; APE -0.211, BNB -0.279, BTC -0.334, SOL -0.176
negative — the same symbol split as 5m, i.e. symbol selection, not
timeframe, drives the residual.

### 1h deep sample (days 0–90, same 7 symbols)

`--days 90 --end-days-ago 90 --interval 1h` (context 4h, ~80d effective
after warmup): 58 closed total, grossR -0.40, netR -3.30,
**expNetR -0.057** pooled; every per-symbol cell <20 trades →
inconclusive individually, but the pooled sign is negative and fee drag
fell to only 0.050 R/trade as predicted — the fee-relief hypothesis is
confirmed mechanically, yet gross expectancy stayed ~zero (-0.007 expR).
Wider stops dilute fees but do not create gross edge. 1h is not a path
to profitability for this detector either.

**Verdict update: no timeframe (5m/15m/1h) produces positive net
expectancy. Timeframe is not the missing variable.**
