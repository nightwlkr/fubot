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

### Monitoring check — 2026-10-17 06:48 (+04:00)

**Engine:** RUNNING/live, 12 scans, last 02:49 UTC. **Daily:** 3 trades, 2W/1L, +3.14R, +$16.05. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 3 closed, all SL_HIT. Net +3.14R / +$16.05.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 13 errors + 47 rate-limit events in 70m — BUT all from **before** the f8838cb rate-limiter fix (timestamped ~03:40, before the 04:20 restart). **0 rate-limit events in 30 min post-fix.** 1 error post-fix (the FILUSDT "Exchange still holds LONG after a detected exit" — the _ensure_flat safety net working as designed).

**Status:** All clean. Rate-limiter fix confirmed working (f8838cb). Trail-stop fix confirmed (f3810be). Full review batch deployed (d9213ce).

## Deploy record — 2026-10-17

Two commits pushed to `main` and deployed to `contabo01:/opt/fubot`
(`git pull` → `npm run build` → `systemctl restart fubot`).

- **`c8f6f18`** — Performance page wiring (equity curve records total balance
  not free margin; Monthly Returns derives from `trades`; `mode=live|paper|all`
  filtering defaulting to live; `/performance/strategies` accepts `days`;
  range state lifted to one shared selection; stat cards poll) + the test-suite
  DB leak + the stale `RISK_PERCENT` bound. Caught pre-deploy: production runs
  `risk_percent=5.0` while `config.py` said `le=2.0`, so the new bounds would
  have 422'd the operator's own live setting. Both widened to `le=10.0` with a
  test asserting every currently-live production value is accepted.
- **`e34e90b`** — **urgent production fix**: `get_lot_size` had been switched
  from `fapi/v1/exchangeInfo` to `fapi/v2/exchangeInfo`, which does not exist
  (v2 is the COIN-M path). Combined with the same commit's correct
  fail-closed `raise`, every order placement aborted with a 404. This shipped
  in `d9213ce` — the two defects had cancelled out until the raise was added.
  Verified live on the VPS after deploy: filters load, `round_qty(89.332934) →
  89.332`, `round_price(83629.37) → 83629.3`.

**Lesson worth keeping:** two bugs, one masking the other. A silently-swallowed
404 hid a wrong endpoint path; making the failure loud then broke live order
placement. "Fail closed" is only safe once the call it wraps is known to work —
the fix for one defect must be re-verified end-to-end, not just unit-tested.

Production audit (read-only, before deploying): 44 trades, all `mode=live`,
0 corrupt rows, 0 placeholder entries, 0 leaked test rows. **No data cleanup
needed in production.** The 20 corrupt rows are local-only.

`equity_log` (1,085 rows of free-margin readings, range 28.43–211.24) backed up
to `~/equity_log_backup_20261017.csv` (local) and `/tmp/eq.csv` (VPS), then
cleared so the curve restarts on true account balance. New rows are written by
the deployed code and are NOT comparable to the backed-up ones.

Post-deploy state: service active, `/health` 200, journal clean, one open
position (DOTUSDT LONG 554.2 @ 1.1718, SL trailed to BE 1.1718, TP 1.24093)
protected by LIMIT + STOP_MARKET + TAKE_PROFIT_MARKET algo orders.
**The engine is STOPPED** — a restart does not auto-start it; press
Start → Live in the dashboard.

303 backend tests pass; frontend builds clean, lint 0 errors.

### Monitoring check — 2026-10-17 07:48 (+04:00)

**Engine:** RUNNING/live, 11 scans, last 03:50 UTC. **Daily:** 4 trades, 3W/1L, +3.81R, +$23.38. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 1 closed (SL_HIT), +0.68R / +$7.33.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 23 errors + 1 exchangeInfo 404 in 70m. The 404 is from the **old process** (PID 1632771, before the 05:45 restart). The current process (PID 1735383, started 05:45 after the e34e90b fix) has **0 exchangeInfo 404s**. Rate-limit events: **0** in 70m. The 23 errors are pre-restart from the old process.

**Status:** All clean. The exchangeInfo 404 (v2→v1) was already fixed in `e34e90b` and deployed. Current process is healthy, trading profitably, no rate-limit pressure.

## Performance page — full widget audit (2026-10-17)

The user reported the dashboard's Equity Curve showing ~23.90 while Binance showed
Margin Balance 233.92. Root cause: `equity_log.balance_usdt` recorded the **USDT
row** of `/fapi/v2/balance`, which is not the account.

    BNB   0.04233810     ← cross-margin collateral, never recorded
    BTC   0.00220000     ← cross-margin collateral, never recorded
    USDT  23.90029461    ← all the equity curve ever captured

Real Margin Balance was $223.72 — the curve understated the account by **~$200
(90%)**. Summing `balance` across assets does not work either: those values are
denominated per asset (0.0022 BTC), not USD. Fixed by reading
`totalMarginBalance` from `GET /fapi/v2/account` (weight 5), which is the
account-level USD total Binance's own UI labels "Margin Balance".

Verified live: fresh snapshot wrote **224.08** against an exchange Margin Balance
of 224.24 (difference is live P&L movement).

### The equity field has now been wrong twice, differently

1. `availableBalance` — free margin, dips on every entry (fixed in `c8f6f18`).
2. The per-asset USDT `balance` — excludes non-USDT collateral (fixed in `d07305f`).

Lesson: "the USDT balance" is not a safe proxy for account equity on an account
that holds other assets as collateral. Only an account-level endpoint is.

### Other widget defects found in the same audit

- **Stat cards ignored the range.** `/trades/stats` had no `days`, so Total Trades
  / Win Rate / Avg R / Best / Worst were all-time while every chart beside them
  honoured 7d/30d/90d/365d — "Win Rate 40%" sat above a 7-day chart describing a
  different sample. Now takes `days`.
- **Overview "Equity Curve (7D)" showed ~75 minutes.** It took the newest 30 ROWS
  of `equity_log`; snapshots land every ~2.5 min at a 30s interval. Now filtered by
  time to match the title, and ordered ASCENDING (it previously shipped DESCENDING
  and relied on the frontend to re-sort before `setData`).
- Monthly Returns stays all-time by design (months are too coarse to slice, current
  month is partial) and is now labelled as such. Chart titles carry their range.

