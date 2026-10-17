# DECISION — Strategy Review Cycle 1 (2026-10-16)

Lead-owned. Panel: MiMo-V2.5 (entry), MiniMax-M2.7 (exit), GLM-5.3 (risk), Agnes 2.5 Flash (strategy/validation).
Artifacts: `analysis/panel/{mimo-entry,minimax-exit,glm-risk,agnes-strategies}.md`.

## Round 1 — consensus and conflicts

Agreed by all four: (1) the fixed 3R take-profit truncates the right tail (DOT 7.47R / ETH 4.16R MFE vs ~4.15R/~2.55R exits); (2) FVG is negative and geometrically broken (n=5, -0.397R, 15.6% target); (3) the 1h-range backtest harness is unfit for 5m logic; (4) changes must be validated on a 5m-path replay before deploy.

Conflicts (all four attacked each other with numbers):
- MiMo vs MiniMax: only 5% of signals can physically reach 3R because the stop floor uses the **1h ATR on a 5m pattern** (risk 0.95% vs structural 0.05-0.16%, 15x) — so exit redesign is rearranging a 5% winner cohort until the R unit shrinks. MiniMax's rr_min 3->4 would make it worse.
- GLM: the notional cap (50%) silently cuts the 5% slider to 0.5% ($1.03/trade vs $10.25 intended); 3 same-direction positions = 2.49 effective bets (avg rho +0.744 vs BTC); cap and stop-metric must change in lockstep.
- GLM vs MiMo: a 5m-ATR stop halves risk%, so cap=200% + 5m-ATR must be recomputed together or sizing silently changes again.

## Decisions

| # | Change | Status | Evidence | Notes |
|---|---|---|---|---|
| D1 | Cut FVG (disable the scan branch) | **ADOPTED** | n=5, -0.397R, 0 wins, 15.6% target | Re-enable only after n>=20 with rebuilt geometry |
| D2 | Stop floor uses ATR(5m) for 3CANDLE (keep 1h for FVG->none now) | **ADOPTED** | risk 0.95%->0.51%, 3R reach 5%->18%, E[min(MFE,3R)] 1.00->1.53R (n=1,905) | Requires sizing re-check (D4) |
| D3 | Runner-trail: arm at +1.0R, trail 0.75R (one-way, after BE) | **ADOPTED** | +0.27R->+0.33R/trade on 22 real entries | Keep fixed TP as the far exit; BE trigger 0.5->0.75R |
| D4 | Notional cap 50%->200%, max_positions 5->2 (settings) | **ADOPTED** | cap cut risk 10x; rho-driven effective bets | Sliders, hot-reload; no code change |
| D5 | Limit fill window 12s->600s | **DEFERRED** | fill 26%->76%, adverse selection 27% vs 16% | Needs order-lifecycle design (open restings across scans); redesign next cycle |
| D6 | rr_min 3->4 | **REJECTED** | only 5% reach 3R today | Revisit after D2 |
| D7 | C3 body>=50% quality filter | **DEFERRED** | +0.05-0.10R proxy, split-direction | Needs 60d walk-forward first |
| D8 | Disable MATICUSDT | **ADOPTED** | 404 on fapi (delisted) | Operational |
| D9 | Same-direction exposure guard | **DEFERRED** | rho +0.744, 2.49 effective bets | D4 (max_positions=2) mitigates for now |
| D10 | Time stop / partial scale-out | **REJECTED** | -0.09R / -0.20R on the 22 | Minimax tested both |

