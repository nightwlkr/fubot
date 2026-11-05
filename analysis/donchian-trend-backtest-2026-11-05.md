# Donchian ensemble trend backtest — 2026-11-05

**Harness:** `scripts/donchian_backtest.py` (standalone research script; does not touch `backend/backtest.py` or any live path). Reuses `backend/backtest_data.py` (`fetch_klines`/`align_window`/`INTERVAL_MS`) and `backend/core/cost_model.py` (`LIVE` taker model). Candle cache: `/tmp/donchian_klines/`.

## Verdict

**1d: weakly positive, consistent across two disjoint 2-year windows — the first pooled net-positive result in this program, but tail-driven and modest.** With a 0.5% minimum stop-distance floor (see "R-normalization hazard"):

| Window | Trades | Win% | Net R | expNetR | PF | MaxDD (R) | avgNet%/trade |
|---|---|---|---|---|---|---|---|
| 1d, last ~730d (≈ Nov 2024 – Nov 2026) | 295 | 41.4 | +26.72 | **+0.091** | 1.40 | −20.09 | +1.70% |
| 1d, 730d ending 730d ago (≈ Nov 2022 – Nov 2024) | 255 | 42.4 | +39.92 | **+0.157** | 1.70 | −21.08 | +1.68% |

**4h: no edge.** Recent ~180d: 553 trades, expNetR **+0.016**, PF 1.05 (breakeven). Prior disjoint ~180d (`--end-days-ago 180`): 495 trades, expNetR **−0.077**, PF 0.73 — sign flips between windows, so the 4h cell is rejected.

**Fee-drag thesis confirmed:** at 1d stop distances (median ≈ 28% of entry) the modeled 0.11% round trip costs ≈ **0.005R per trade** (recent window: grossR 28.23 → netR 26.72 over 295 trades). Fees are no longer the binding constraint; the edge itself is small.

## Strategy implemented

- **Universe:** BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, LTC, AVAX, DOT, NEAR, SUI, OP, UNI (all USDT-M perps; all fetched successfully).
- **Ensemble:** lookbacks {10, 20, 55} bars on both TFs; long when close exceeds ≥2 of 3 channel tops (tops = max high of the N bars strictly before the signal bar), short mirrored on channel bottoms.
- **Entry:** next bar open. One position per symbol, no pyramiding.
- **Initial stop:** 20-bar opposite channel extreme frozen at the signal bar; 1R = |fill − stop|. Signals whose next-bar open already gaps through the stop are dropped.
- **Exit:** trailing stop at the 10-bar opposite channel extreme, ratcheting only, recomputed at each closed bar; a bar whose range touches the stop exits at the **next bar open** (no take-profit; only `trail`/`window_end` exits observed).
- **Costs:** `CostModel.round_trip_in_r(entry, risk)` = `entry × 0.00110 / risk` per trade; `pct_net` columns report the same economics per unit notional.

## Lookahead / ordering statement

Signals use only bars closed before the decision bar (channels exclude the signal bar itself); entries fill at the next bar's open; the trailing stop in force during bar *k* is computed from bars closed before *k*; stop touches are detected on bar *k*'s OHLC and executed at bar *k+1*'s open. There is no intrabar stop fill — OHLC cannot order events inside a bar, so detection is on the closed bar and execution is next open, identical in spirit to the entry rule. No lookahead anywhere in the replay.

## Data hygiene (important)

Binance public klines contain corrupt bars. `sanitize_candles()` drops any bar with non-positive/inverted OHLC or a high >2.2× / low <1/2.2× the median of adjacent closes; dropped bars are counted in output. Confirmed bad ticks include ETHUSDT 1d 2025-06-17 `high=104454.9` (real ≈ 2500), SUIUSDT 1d 2025-04-12 `low=0.0076` (real ≈ 2.3), NEARUSDT 1d 2025-03-22 all-OHLC corrupt. The two reported 1d windows dropped 19 and 7 bars respectively. **The ~4-years-ago window (ending 1460d ago, ≈ 2020–2022) was attempted and REJECTED**: >1000 bars failed sanity checks and surviving corrupt prints produced absurd fills (e.g. DOTUSDT "entry 0.10"), so no inference is drawn from it. Sparse listings (SUI/OP pre-2023) also truncate that window.

## R-normalization hazard (read before quoting expNetR)