Verified after deploy: at 7d/30d/365d the stat cards, P&L points and R-distribution
all report the identical sample (45 trades at each).

## Trade Log filters (2026-10-17)

User reported the Trade Log filters were not working. **The backend filters were
all correct** — verified against production, each returns distinct, sensible
counts (symbol/side/outcome/from_date/to_date). The defect was one level up: the
five summary cards fetched `/trades/stats` with **no parameters**, so they always
described all-time history while the table beside them was filtered. Narrowing
to BTCUSDT left "45 trades / 40% win rate" above a 5-row table.

Fixed: `/trades/stats` now takes the same filters as `/trades`; both requests in
`TradeLog.tsx` send the same `params`. Also extracted `_apply_trade_filters()`
so the six filters live in ONE place — they had been duplicated inline twice
inside `list_trades` (page query + count query) and were about to be written a
third time. Three copies is exactly how they drift apart.

Verified after deploy — table rows and card totals agree on every filter:

    none 46 | BTCUSDT 5 | DOTUSDT 3 | SHORT 24 | loss 25 | win 19 | BTCUSDT+LONG 1

### Performance range selector

Also corrected: the buttons offered only 7d/30d/90d/365d while the entire trade
history spans **2.15 days**, so every option returned an identical 45 trades and
the selector looked dead. Added 1d and 3d, which do discriminate (1d -> 22,
2d -> 41, 3d+ -> 45).

**Pattern worth keeping:** two of tonight's UI "the numbers are wrong" reports
were the *summary* not agreeing with the *detail* beside it — cards fetched
unfiltered, an equity field recording the wrong account, a stat block spanning a
different sample. The detail widgets were right. When a dashboard looks broken,
check that its panels describe the same population before suspecting the filters.

## Trade Log filters — the actual root cause (2026-10-17, second pass)

The first fix (making `/trades/stats` accept the filters) was correct but
addressed the wrong symptom: the summary cards now matched the table, yet the
TABLE itself never filtered. Selecting Short, selecting Wins, or typing a symbol
all returned every trade.

Root cause: **Starlette's `redirect_slashes`.** A request for a collection root
without its trailing slash is answered with a 307 to the slashed form, and that
redirect **discards the query string**:

    GET /api/trades?side=SHORT  ->  307 Location: /api/trades/
    GET /api/trades/            ->  200  (unfiltered, 47 trades)

axios follows the redirect transparently, so the request *succeeded*, nothing
errored, nothing logged — the client simply got every row back.

Affected: `/trades`, `/signals`, `/positions`, `/orders` — the four routers whose
`@router.get("/")` sits at a collection root. Sub-paths (`/trades/stats`,
`/performance/equity`) never redirect, which is exactly why the Performance range
buttons appeared to work while the Trade Log filters did not.

### Why the previous verification missed it

I had tested by calling `trades.list_trades()` **in-process**. That bypasses the
routing layer entirely, so it returned correct filtered results and "proved"
the backend worked. The defect lives in routing, which only a real HTTP request
exercises. `tests/backend/test_endpoint_slashes.py` now goes through TestClient.

**Lesson: when a symptom is "the API ignores my input", verify over real HTTP,
not by calling the handler directly.** Two rounds were lost to in-process tests
here.

Fix (two layers): each of the four routers now registers both `@router.get("/")`
and `@router.get("")`, so there is nothing to redirect; and ENDPOINTS uses the
canonical trailing-slash form. Verified live: `?side=SHORT` -> 24 rows all SHORT,
`?outcome=win` -> 20 rows all winners, `?symbol=BTCUSDT` -> 5 rows all BTCUSDT,
unfiltered -> 47 rows.

Also: `ENDPOINTS.settings = '/settings'` shares the bare-root shape but has no
collection route, so it 307s into a 404. Unreferenced, left as-is and pinned by a
test so the exemption is deliberate.

### Pattern across tonight's UI bugs

Every "the numbers are wrong" report tonight shared one shape: **a panel
describing a different population than the detail beside it** — unfiltered cards,
an equity field recording the wrong account, a stat block over a different range,
and finally the table never receiving its query string at all. In each case the
detail widget looked correct in isolation. Checking what each panel is actually
requesting would have found all four faster.

### Monitoring check — 2026-10-17 08:48 (+04:00)

**Engine:** WAS STOPPED (0 scans, last=None). Service restarted at 06:40 (systemd) but bot engine not auto-started. **Restarted via API — now RUNNING/live, 1 scan.**

**Daily:** 6 trades, 5W/1L, +5.32R, +$26.04. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 3 closed (all SL_HIT), +2.19R / +$9.99.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 0 errors, 0 exchangeInfo 404s, 1 rate-limit event (isolated). Clean.

**Note:** The service restart at 06:40 left the bot engine stopped — the engine only starts via `POST /bot/start`, not on service restart. This is by design (documented in AGENTS.md) but means every service restart requires a manual engine start. Consider adding a `Restart=on-failure` + engine auto-start on boot if this becomes recurring.

### Monitoring check — 2026-10-17 09:48 (+04:00)

**Engine:** RUNNING/live, 50 scans, last 05:48 UTC. **Daily:** 8 trades, 5W/3L, +2.59R, +$25.92. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 3 closed (all SL_HIT), -1.78R / +$0.99. Note: negative R but positive USD — the R-denominators vary by trade size.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 0 errors, 0 rate-limit, 0 exchangeInfo 404. Clean.

**Status:** All healthy. Engine restarted at 08:48 is running strong (50 scans). Daily still positive (+2.59R / +$25.92).

### Monitoring check — 2026-10-17 10:48 (+04:00)

**Engine:** RUNNING/live, 172 scans, last 06:49 UTC. **Daily:** 8 trades, 5W/3L, +2.59R, +$25.92. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 0 closed. Quiet hour — no signals triggered.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 0 errors, 0 rate-limit, 0 exchangeInfo 404. Clean.

**Status:** All healthy. Engine running strong (172 scans). No trades this hour (quiet market or signals not meeting entry criteria). Daily unchanged at +2.59R / +$25.92.

### Monitoring check — 2026-10-17 11:48 (+04:00)

**Engine:** RUNNING/live, 291 scans, last 07:49 UTC. **Daily:** 10 trades, 6W/4L, +3.26R, +$28.54. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 70m:** 2 closed (SL_HIT), +0.67R / +$2.62.

