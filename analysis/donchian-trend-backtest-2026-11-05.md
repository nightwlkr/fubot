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