The 20-bar opposite extreme can sit arbitrarily close to the fill in a tight range, making risk → ~0 and any ordinary move an enormous R multiple. Observed: UNIUSDT long 2024-06-18 → +123.2R on a **+1.4%** notional move (risk ≈ 0.01%); XRPUSDT short 2025-03-31 → +50.9R on a ~17% move with a 0.33% stop (this print sits inside a week of anomalous pinned XRPUSDT klines — every 1h bar showed H≈2.527/L≈2.499 — verified identical on the 1h endpoint, so it is Binance's served data, but it is low-confidence). The `--min-risk-pct` floor (applied at fill: `100·|entry−stop|/entry ≥ floor`) removes these degenerate trades; **0.5% and 1.0% floors give identical pooled results**, i.e. only ~2 trades per window were affected. All headline numbers above use the 0.5% floor; the spec-faithful unfloored runs are: recent 1d 297 trades / netR +77.28 / expNetR +0.260 (one trade = 77% of net R), prior 1d 256 trades / netR +163.10 / expNetR +0.637 (one trade = 78%).

Even floored, results are tail-driven by construction (median netR ≈ −0.08 to −0.11; win rate ~41%): ex-top-1-trade the recent window is still +0.089 expNetR, ex-top-5 ≈ +0.002. Trend-following expectancy lives in the right tail; ~300 trades over 2 years is a thin sample for tail estimates.

## Per-symbol notes (cells <20 trades are inconclusive)

Recent 1d window: only AVAX (−0.04), OP (−0.17) and SUI (−0.14) have ≥20 trades and are negative; the rest are inconclusive individually. Prior window: LTCUSDT −0.367 (n=15, inconclusive) and XRPUSDT −0.201 (n=13) are the weak cells; BTC +0.598, SOL +0.460, NEAR +0.334 look good but n<20. No symbol-level conclusion is supported; pooled stats only.

## Bottom line for the program

- Daily Donchian trend following is the **first configuration class with positive pooled net expectancy in two disjoint windows** (+0.09 / +0.16 expNetR per trade, PF 1.4–1.7, ~17-bar average holds, fee drag ≈ 0.005R).
- The edge is small, tail-dependent, and unproven out-of-sample beyond two windows that share the same strategy family and fee model. It is **not** a deployable signal on this evidence alone.
- 4h detection shows no edge (windows disagree in sign).
- Any follow-up should (a) test a third disjoint window once older klines are validated, (b) check robustness to the exit rule (the single 10-bar trail is the only exit — no breakeven/profit-lock), and (c) treat the anomalous XRPUSDT March-2025 prints as a data-quality caveat on the recent window's +0.091.

## Deep validation — 2026-11-05 (second pass)

Method: `/tmp/donchian_deep.py` re-simulates the **same cached, sanitized candles** through `donchian_backtest.simulate()` (`--min-risk-pct 0.5`), so every number below is on identical data to the headline runs. Parameter variants monkeypatch the module constants (`LOOKBACKS`, `TRAIL_LOOKBACK`, `MIN_CHANNEL_HITS`, `STOP_LOOKBACK`) — no code change. Three clean windows scored: **recent** ≈ Nov 2024–Nov 2026 (295 trades), **prior** ≈ Nov 2022–Nov 2024 (255), **win3** = 365d ending 730d ago ≈ Nov 2023–Nov 2024 (178 — *overlaps the second half of `prior`; a robustness slice, not a disjoint window*). The truly disjoint oldest window (≈ Nov 2020–Nov 2022, cached) remains **rejected**: 1,043 bars dropped by the sanitizer (>20% of some symbols' history) and one UNIUSDT trade (+218.5R, entry 3.34 → exit 16.85, a real 2021 bull move on a 1.8% stop) supplies **84% of that window's net R** — unusable.

### 1. Per-symbol stability (expNetR, n)

| Symbol | recent | prior | win3 |
|---|---|---|---|
| NEARUSDT | +0.193 (27) | +0.334 (21) | +0.312 (12) |
| OPUSDT | **−0.167** (29) | +0.177 (24) | +0.131 (13) |
| XRPUSDT | +0.601 (15) | −0.201 (13) | **−0.348** (13) |
| SUIUSDT | **−0.138** (22) | +0.091 (16) | +0.175 (12) |
| AVAXUSDT | −0.040 (22) | +0.280 (20) | +0.441 (12) |
| LTCUSDT | +0.064 (17) | −0.367 (15) | −0.223 (11) |
| DOGEUSDT | +0.014 (20) | −0.068 (19) | +0.531 (13) |
| UNIUSDT | +0.108 (22) | +0.211 (17) | +0.534 (10) |
| BTCUSDT | +0.156 (14) | +0.598 (19) | +0.034 (13) |
| SOLUSDT | +0.089 (16) | +0.460 (18) | +0.312 (11) |
| BNBUSDT | +0.360 (17) | +0.129 (14) | +0.186 (12) |
| ADA/DOT/ETH/LINK | all +, n≤19 | all +, n≤17 | ETH −0.045, LINK −0.202, rest + |

- **No symbol is negative in both scored windows with ≥20 trades.** Recent negatives (AVAX −0.04, OP −0.17, SUI −0.14) are all ≥20-trade cells; prior negatives (DOGE, LTC, XRP) are all n<20 → inconclusive individually. Sign flips dominate: OP −0.17→+0.18, SUI −0.14→+0.09, XRP +0.60→−0.20.
- **Not a 1–2-symbol carry at the symbol level** — 12/15 symbols positive in recent, 10/15 in prior, NEAR positive in all three windows. But concentration is real in *R terms*: top-3 symbols contribute ≈76% of recent net R (XRP+NEAR+BNB = 20.3/26.7) and ≈67% of prior (BTC+SOL+NEAR = 26.6/39.9). NEAR is the only symbol ≥20 trades and positive in both scored windows.

### 2. Tail decomposition (net R)

| Window | total netR | top-1 | top-5 | top-10 | top-20 | ex-top-5 expNetR | ex-top-10 | median trade |
|---|---|---|---|---|---|---|---|---|
| recent | +26.72 (n=295) | 42.7% | **108.8%** | 151.4% | 207.5% | **−0.008** | −0.047 | −0.113 |
| prior | +39.92 (n=255) | 14.6% | 63.9% | 102.8% | 153.2% | +0.056 | −0.004 | −0.083 |
| win3 | +23.37* (n=178) | 37.8% | 112.9% | 162.9% | 216.7% | −0.018 | −0.086 | −0.128 |

(*win3 recomputed cleanly after fixing a cache-glob collision — see caveats.)

**Verdict on mid-distribution: there is NO mid-distribution edge.** Excluding the top 5 trades the pooled expectancy is ≈0 or negative in every window; excluding top-10 it is negative everywhere. The median trade loses ~0.1R. This is a pure lottery structure: ~41% win rate, winners are rare multi-R trail exits. Any live deployment inherits this — long flat stretches are expected, not a bug.

### 3. Monthly PnL curve (pooled, by exit month)

| Window | months | losing months | longest losing streak | longest below equity peak |
|---|---|---|---|---|
| recent | 25 | 12 (48%) | 3 | **586 d** (~80% of window) |
| prior | 25 | 14 (56%) | 5 | 281 d |
| win3 | 13 | 6 (46%) | 5 | 212 d |

The recent window's equity spent ~19 months underwater before the final run-up — classic trend-follower profile. Roughly half of all months lose money; streaks of 3–5 losing months are normal.

### 4. Parameter sensitivity (recent window unless noted; prior in parens)

**Structural finding first:** the Donchian channel tops are *nested* — top(55) ≥ top(20) ≥ top(10) by construction. Exceeding the 20-bar top implies exceeding the 10-bar top, so "close above ≥2 of 3 tops" is mathematically **equivalent to "close above the 20-bar top"** (and mirrored for shorts). The ensemble conviction filter is degenerate: it *is* a plain 20-bar Donchian breakout. Verified empirically: `LOOKBACKS=(10,20)` and `(10,55)` reproduce the baseline bit-for-bit (n=295, netR +26.72).

| Variant | n | netR | expNetR | PF | ex-top-5 expNetR | prior expNetR |
|---|---|---|---|---|---|---|
| baseline (≡20-bar entry, trail 10) | 295 | +26.7 | +0.091 | 1.40 | −0.008 | +0.157 |
| strict-55 (hits=3, ≡55-bar breakout) | 170 | +26.3 | **+0.155** | 1.95 | +0.046 | +0.152 |
| trail = 20 | 213 | +33.0 | **+0.155** | 1.61 | +0.012 | **+0.329** |
| trail = 5 | 345 | +15.3 | +0.044 | 1.24 | −0.032 | +0.091 |
| stop-lookback = 10 | 295 | +29.2 | +0.099 | 1.39 | 0.000 | +0.249 |
| stop-lookback = 40 | 295 | +26.9 | +0.091 | 1.53 | +0.002 | +0.140 |
| 4ch {5,10,20,55} h2 (adds 10&5-only breakouts) | 382 | +158.7 | +0.415 | 2.63 | −0.016 | +0.249 |
| {10,20,55,110} h2 | 281 | −3.6 | −0.013 | 0.95 | −0.078 | — |

- **Not knife-edge on the trail**: t20 ≥ t10 in both windows (prior +0.329 vs +0.157); t5 is worse in both. Slower trail = better, consistent with trend capture.
- **Stricter entry (55-bar breakout) and slower trail (20) are the most robust positive variants**: strict-55 gives +0.155/+0.152 with positive ex-top-5 expectancy in *both* windows (+0.046/+0.011); trail-20 gives +0.155/+0.329 (ex-top-5 +0.012/+0.068). Fewer trades for strict-55 (170/152) but better per-trade.
- The {…,110} cell is **inconclusive**: WARMUP_BARS=70 < 110, so the first ~40 window bars per symbol can't signal — the deficit is a warmup artifact, not a parameter effect (the signal set is provably identical to baseline anyway).
- The 4-channel variant genuinely changes signals (the 5-bar top breaks the nesting) and is positive in both windows but *more* tail-driven, not less.

### 4b. Third window

`--end-days-ago 730 --days 365` (Nov 2023–Nov 2024, fetched fresh — public klines, same sanitizer, 5 dropped bars): **157→178 trades\*, +23.37 netR, expNetR +0.131, PF 1.57** — third positive cell, though it overlaps `prior`'s second half so it is corroboration, not independent confirmation. \*n differs between the contaminated and clean runs; clean n=178.

### 5. Long/short split — the strongest finding

| Window | Long n | Long expNetR | Long PF | Short n | Short expNetR | Short PF |
|---|---|---|---|---|---|---|
| recent | 138 | **+0.233** | 1.95 | 157 | **−0.034** | 0.83 |
| prior | 119 | **+0.388** | 2.71 | 136 | **−0.046** | 0.79 |
| win3 | 102 | **+0.303** | 2.25 | 76 | **−0.099** | 0.54 |

Shorts are net-negative in **all three windows** (and negative in nearly every sensitivity variant). The entire edge is the long side — crypto's upward drift over 2022–2026. A live implementation should be **long-only**; shorts add ~55% of trades and subtract R.

### 6. Account simulation — $3,000, 1% risk/trade

Event-driven sim on the pooled trade list; each entry risks 1% of current equity (compound) or $30 fixed, qty = risk$ / |entry−stop|, fees = 0.11% notional. No margin/leverage cap modeled (worst-case notional ≈ risk$/0.5% ≈ 2× equity per position; concurrent positions could exceed margin — flagged, not modeled).

| Window | Mode | Final equity | Median month | p5 month | p95 month | Worst month | Best month | Losing months |
|---|---|---|---|---|---|---|---|---|
| recent | fixed $30 | $3,802 (+$802) | **+$12** | −$143 | +$200 | −$151 | +$886 | 12/25 |
| prior | fixed $30 | $4,198 (+$1,198) | **−$5** | −$167 | +$417 | −$183 | +$692 | 14/25 |
| win3 | fixed $30 | $3,701 (+$701) | +$26 | −$153 | +$401 | −$230 | +$675 | 6/13 |
| recent | compound | $3,716 (+$716) | +$12 | −$169 | +$224 | −$181 | +$886 | 12/25 |

Read: **the median month is ≈ $0** — most months are flat-to-slightly-negative noise; all profit concentrates in a handful of months (best month ≈ +$400–900 = 13–30% of equity). A bad month costs ~5% of equity. This is the dollar-level signature of the tail-driven R distribution.

### Verdict — deep validation

**DEPLOY-TO-PAPER (long-only).** The edge replicates in sign across three windows (+0.09/+0.16/+0.13 pooled; +0.23/+0.39/+0.30 long-only), is not parameter knife-edge (trail-20 and strict-55 both equal or better), and shorts are consistently negative — so the deployable spec is: 20-bar Donchian breakout (the "ensemble" degenerates to this), long-only, initial stop = 20-bar low, trail = 10–20-bar low ratchet, 0.5% min stop-distance floor, 0.11% RT costs.

**Mandatory caveats for the paper run:**
1. Mid-distribution expectancy is ≈0 — expect ~40% win rate, median trade ≈ −0.1R, and PnL only in rare multi-R months. Judge the paper run over ≥6–12 months, not weeks.
2. Per-symbol cells are all inconclusive (n<30); do not subset the universe on this evidence.
3. The strict-55 variant is the only config with positive ex-top-5 expectancy in both windows — consider it as the primary paper spec.
4. The 2020–2022 window is unusable (corrupt klines + one 218R trade = 84% of net R); true out-of-sample history beyond 4 years is unavailable.
5. Any live port must keep the next-bar-open execution, closed-bar channel math, and the 0.5% risk floor — removing the floor reintroduces the degenerate-R trades documented above.