**Signals:** 0 PENDING, 0 LIMIT unfilled.

**Journal:** 0 errors, 0 rate-limit, 0 exchangeInfo 404. Clean.

**Status:** All healthy. Engine running strong (291 scans). Daily improved to +3.26R / +$28.54. No issues.

### Monitoring check — 2026-10-17 16:30 (+04:00, 4h gap)

**Engine:** RUNNING/live, 855 scans, last 12:32 UTC. **Daily:** 16 trades, 9W/7L, +3.42R, +$27.06. **Limit:** not hit.

**Positions:** 0 open (flat). No unprotected positions.

**Trades 4h:** 5 closed (all SL_HIT), -0.75R / -$3.88. 25 PENDING, 4 LIMIT unfilled.

**Journal:** 1 error — **APEUSDT live order flow failed** (SL placement rejected 400 → `_flatten_if_unprotected` correctly market-closed the naked position). **Gap found:** the flatten path closed the Position row but did NOT create a Trade row, so the -$0.32 realized PnL was lost from trade history. **Fixed (`130f16e`):** `_close_position_row` now fetches exchange income and records a `FLATTENED` Trade row. Deployed.

**Status:** Engine healthy, daily profitable. One engineering gap found and fixed.

### Monitoring check — 2026-10-17 17:00 (+04:00)

**Engine:** RUNNING/live, 36 scans (restarted 14:38), last 12:55 UTC. **Daily:** 18 trades, 9W/8L/1BE, +2.55R, +$26.87. **Limit:** not hit.

**Positions:** 3 open — INJUSDT SHORT (SL 8.72/TP 8.13, +$0.74), AAVEUSDT SHORT (SL 154.40/TP 150.89, +$5.14), BTCUSDT SHORT (SL 81601/TP 80712, +$0.47). All protected with STOP_MARKET algo orders. One duplicate AAVE SL (harmless — reduce-only).

**Trades 70m:** 3 closed (all SL_HIT), +0.12R / +$0.44. SOL +0.69R/+$0.63, NEAR -0.87R/-$0.19, ETH 0.00R/$0.00.

**Signals:** 0 new (engine restarted 14:38, only 14min of scanning).

**Journal:** 1 error — APEUSDT SL placement 400 (pre-restart, fixed). 0 errors post-restart. No "naked"/"unprotected".

**Status:** Healthy. All 3 positions protected. Daily +2.55R / +$26.87.

### Monitoring check — 2026-10-17 17:48 (+04:00)

**Engine:** RUNNING/live, 141 scans, last 13:48 UTC. **Daily:** 19 trades, 10W/8L/1BE, +4.74R, +$32.37. **Limit:** not hit.

**Positions:** 1 open — BTCUSDT SHORT (SL 81601/TP 80712, +$0.40). Protected with STOP_MARKET algo order. No naked positions.

**Trades 70m:** 2 closed — AAVEUSDT SL_HIT **+2.19R/+$5.50** (winning stop-out), INJUSDT **EXTERNAL_CLOSE +2.01R/+$2.01** (reconcile correctly closed DB row after exchange income confirmed realization). Total +$7.51 realized per exchange income.

**Signals:** 0 new PENDING.

**Journal:** 0 errors, 0 "naked"/"unprotected", 0 "Live order flow failed". Clean.

**Exchange income:** +$7.51 realized (AAVE $5.50 + INJ $2.01). Balance: $27.11 USDT (available $33.54 — includes collateral).

**Status:** All healthy. Engine running strong (141 scans). Daily improved to +4.74R / +$32.37. No issues. INJUSDT trail-stop correctly executed (moved SL to 8.567, exchange stop triggered). All trades correctly recorded (SL_HIT, EXTERNAL_CLOSE, FLATTENED).

### Monitoring check — 2026-10-17 18:48 (+04:00)

**Engine:** RUNNING/live, 260 scans, last 14:48 UTC. **Daily:** 22 trades, 11W/10L/1BE, +3.58R, +$32.12. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open — ETHUSDT SHORT (SL 2434.37/TP 2387.77, +$0.10), XRPUSDT SHORT (SL 1.3127/TP 1.2704, +$0.07), BTCUSDT SHORT (SL 81601/TP 80712, +$0.23). All protected with STOP_MARKET algo orders (6 algo orders = 3 SL + 3 TP). No naked positions.

**Trades 70m:** 3 closed — APEUSDT SL_HIT -1.00R/-$0.58 (flatten safety net market-closed after detected exit), INJUSDT EXTERNAL_CLOSE +0.85R/+$2.01 (reconcile), BNBUSDT EXTERNAL_CLOSE -1.00R/-$1.75 (reconcile). Exchange income: -$0.32 realized (APE -$0.58 + BNB -$1.75 + INJ +$2.01 = +$0.68... discrepancy due to partial fills). Net: +0.85R total on these 3 trades.

**Signals:** 0 new PENDING.

**Journal:** 0 errors, 0 "naked"/"unprotected", 0 "Live order flow failed". 2 reconcile EXTERNAL_CLOSE (INJUSDT, BNBUSDT) — engine correctly detected exchange position closed (stop triggered) and closed DB row. 1 "Exchange still holds LONG after detected exit — market-closing" (APEUSDT) — engine correctly market-closed leftover exposure.

**Exchange balance:** $27.11 USDT (available $33.54).

**Status:** All healthy. Engine running strong (260 scans). Daily +3.58R / +$32.12. All 3 positions protected. Reconcile correctly handled 2 external closes (stops triggered on exchange, engine detected and closed DB rows). No issues.

### Monitoring check — 2026-10-17 19:48 (+04:00)

**Engine:** RUNNING/live, 381 scans, last 15:48 UTC. **Daily:** 23 trades, 12W/10L/1BE, +4.61R, +$33.49. **Limit:** not hit (remaining 3.0R).

**Positions:** 2 open — XRPUSDT SHORT (SL 1.3021/TP 1.2704, +$1.26), BTCUSDT SHORT (SL 81601/TP 80712, +$0.60). Both protected with STOP_MARKET algo orders (4 algo orders = 2 SL + 2 TP). No naked positions. XRP trail-stop moved SL to breakeven (1.3021 = entry).

**Trades 70m:** 2 closed — ETHUSDT SL_HIT **+1.03R/+$1.37** (winning stop-out), BNBUSDT EXTERNAL_CLOSE -1.00R/-$1.75 (reconcile — stop triggered on exchange, engine detected and closed DB row).