## Validation before deploy
5m-path, no-lookahead replay mirroring `scan_symbol` (per Agnes's standard): stop-before-target, fees stated, >=60 days where klines allow, >=20 trades/cell, walk-forward split. Configs: baseline current vs (D2) vs (D3) vs (D2+D3) vs (D2+D3+D4).

## Validation results (CORRECTED — harness fixed 2026-10-16 04:15 +04)

The first harness run had two bugs (exit labels fired in disabled configs; `run_config`
never applied its arm/trail/be parameters to the simulator). Both fixed; the simulator
now passes 8 hand-built path sanity checks (LONG/SHORT trail, BE, SL, TP, stop-first
conservative ordering, one-way ratchet). n≈2,270 filled trades per config, 45 days,
8 symbols, entry = midpoint limit filled ≤60m, stop-before-target, 240m horizon.

**Gross (no fees) vs net (0.07% round trip):**

| config | gross R/trade | net R/trade | win% | exits |
|---|---|---|---|---|
| baseline (1h-ATR stop, TP 3R, BE0.5) | −0.017 | −0.108 | 11.2 | BE 1257, SL 735, TP 211 |
| D2 (5m-ATR stop) | +0.039 | −0.320 | 6.6 | BE 1712, SL 412, TP 150 |
| D3 (runner 1.0/0.75, 1h-ATR) | −0.012 | −0.102 | 31.8 | TRAIL 664, BE 793, TP 43 |
| D2+D3 | **+0.144** | −0.216 | 34.6 | TRAIL 741, BE 1075, TP 46 |
| D2+D3+BE0.75 | +0.099 | −0.261 | 45.7 | TRAIL 975, BE 473, TP 64 |
| TP 2R variants | — | −0.099…−0.322 | — | no improvement |

**Revised verdicts (supersede the round-1 table where they conflict):**
- **No tested configuration is net-positive after fees.** The strategy's gross edge is
  ≈0 to +0.14R/trade depending on config; fees (0.09R at 1% stops, 0.36R at 5m-ATR
  stops) consume all of it. **Fee drag, not exit geometry, is the binding constraint.**
- **D2 (5m-ATR stop) REVERSED → REJECT.** Gross-positive but net-terrible: the tight
  stop multiplies fee drag 4× and floods BE scratches (75% of trades). MiMo's
  "3R reachability" mechanism is real but swamped.
- **D3 (runner trail) REVERSED → DEFER/REJECT.** Net +0.006R vs baseline — noise. It
  also cuts TP hits 211→43 (the 0.75R trail kills trades that would have run to 3R).
  The panel's +0.4R estimate was 22-trade small-sample noise. Not worth migration 007
  + position-manager complexity for a wash.
- **D1 (cut FVG) STANDS** — orthogonal to the above; removes live-measured −0.397R/trade.
- **GLM's settings triple STANDS** — sizing coherence is independent of R-expectancy.
- **D8 (disable MATICUSDT) STANDS** — operational.
- **Real levers the data points to**: (a) fewer, better trades (D7 C3-body filter,
  volatility/session filters — raises gross edge and cuts fee count), (b) fee structure
  (maker exits vs taker), (c) NOT exit-geometry tinkering.

## Cycle 2 — prototype strategy screen (2026-10-17)

Three candidate strategies on identical 5m candles, identical fee model
(0.05% taker x2 + 0.02% slippage x2 = 0.14% round-trip), R:R 2.0,
max 3 concurrent trades/symbol, 20 symbols, `--days 45` (Binance's 1000-bar
cap binds -> **~3.5 days of 5m data, NOT 45**).

Harness: `scripts/strategy_compare.py` + `scripts/aggregate_results.py`.

| Strategy | Trades | Win% | Gross R | Net R | Net exp/trade | Symbols positive |
|----------|-------:|-----:|--------:|------:|-------------:|-----------------:|
| A. VWAP mean reversion | 583 | 33.3% | -38.51 | -132.39 | **-0.227R** | 4/20 (20%) |
| B. ORB (15min range)  | 990 | 30.3% | -168.89 | -366.18 | **-0.370R** | 1/20 (5%) |
| C. OB+FVG confluence   | 131 | 55.0% | +79.18 | +64.56 | **+0.493R** | 17/20 (85%) |

- **A (VWAP) REJECT.** Negative net on 16/20 symbols. Same shape as the live
  FVG loss profile (D1): the stop sits beyond the deviation extreme, so the
  risk distance is ~3-4x wider than the target's edge can pay for at 0.14%
  round-trip. Fee drag dominates.
- **B (ORB) REJECT.** Worst of the three. 990 trades, 30% win — the 15m range
  is too tight relative to 5m ATR, so breakouts get chopped out and the
  wide stop (opposite range edge) is paid for on nearly every trade.
- **C (OB+FVG) CANDIDATE — needs a longer window before adoption.** The only
  net-positive config. **Sample size is the caveat: 131 trades over 20 symbols
  is ~6.5 trades/symbol, below the 20-trade inconclusive threshold in
  `analysis/AGENTS.md`.** Positive on 17/20 symbols is consistent with a real
  edge, but 3.5 days cannot separate it from luck. Next step is a paginated
  45d run (the current harness cannot — single unpaginated 1000-bar request)
  or a 1h-signal variant with more bars per fetch.

**Harness correctness notes (all fixed this cycle, 192 tests):**
- ATR is per-index (`calculate_atr_series`), lookahead-free: bar *i* uses
  candles[0..i] only. The earlier scalar full-series ATR leaked future
  volatility into every historical stop and filter.
- OB confluence is bounded by `confirm_at_idx`, not `formed_at_idx` — an OB is
  only knowable after its pivot AND its BOS pivot confirm.
- The overlap cap tracks EXIT bars (a multiset, not a set — unresolved trades
  all share `len(candles)`); unresolved trades are now counted, not silently
  dropped from the denominator.
- `evaluate_fvg_entry`'s `rr_ratio >= rr_min` float boundary silently dropped
  ~0.15% of setups; gated with an epsilon.

**Caveat on C:** it is FVG + an OB filter, while D1 disabled production FVG on
a measured **-0.397R/trade**. C's positive result is NOT a contradiction of
D1 — it is a different population (only OB-confluent gaps, wide 1h-ATR stops,
~6 trades/symbol). It does not by itself justify re-enabling plain FVG.

## Monitoring log
- 2026-10-16 14:42 +04: **Paper-session cleanup done.** Root cause: bot restarts in `paper` mode by default (`BotEngine._mode = "paper"` on init) — a service restart silently reverts to paper without warning. Fix (migration 009, `6ff8aee`): added `mode VARCHAR(10) NOT NULL DEFAULT 'live'` to both `positions` and `trades`; all three creation sites now pass `mode` explicitly from `BotEngine._mode` (reconcile too). Existing rows retroactively classified `live` via server_default — 32 positions / 25 trades all `mode=live`. Paper trades 330-333 deleted, positions 340-342 deleted, daily_summary 2026-10-16 recalculated to `+0.85R` (your 2 manual closes only). Bot restarted in paper mode — press **Start → Live** in the dashboard to resume live trading. `paper_use_real_balance=false` in DB; paper sizing uses $10k default.
- 2026-10-16 16:48 +04: engine STOPPED (mode=live, 0 scans). 8 trades today 6W/0L +2.91R. 1 OPEN position: XRPUSDT LONG 231.5 @ 1.3400, SL=1.3302 TP=1.3739 (protected on exchange ✅). Previous 3 positions (BTCUSDT, OPUSDT, ADAUSDT) closed manually. Exchange Sync button + profit-lock trailing + stale-trade exit + liquidation/margin display all deployed today. 0 journal errors.
- 2026-10-17 03:48 +04: engine running/live, 154 scans. 17 trades 9W/6L +1.21R (3 new trades since last check, net +0.81R). 1 OPEN position: OPUSDT LONG 1078.3 @ 0.13716, SL=0.13778 (profit-locked above entry!) TP=0.14428. Exchange PnL +$1.09. Profit-lock trailing stop confirmed working. 0 journal errors.
- 2026-10-16 13:48 +04: unchanged. engine stopped (limit_hit=true, -3.479R). XRP SHORT + ALGO LONG OPEN (paper). 0 trades/70min. 0 journal errors.
- 2026-10-16 04:48 +04: engine OK (running/live, 732 scans), 3 OPEN positions all protected **at the exchange** (BTC SL 81841.4/TP 84670.4, XRP SL 1.3486/TP 1.2913, BNB SL 731.01 BE/TP 711.76 — verified via openAlgoOrders; orders-table links absent for the two reconcile-adopted rows as expected). 0 trades in 4h — max_positions=3 reached by the open positions, so no new entries (expected, not a fault). 6,214 signals expired in 4h (pipeline generating, execution capped). LIMIT-entry fill rate still unmeasured (no attempts). Journal: 138 error lines = transient Postgres-restart blips in the bookTicker callback ("Connection refused"/"shutting down/starting up"), self-recovered — no code fault; note for backlog: make the tick handler resilient to brief DB outages.
- 2026-10-16 01:24 +04: engine OK (`{"status":"ok"}`), 3 OPEN positions all protected (BNB SL 736.41, XRP SL 1.3486, BTC SL 81841.4), 0 trades/2h, 0 journal errors, no naked/unprotected events, no LIMIT-fill cancellations logged yet.

### Monitoring check — 2026-10-17 04:48 (+04:00)

**Engine:** was STOPPED at check time, restarted in live mode, confirmed running (289 scans, 26s uptime). No crash in journal — likely a manual stop or restart gap.

**Daily:** 0 trades today (Oct 17 UTC), 0/3R used, limit not hit.

**Positions:** 0 open — flat. No unprotected positions.

**Trades 70m:** 1 closed (+0.58R, +$0.67) — exit reason not retrievable (GROUP BY error in query).

**Signals:** 0 new PENDING in 70m, 0 LIMIT cancelled.

**Journal:** 0 errors/tracebacks in 70m.

**Note:** Daily counter reset at UTC midnight — today is a fresh trading day. Engine running, pipeline healthy.

## Cycle 2 outcome — OB+FVG adopted (2026-10-17)

User decision: **implement, default ON** (explicit confirmation, after being
shown the corrected figures below).

### Harness bug found and fixed before shipping

`run_obfvg_backtest` entered at `fvg_idx_5m + 1` — the START of the still-forming
1h hour. An FVG at 1h bar `i` is built from bars `i-2, i-1, i`, so it only exists
at that hour's CLOSE = 5m index `i*12 + 11`. The exit scan therefore ran ~10 bars
(50 minutes) before the gap it was trading existed. Fixed via
`fvg_entry_idx_5m()` in `scripts/strategy_compare.py` + regression test.

**This invalidated the cycle-2 figures I first reported (+0.493R at R:R 2.0 /
+0.497R at R:R 3.0).** Corrected:

| Config | Trades | Net expectancy |
|---|---|---|
| R:R 2.0, ungated (buggy) | 131 | +0.493R |
| R:R 3.0, ungated (buggy) | 109 | +0.497R |
| R:R 3.0, ungated (**fixed**) | 111 | **+0.432R** |
| R:R 3.0, **+ live trend gate** | 84 | **+0.315R** |

R:R 3.0 (the global `rr_min`, per user) was chosen over the backtest's 2.0 — the
edge holds at 3.0 because a wider stop makes fee drag a smaller fraction of risk.

The gate drops 109 → 82 trades (75% survive); the 27% it removes are
counter-trend (33 counter-trend SHORT, 12 counter-trend LONG, 3 weak-trend).

### What ships

`signal_service.detect_obfvg_candidates` → `strategy_type="OBFVG"`, gated on
`strategy_obfvg_enabled` (default **true**), `strategy_obfvg_max_age_bars`
(default 2 closed 1h bars). No migration: `strategy_type` is `String(20)` on
signals/positions/trades.

**Freshness is the load-bearing approximation.** The backtest entered each gap at
the first 5m bar after its forming hour closed. Live has no historical index to
enter at, so it approximates "at formation" by AGE — only the last N closed 1h
bars may fire. This is not equivalent: raising `max_age_bars` widens the trade
population beyond what was measured. Do not tune it upward without re-measuring.

### Evidence quality — stated plainly

84 trades over **3.5 days**. `--days 45` yields ~3.5 days because Binance caps
unpaginated `/fapi/v1/klines` at 1000 bars (the script warns about this). This is
below the 20-trades-per-cell bar in `analysis/AGENTS.md`; a single bad week could
account for the whole result. The default-ON decision means this takes real money
on that evidence — next step is either kline pagination for a proper 45d read or
a paper soak before trusting live OBFVG numbers.

Does NOT justify re-enabling plain FVG (D1 stays: disabled at -0.397R/trade).
Different population.

### Monitoring check — 2026-10-17 05:49 (+04:00)

**Engine:** RUNNING/live, 16 scans. **Daily:** 0 trades, 0/3R. **Positions:** none (flat). No unprotected positions.

**Trades/signals 70m:** 0 closed, 0 PENDING, 0 LIMIT unfilled. Fresh UTC day.

**Journal:** ⚠️ **453 rate-limit / 400 / 429 events** + 80 "Rate limited" in 15min — the 71399af scan fix (24→1 positionRisk/scan) had NOT stopped the pressure. My earlier 90s "clean" window was a fluke inside a self-clearing cooldown; I over-read it.

**Root cause (2 deeper bugs the scan fix didn't address):**
1. `RateLimiter.acquire()` slept WHILE holding the lock and refilled a flat 20 tok/s from an oversized 1200 bucket → never actually throttled; effective request rate unbounded.
2. High-cost calls declared weight 1 when Binance charges more: `get_klines(limit=200)`→2, `positionRisk`→5, `balance`→5. The scan fetches 200 bars × 3 intervals × 24 symbols/pass, blowing ~2× the real budget.

**Fix (f8838cb):** limiter sleeps outside the lock, bucket sized to Binance's real 2400-weight/min cap (refill 36/s ≈ 2160/min); correct weights on klines/positionRisk/balance. 2 regression tests.

**Verified:** 5-minute clean window post-deploy — **0 rate-limit events, 0 errors**, engine advancing (12 scans). 207 tests pass.