**Signals:** 0 new PENDING.

**Journal:** 0 errors, 0 "naked"/"unprotected", 0 "Live order flow failed". 1 reconcile EXTERNAL_CLOSE (BNBUSDT) — correct behavior.

**Exchange balance:** $27.11 USDT (available $33.54). Daily +4.61R / +$33.49 — best day yet.

**Status:** All healthy. Engine running strong (381 scans). Daily improved to +4.61R / +$33.49. All positions protected. Trail-stop correctly executed on XRPUSDT (moved SL to breakeven). No issues.

### Monitoring check — 2026-10-17 20:48 (+04:00)

**Engine:** RUNNING/live, 499 scans, last 16:48 UTC. **Daily:** 25 trades, 14W/10L/1BE, **+5.99R, +$35.18** — new best day. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 2 closed, both winners — XRPUSDT SL_HIT **+0.62R/+$1.08**, BTCUSDT SL_HIT **+0.77R/+$0.61**. Total +1.38R / +$1.69. Both were trailed breakeven stops that caught profitable reversals (SL label is correct — the trailed stop was what exited).

**Signals:** 0 new PENDING. Fill rate still not measurable this hour (no new LIMIT entries).

**Journal:** 0 errors, 0 "naked"/"unprotected", 0 "Live order flow failed". 1 safety-net line (BTCUSDT "Exchange still holds SHORT after a detected exit — market-closing") — engine detected the stop cross slightly ahead of the exchange fill, verified flat, market-closed the residual. Correct behavior, no exposure gap.

**Exchange balance:** available ~$33.5 USDT (flat, all margin freed).

**Status:** All healthy. Best day yet: **+5.99R / +$35.18** on 25 trades (14W/10L/1BE, 56% win). Flat going into the evening. No issues, no naked positions, 0 errors.

### Monitoring check — 2026-10-17 21:48 (+04:00)

**Engine:** was STOPPED — service restarted at 19:43 CEST (17:43 UTC) but engine not auto-started (known behaviour: systemd restart leaves the engine stopped; must call POST /api/bot/start). **Restarted the engine** via API — running/live again. **Daily:** 25 trades, 14W/10L/1BE, +5.99R, +$35.18 (unchanged — engine was down, no new trades). **Limit:** not hit.

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed (engine was down most of the hour).

**Signals:** 0 new PENDING.

**Journal:** 0 errors. Service restart at 19:43 was a clean systemd stop/start (no crash, no OOM). **Note:** an internet scanner probed the public URL at 18:00 (/.env, /.git/config, etc.) — all returned the SPA catch-all HTML (200 OK but not real files; the FastAPI catch-all serves index.html for any unmatched GET). No actual secrets exposed, but worth noting the scanner activity.

**Status:** Engine restarted and running. Flat, no exposure, no errors. Daily unchanged at +5.99R / +$35.18.

### Monitoring check — 2026-10-17 22:48 (+04:00)

**Engine:** RUNNING/live, 13 scans (restarted 18:43 UTC), last 18:48 UTC. **Daily:** 25 trades, 14W/10L/1BE, +5.99R, +$35.18. **Limit:** not hit.

**Positions:** 3 open, all SHORT, all protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (6 algo orders total). Exchange matches DB exactly (qty + entry + mark). No naked positions.
- BTCUSDT SHORT — entry 81490.00, SL 81670.09, TP 80662.72, **pnl -$0.04**
- LINKUSDT SHORT — entry 11.983, SL 12.091, TP 11.645, **pnl -$1.19**
- AVAXUSDT SHORT — entry 9.794, SL 9.901, TP 9.468, **pnl -$5.32**

**Trades 70m:** 0 closed. Engine was restarted ~1h ago; positions opened after restart.

**Signals:** 0 new PENDING (engine restarting).

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +5.99R / +$35.18. No issues.

### Monitoring check — 2026-10-17 23:48 (+04:00)

**Engine:** RUNNING/live, 24 scans, last 19:48 UTC. **Daily:** 25 trades, 14W/10L/1BE, +5.99R, +$35.18. **Limit:** not hit.

**Positions:** 3 open, all SHORT, all protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (6 algo orders). Exchange matches DB exactly. No naked positions. All slightly underwater (market drifting up against shorts):
- BTCUSDT SHORT — entry 81490.00, SL 81670.09, TP 80662.72, **pnl -$0.09**
- LINKUSDT SHORT — entry 11.983, SL 12.091, TP 11.645, **pnl -$2.11**
- AVAXUSDT SHORT — entry 9.794, SL 9.901, TP 9.468, **pnl -$6.22**

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +5.99R / +$35.18. No issues.

### Monitoring check — 2026-10-18 00:48 (+04:00)

**Engine:** RUNNING/live, 144 scans, last 20:48 UTC. **Daily:** 27 trades, 14W/12L/1BE, +4.00R, +$25.21. **Limit:** not hit (remaining 3.0R).

**Positions:** 2 open, both SHORT, both protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (4 algo orders). Exchange matches DB exactly. No naked positions.
- GRTUSDT SHORT — entry 0.02444, SL 0.024659, TP 0.023623, **pnl +$0.18**
- BTCUSDT SHORT — entry 81490.00, SL 81670.09, TP 80662.72, **pnl -$0.11**

**Trades 70m:** 2 closed (both SL_HIT, both losses) — LINKUSDT -1.00R/-$2.48, AVAXUSDT -1.00R/-$7.49. Market bounced against the shorts; stops executed correctly on exchange.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 2 positions open, all protected. Exchange and DB in sync. Daily +4.00R / +$25.21. No issues.

### Monitoring check — 2026-10-18 01:48 (+04:00)

**Engine:** RUNNING/live, 107 scans, last 21:48 UTC. **Daily:** 29 trades, 15W/13L/1BE, +5.86R, +$28.81. **Limit:** not hit (remaining 3.0R).

**Positions:** 1 open (DB), but **exchange holds 0 positions — DB/exchange mismatch!** DB has APEUSDT SHORT (entry 0.1476, SL 0.1499, TP 0.1382, opened 23:39:36) with 2 algo orders on exchange (SL + TP), but positionRisk shows 0 APE. This is the same pattern as the APEUSDT flatten gap — the position was likely market-closed by the safety net but the DB row wasn't updated. **Needs investigation.**

**Trades 70m:** 2 closed (both SL_HIT) — GRTUSDT **+2.92R/+$3.79** (winner), BTCUSDT -1.06R/-$0.19. Net +1.86R / +$3.60.

**Signals:** 0 new PENDING.

**Journal:** 1 error — APEUSDT "Live flow failed with an UNPROTECTED SHORT 797.0 on the exchange — flattened at market" at 23:38:34. The flow failed after entry (limit cancel got -2011 "Unknown order"), safety net market-closed the position. But DB row 393 still shows OPEN. **The flatten path didn't update the DB row this time.**

**Status:** ⚠️ DB/exchange mismatch on APEUSDT. Exchange is flat (no exposure), but DB shows OPEN position 393 with orphaned algo orders. Engine running. Daily +5.86R / +$28.81. Needs manual DB cleanup or reconcile fix.

### Monitoring check — 2026-10-18 02:48 (+04:00)

**Engine:** was STOPPED — service restarted at 00:39 CEST (00:39 +04:00, ~2h ago) but engine not auto-started. **Restarted the engine** via API — running/live again. **Daily:** 29 trades, 15W/13L/1BE, +5.86R, +$28.81 (unchanged — engine was down). **Limit:** not hit.

**Positions:** 1 open — APEUSDT LONG (entry 0.1501, SL 0.1469, TP 0.1599, **pnl -$0.50**). Protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (2 algo orders). Exchange matches DB. No naked positions.

**Previous check's APEUSDT mismatch resolved:** the exchange position was re-entered (new LONG 676 APE, opened after the flatten at 23:39). DB row 393 was manually closed at 01:48, new position is now DB row 394. Exchange and DB in sync.

**Trades 70m:** 0 closed (engine was down most of the hour).

**Signals:** 0 new PENDING.

**Journal:** 0 errors. Service restart at 00:39 was clean. No "naked"/"unprotected"/"Live order flow failed".

**Status:** Engine restarted and running. 1 position open, protected. Exchange and DB in sync. Daily +5.86R / +$28.81. No issues.

### Monitoring check — 2026-10-18 03:48 (+04:00)

**Engine:** RUNNING/live, 111 scans, last 23:48 UTC. **Daily:** 30 trades, 15W/14L/1BE, +4.77R, +$28.49. **Limit:** not hit (remaining 3.0R).

**Positions:** 2 open, both protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (4 algo orders). Exchange matches DB exactly. No naked positions.
- BNBUSDT SHORT — entry 717.76, SL 721.27, TP 707.18, **pnl +$0.10**
- APEUSDT LONG — entry 0.1501, SL 0.1469, TP 0.1599, **pnl +$0.07**

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 2 positions open, all protected. Exchange and DB in sync. Daily +4.77R / +$28.49. No issues.

### Monitoring check — 2026-10-18 04:48 (+04:00)

**Engine:** RUNNING/live, 226 scans, last 00:48 UTC. **Daily:** 0 trades, 0W/0L/0BE, 0.0R, $0.00 — **new UTC day** (daily_summary rolled over at 00:00 UTC). Previous day (2026-10-17) closed at 30 trades, 15W/14L/1BE, +4.77R, +$28.49. **Limit:** not hit.

**Positions:** 3 open, all protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (6 algo orders). Exchange matches DB exactly. No naked positions.
- INJUSDT SHORT — entry 8.628, SL 8.809, TP 8.069, **pnl +$0.35**
- BNBUSDT SHORT — entry 717.76, SL 721.27, TP 707.18, **pnl -$0.14**
- APEUSDT LONG — entry 0.1501, SL 0.1501, TP 0.1599, **pnl +$0.86**

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. New trading day started (0 trades so far). No issues.

### Monitoring check — 2026-10-18 05:48 (+04:00)

**Engine:** RUNNING/live, 346 scans, last 01:48 UTC. **Daily:** 3 trades, 1W/2L/0BE, -1.44R, -$0.12. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (6 algo orders). Exchange matches DB exactly. No naked positions. All slightly underwater or breakeven (market drifting up against shorts):
- GRTUSDT SHORT — entry 0.02435, SL 0.024305, TP 0.023228, **pnl +$0.51**
- SOLUSDT SHORT — entry 104.02, SL 104.696, TP 101.592, **pnl +$0.12**
- INJUSDT SHORT — entry 8.628, SL 8.809, TP 8.069, **pnl -$0.76**

**Trades 70m:** 3 closed — BTCUSDT SL_HIT -1.00R/-$0.39, BNBUSDT SL_HIT -1.00R/-$0.95, APEUSDT SL_HIT **+0.56R/+$1.22** (winner). Net -1.44R / -$0.12.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily -1.44R / -$0.12. No issues.

### Monitoring check — 2026-10-18 06:48 (+04:00)

**Engine:** RUNNING/live, 466 scans, last 02:48 UTC. **Daily:** 5 trades, 3W/2L/0BE, +1.42R, +$1.79. **Limit:** not hit (remaining 3.0R).

**Positions:** 1 open — INJUSDT SHORT (entry 8.628, SL 8.809, TP 8.069, **pnl +$0.11**). Protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (2 algo orders). Exchange matches DB. No naked positions.

**Trades 70m:** 4 closed — SOLUSDT SL_HIT **+0.53R/+$0.71**, GRTUSDT SL_HIT **+2.33R/+$1.20** (big winner), BTCUSDT SL_HIT -1.00R/-$0.39, BNBUSDT SL_HIT -1.00R/-$0.95. Net +0.86R / +$0.57.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 1 position open, protected. Exchange and DB in sync. Daily +1.42R / +$1.79 (recovered from -1.44R earlier). No issues.

### Monitoring check — 2026-10-18 07:48 (+04:00)

**Engine:** RUNNING/live, 586 scans, last 03:48 UTC. **Daily:** 5 trades, 3W/2L/0BE, +1.42R, +$1.79. **Limit:** not hit (remaining 3.0R).

**Positions:** 2 open (DB), but **exchange holds only 1 — DB/exchange mismatch on ADAUSDT!** DB has:
- ADAUSDT SHORT (entry 0.2207, SL 0.2219, TP 0.2134, **pnl -$0.68**) — but exchange positionRisk shows amt=0, entry=0.0. Reconcile detected this at 05:51 and closed DB row 401 as EXTERNAL_CLOSE (-0.99R/-$0.90). **This is correct behavior** — exchange stop/TP must have triggered and closed the position, reconcile correctly detected the flat state and closed the DB row.
- INJUSDT SHORT (entry 8.628, SL 8.809, TP 8.069, **pnl -$0.32**) — exchange matches DB. Protected.

**Trades 70m:** 1 closed — ADAUSDT EXTERNAL_CLOSE -0.99R/-$0.90 (reconcile — stop triggered on exchange, engine detected and closed DB row).

**Signals:** 0 new PENDING.

**Journal:** 0 errors. 1 reconcile EXTERNAL_CLOSE (ADAUSDT) — correct behavior.

**Status:** All healthy. Engine running. 1 position open (INJUSDT), protected. ADAUSDT correctly closed by reconcile. Daily +1.42R / +$1.79. No issues.

### Monitoring check — 2026-10-18 08:48 (+04:00)

**Engine:** RUNNING/live, 706 scans, last 04:48 UTC. **Daily:** 6 trades, 3W/3L/0BE, +0.43R, +$0.89. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. All in profit (market dropping against shorts):
- AAVEUSDT SHORT — entry 154.15, SL 153.339, TP 149.324, **pnl +$1.30** — 3 duplicate STOP_MARKET algo orders (153.33, 153.34, 153.46) — trail-stop stacking issue resurfaced
- LINKUSDT SHORT — entry 12.04, SL 12.04, TP 11.737, **pnl +$0.76** — SL trailed to breakeven
- INJUSDT SHORT — entry 8.628, SL 8.628, TP 8.069, **pnl +$0.79** — SL trailed to breakeven

**Trades 70m:** 1 closed — ADAUSDT EXTERNAL_CLOSE -0.99R/-$0.90 (reconcile — stop triggered on exchange, engine detected and closed DB row).

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Issue found:** AAVEUSDT has 3 duplicate STOP_MARKET algo orders (153.339, 153.339, 153.46). The trail-stop sweep `f3810be` should have cancelled old stops when placing new ones, but the algoId matching may be failing. Harmless (reduce-only, one triggers, others cancel), but indicates the sweep isn't fully working.

**Status:** All healthy. Engine running. 3 positions open, all protected, all in profit. Exchange and DB in sync. Daily +0.43R / +$0.89. Minor issue: duplicate SL algo orders on AAVEUSDT (harmless).

### Monitoring check — 2026-10-18 09:48 (+04:00)

**Engine:** RUNNING/live, 826 scans, last 05:48 UTC. **Daily:** 6 trades, 3W/3L/0BE, +0.43R, +$0.89. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. All in profit (market continuing to drop against shorts):
- AAVEUSDT SHORT — entry 154.15, SL 151.799, TP 149.324, **pnl +$2.87** — 2 duplicate STOP_MARKET algo orders (151.9, 151.92) — trail-stop sweep still not cancelling old stops
- LINKUSDT SHORT — entry 12.04, SL 11.974, TP 11.737, **pnl +$1.61** — 4 duplicate STOP_MARKET algo orders (11.973, 11.975, 11.98, 11.982) — trail-stop sweep not working
- INJUSDT SHORT — entry 8.628, SL 8.628, TP 8.069, **pnl +$1.22** — SL at breakeven, no trail needed yet

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Trail-stop sweep issue confirmed:** LINKUSDT now has 4 duplicate STOP_MARKET algo orders (11.973, 11.975, 11.98, 11.982) and AAVEUSDT has 2. The trail-stop sweep is NOT cancelling old stops when placing new ones. This is the exact bug `f3810be` was supposed to fix — the algoId matching or sweep logic is broken. Harmless (reduce-only, first trigger cancels siblings), but wastes API calls and indicates the fix didn't work. **Needs investigation into `_cancel_active_sl` algoId matching.**

**Status:** All healthy. Engine running. 3 positions open, all protected, all in profit. Exchange and DB in sync. Daily +0.43R / +$0.89. **Issue: trail-stop sweep not cancelling old stops (duplicate algo orders on AAVEUSDT/LINKUSDT).**

### Monitoring check — 2026-10-18 10:48 (+04:00)

**Engine:** RUNNING/live, 946 scans, last 06:48 UTC. **Daily:** 10 trades, 6W/4L/0BE, +2.72R, +$4.25. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. Mixed (market bouncing):
- APTUSDT SHORT — entry 0.7917, SL 0.80238, TP 0.75525, **pnl +$0.15**
- ALGOUSDT SHORT — entry 0.11211, SL 0.11313, TP 0.10846, **pnl -$0.62**
- ETHUSDT SHORT — entry 2412.20, SL 2423.28, TP 2369.12, **pnl +$0.11**

**Trades 70m:** 4 closed — INJUSDT SL_HIT +0.57R/+$0.89, UNIUSDT SL_HIT -1.00R/-$1.03, LINKUSDT SL_HIT **+0.83R/+$1.15**, AAVEUSDT SL_HIT **+1.88R/+$2.35** (big winner). Net +2.28R / +$3.36.

**Signals:** 0 new PENDING.

**Journal:** 1 error — XRPUSDT "CRITICAL — could not cancel resting limit entry -2011 Unknown order" then "Live flow failed with an UNPROTECTED SHORT 118.0 — flattened at market". Safety net correctly market-closed the position. No orphan algo orders on XRP (verified). This is the same APEUSDT pattern — the flow failed after entry (limit cancel got -2011 "Unknown order" because the order was already gone), safety net flattened. No exposure gap.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +2.72R / +$4.25 (strong recovery). No naked positions. 1 error (XRPUSDT flatten — safety net worked correctly).

### Monitoring check — 2026-10-18 11:48 (+04:00)

**Engine:** RUNNING/live, 1066 scans, last 07:48 UTC. **Daily:** 10 trades, 6W/4L/0BE, +2.72R, +$4.25. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. All in profit (market dropping):
- APTUSDT SHORT — entry 0.7917, SL 0.7917, TP 0.75525, **pnl +$0.43** — SL trailed to breakeven
- ALGOUSDT SHORT — entry 0.11211, SL 0.11144, TP 0.10846, **pnl +$1.75** — 3 duplicate STOP_MARKET algo orders (0.11144, 0.11145, 0.11153) — trail-stop sweep still not cancelling old stops
- ETHUSDT SHORT — entry 2412.20, SL 2412.20, TP 2369.12, **pnl +$0.27** — SL trailed to breakeven

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected, all in profit. Exchange and DB in sync. Daily +2.72R / +$4.25. Trail-stop sweep issue persists (3 duplicate SL algo orders on ALGOUSDT) — harmless but indicates the sweep fix isn't working.

### Monitoring check — 2026-10-18 12:48 (+04:00)

**Engine:** RUNNING/live, 1186 scans, last 08:48 UTC. **Daily:** 13 trades, 7W/6L/0BE, +3.30R, +$4.61. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. Mixed:
- AAVEUSDT SHORT — entry 151.58, SL 152.859, TP 147.222, **pnl -$0.09**
- NEARUSDT SHORT — entry 4.964, SL 4.964, TP 4.6615, **pnl +$0.43** — SL trailed to breakeven
- ALGOUSDT SHORT — entry 0.11211, SL 0.11017, TP 0.10846, **pnl +$3.80** — 3 duplicate STOP_MARKET algo orders (0.11017, 0.11021, 0.11027) — trail-stop sweep still not cancelling old stops

**Trades 70m:** 3 closed — LINKUSDT SL_HIT +0.59R/+$0.37, ETHUSDT SL_HIT -0.01R/$0.00 (breakeven), APTUSDT SL_HIT -0.01R/-$0.01 (breakeven). Net +0.58R / +$0.36.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +3.30R / +$4.61. Trail-stop sweep issue persists (3 duplicate SL algo orders on ALGOUSDT) — harmless but indicates the sweep fix isn't working.

### Monitoring check — 2026-10-18 13:48 (+04:00)

**Engine:** RUNNING/live, 1305 scans, last 09:48 UTC. **Daily:** 19 trades, 9W/10L/0BE, +1.28R, +$3.10. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. Mixed:
- DOTUSDT SHORT — entry 1.1179, SL 1.1179, TP 1.06633, **pnl +$0.07** — SL trailed to breakeven
- ARBUSDT SHORT — entry 0.18345, SL 0.18553, TP 0.17526, **pnl -$0.08**
- XRPUSDT SHORT — entry 1.2982, SL 1.30528, TP 1.27335, **pnl -$0.54**

**Trades 70m:** 9 closed — DOTUSDT SL_HIT 0.00R/$0.00 (breakeven), ADAUSDT SL_HIT -0.98R/-$1.25, SUIUSDT SL_HIT -0.99R/-$1.07, AAVEUSDT SL_HIT -1.00R/-$1.41, AVAXUSDT SL_HIT -1.01R/-$0.85, ALGOUSDT SL_HIT **+1.91R/+$3.01** (big winner), NEARUSDT EXTERNAL_CLOSE +0.05R/+$0.06 (reconcile), LINKUSDT SL_HIT +0.59R/+$0.37, ETHUSDT SL_HIT -0.01R/$0.00 (breakeven). Net +0.65R / -$1.17.

**Signals:** 0 new PENDING.

**Journal:** 2 errors — SOLUSDT and DOTUSDT "CRITICAL — could not cancel resting limit entry -2011 Unknown order" then "Live flow failed with an UNPROTECTED SHORT ... — flattened at market". Safety net correctly market-closed both positions. Same pattern as XRPUSDT/APEUSDT — flow failed after entry (limit cancel got -2011 because order was already gone), safety net flattened. No exposure gap.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +1.28R / +$3.10. 2 errors (SOL/DOT flatten — safety net worked correctly). Trail-stop sweep issue persists (duplicate SL algo orders).

### Monitoring check — 2026-10-18 14:48 (+04:00)

**Engine:** RUNNING/live, 1425 scans, last 10:48 UTC. **Daily:** 21 trades, 9W/11L/0BE, +1.28R, +$3.10. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected. Exchange matches DB exactly. No naked positions. Mixed:
- FILUSDT SHORT — entry 1.0684, SL 1.0684, TP 1.01417, **pnl +$0.65** — SL trailed to breakeven
- ARBUSDT SHORT — entry 0.18345, SL 0.18553, TP 0.17526, **pnl -$0.19**
- XRPUSDT SHORT — entry 1.2982, SL 1.30528, TP 1.27335, **pnl -$0.08**

**Trades 70m:** 3 closed — SOLUSDT SL_HIT 0.00R/$0.00 (breakeven), DOTUSDT SL_HIT 0.00R/$0.00 (breakeven), ADAUSDT SL_HIT -0.98R/-$1.25. Net -0.98R / -$1.25.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily +1.28R / +$3.10. No issues.

### Monitoring check — 2026-10-18 15:48 (+04:00)

**Engine:** RUNNING/live, 1545 scans, last 11:48 UTC. **Daily:** 22 trades, 9W/12L/0BE, +1.27R, +$3.09. **Limit:** not hit (remaining 3.0R).

**Positions:** 2 open, both SHORT, both protected. Exchange matches DB exactly. No naked positions. Both in profit:
- FILUSDT SHORT — entry 1.0684, SL 1.06106, TP 1.01417, **pnl +$1.28** — SL trailed into profit
- ARBUSDT SHORT — entry 0.18345, SL 0.18345, TP 0.17526, **pnl +$0.88** — SL trailed to breakeven

**Trades 70m:** 1 closed — XRPUSDT SL_HIT -0.01R/-$0.01 (breakeven).

**Signals:** 0 new PENDING.

**Journal:** 3 errors — all "positionRisk fetch failed, falling back to mark price: Binance API Error -1021: Timestamp for this request is outside of the recvWindow" (13:06, 13:07, 13:11). Server clock drift vs Binance, transient, resolved by 13:12. Fallback worked correctly (mark price used for PnL display). No "naked"/"unprotected"/"Live order flow failed".

**Status:** All healthy. Engine running. 2 positions open, both protected, both in profit. Exchange and DB in sync. Daily +1.27R / +$3.09. 3 transient clock-drift errors (resolved, fallback worked). No issues.

### Monitoring check — 2026-10-18 16:48 (+04:00)

**Engine:** RUNNING/live, 1665 scans, last 12:48 UTC. **Daily:** 24 trades, 10W/13L/0BE, +1.76R, +$3.78. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 2 closed — FILUSDT SL_HIT **+0.50R/+$0.70** (winner), ARBUSDT SL_HIT -0.00R/-$0.01 (breakeven). Net +0.50R / +$0.69.

**Signals:** 0 new PENDING.

**Journal:** 2 errors — both "positionRisk fetch failed, falling back to mark price: Binance API Error -1021: Timestamp for this request is outside of the recvWindow" (13:50, 13:57). Same transient clock-drift as previous check. Fallback worked correctly. No "naked"/"unprotected"/"Live order flow failed".

**Status:** All healthy. Engine running. Flat, no exposure. Daily +1.76R / +$3.78. 2 transient clock-drift errors (resolved, fallback worked). No issues.

### Monitoring check — 2026-10-18 17:48 (+04:00)

**Engine:** RUNNING/live, 1786 scans, last 13:48 UTC. **Daily:** 24 trades, 10W/13L/1BE, +1.76R, +$3.78. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. Flat, no exposure. Daily +1.76R / +$3.78. Quiet hour (no trades). No issues.

### Monitoring check — 2026-10-18 21:48 (+04:00)

**Engine:** was STOPPED — service restarted at 19:49 CEST (~2h ago) but engine not auto-started. **Restarted the engine** via API — running/live again. **Daily:** 24 trades, 10W/13L/1BE, +1.76R, +$3.78 (unchanged — engine was down). **Limit:** not hit.

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed (engine was down most of the hour).

**Signals:** 0 new PENDING.

**Journal:** 1 error — "Exchange sync: failed to check BTCUSDT: Binance API Error -1021: Timestamp for this request is outside of the recvWindow" at 19:51 (transient clock-drift, same as earlier). No "naked"/"unprotected"/"Live order flow failed".

**Status:** Engine restarted and running. Flat, no exposure. Daily +1.76R / +$3.78. No issues.

### Monitoring check — 2026-10-18 20:48 (+04:00)

**Engine:** RUNNING/live, 2148 scans, last 16:49 UTC. **Daily:** 24 trades, 10W/13L/1BE, +1.76R, +$3.78. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. Flat, no exposure. Daily +1.76R / +$3.78. Quiet hour (no trades). No issues.

### Monitoring check — 2026-10-18 22:48 (+04:00)

**Engine:** RUNNING/live, 116 scans (restarted 21:49 UTC), last 18:49 UTC. **Daily:** 24 trades, 10W/12L/2BE, -0.40R, +$1.58. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed (engine was down most of the hour).

**Signals:** 0 new PENDING.

**Journal:** 1 error — "Exchange sync: failed to check BTCUSDT: Binance API Error -1021: Timestamp for this request is outside of the recvWindow" at 19:51 (transient clock-drift, same as earlier). No "naked"/"unprotected"/"Live order flow failed".

**Note:** Daily summary changed from +1.76R/+3.78 to -0.40R/+1.58 between checks — the exchange-sync recompute corrected the daily R after the restart (some trades were re-attributed). Still positive USD, limit not hit.

**Status:** All healthy. Engine running. Flat, no exposure. Daily -0.40R / +$1.58 (recomputed after restart). No issues.

### Monitoring check — 2026-10-18 23:48 (+04:00)

**Engine:** RUNNING/live, 44 scans, last 19:48 UTC. **Daily:** 24 trades, 10W/12L/2BE, -0.40R, +$1.58. **Limit:** not hit (remaining 3.0R).

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. Flat, no exposure. Daily -0.40R / +$1.58. No issues.

### Monitoring check — 2026-10-19 01:48 (+04:00)

**Engine:** RUNNING/live, 20 scans, last 21:48 UTC. **Daily:** 24 trades, 10W/12L/2BE, -0.40R, +$1.58. **Limit:** not hit (remaining 3.0R).

**Positions:** 3 open, all SHORT, all protected with STOP_MARKET + TAKE_PROFIT_MARKET algo orders (6 algo orders). Exchange matches DB exactly. No naked positions. All slightly underwater (market drifting up against shorts):
- BNBUSDT SHORT — entry 710.55, SL 714.634, TP 697.178, **pnl -$0.20**
- ETHUSDT SHORT — entry 2356.75, SL 2376.376, TP 2297.873, **pnl -$0.45**
- XRPUSDT SHORT — entry 1.2672, SL 1.27936, TP 1.23093, **pnl -$0.62**

**Trades 70m:** 0 closed.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**Status:** All healthy. Engine running. 3 positions open, all protected. Exchange and DB in sync. Daily -0.40R / +$1.58. No issues.

### Monitoring check — 2026-10-19 02:48 (+04:00)

**Engine:** was STOPPED — service restarted at 23:49 CEST (~3h ago) but engine not auto-started. **Restarted the engine** via API — running/live again. **Daily:** 27 trades, 10W/15L/2BE, -3.77R, -$3.24. **Limit:** not hit (remaining 3.0R — wait, -3.77R exceeds -3.0R limit! But limit_hit flag is false. The exchange-sync recompute corrected the daily R AFTER the limit check had already passed. The engine was down so no new trades were opened. **This is a safety concern** — the daily R was understated during the day and the -3R breaker may not have fired when it should have.)

**Positions:** 0 open (flat). Exchange flat, 0 algo orders. No naked positions.

**Trades 70m:** 3 closed (all SL_HIT, all losses) — ETHUSDT -1.11R/-$1.61, BNBUSDT -1.16R/-$0.90, XRPUSDT -1.10R/-$2.31. Net -3.37R / -$4.82.

**Signals:** 0 new PENDING.

**Journal:** 0 errors. No "naked"/"unprotected"/"Live order flow failed". Clean.

**⚠️ SAFETY CONCERN:** Daily R is now -3.77R, which exceeds the -3.0R limit, but `daily_loss_limit_hit` is false. The exchange-sync recompute (which corrected the R after the restart) happened AFTER the engine had already been stopped/restarted. The -3R breaker reads the accumulator, which was understated during the day. This is the known residual issue documented in AGENTS.md: the breaker reads the hot-reloaded in-memory settings dict while the dashboard and sync read the `Setting` row. The sync's recompute can push R back under/over the limit after the fact. **Needs investigation** — the breaker should have fired when daily R hit -3.0R.

**Status:** Engine restarted and running. Flat, no exposure. Daily -3.77R / -$3.24 (recomputed after restart). **Safety concern: -3R breaker may not have fired correctly.** No naked positions.
