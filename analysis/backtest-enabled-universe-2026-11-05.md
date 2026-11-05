# Backtest: 45-Day Enabled Universe, Current Deployed Config — 2026-11-05

**Purpose**: measure the **live** universe. `analysis/session-symbol-research-2026-11-05.md`
§1.4 / R4 established that `symbols.enabled` is now APT, ARB, SOL, UNI, XRP and
that **APT, ARB and XRP have zero trades in the entire database** — 60% of the
book that the scanner will actually trade has never been measured by anything.
The 45-day runs in `analysis/backtest-45d-2026-11-05.md` covered the *old*
7-symbol set (APE, BNB, BTC, LINK, OP, SOL, UNI), not the enabled one. This run
closes that gap.

**Window**: last 45 days of closed candles, pinned to one `[start, end)`:
**2026-09-21 05:00 UTC → 2026-11-05 05:00 UTC** (45.000 days exactly).
Paginated via `backend/backtest_data.py`; all three intervals pinned to the same
window; series joined on `open_time`, never by index.

**Universe**: APTUSDT, ARBUSDT, SOLUSDT, UNIUSDT, XRPUSDT — the production
`enabled=true` set. All five resolved on Binance Futures; no symbol was
invalid or nonexistent, and no symbol had to be dropped. The CLI run and the
independent pinned-window replay (below) produced **identical** per-symbol and
TOTAL metrics.

## Deployed configuration (as of commit `927ce9e`)

Mirrors production exactly; every knob is either the code default or an
explicit CLI flag matching the deployed setting.

| Setting | Value | Source |
|---------|-------|--------|
| Strategies | 3CANDLE only | `strategy_fvg_enabled=false`, `strategy_choch_enabled=false`, `strategy_obfvg_enabled=false` (`--no-fvg --no-choch --no-obfvg`); `strategy_3candle_enabled` left at its default `True` (`backend/backtest.py:295`) |
| ATR stop floor | `atr_stop_mult = 1.5` | module constant `backend/indicators/three_candle.py:13` (not passed — the default **is** the deployed value after the revert) |
| Risk floor | `min_risk_pct = 0.005` (0.5%) | harness default `backend/backtest.py:282` (not passed — the code default is the deployed value) |
| `rr_min` | 3.0 | `--rr-min 3.0` |
| Trend strength | MODERATE | `--strength MODERATE` |
| Neutral markets | allowed | `--neutral` |
| Volume filter | off | `--no-volume` |
| Regime filter | on | harness default `backend/backtest.py:281` |
| Signal max age | 60 min | `backend/backtest.py:67` |
| Entry fill | touch-fill (0 bp) | `--entry-penetration-bps` default 0 |
| Costs | 0.11% round trip | `backend/core/cost_model.py` (0.045% taker ×2 + 0.01% slippage ×2) |

**Command**

```
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
  scripts/backtest.py --symbols APTUSDT,ARBUSDT,SOLUSDT,UNIUSDT,XRPUSDT --days 45 \
  --no-fvg --no-choch --no-obfvg --rr-min 3.0 --strength MODERATE --neutral --no-volume
```

Ran clean on the first attempt (exit 0, no transient Binance errors, no retry
needed). Raw output: `/tmp/backtest_enabled_universe_2026-11-05.log`.

## Results — per symbol and TOTAL

`totalR`/`expR` are **gross**; `netR`/`expNetR` are **net of the 0.11% round
trip** and are the numbers to trust.

| symbol | sig | closed | win% | totalR | netR | expR | expNetR | trades/d |
|--------|-----|--------|------|--------|------|------|---------|----------|
| APTUSDT | 72 | 71 | 39.4 | 7.75 | 3.98 | 0.109 | **0.056** | 1.6 |
| ARBUSDT | 54 | 54 | 24.1 | -8.86 | -11.67 | -0.164 | **-0.216** | 1.2 |
| SOLUSDT | 77 | 77 | 39.0 | 6.66 | -0.29 | 0.086 | **-0.004** | 1.7 |
| UNIUSDT | 52 | 52 | 44.2 | 11.05 | 8.19 | 0.213 | **0.157** | 1.2 |
| XRPUSDT | 68 | 67 | 32.8 | -4.49 | -11.65 | -0.067 | **-0.174** | 1.5 |
| **TOTAL** | | **321** | | **12.11** | **-11.44** | **0.038** | **-0.036** | |

Aggregate: **321 closed trades, gross +12.11R, net -11.44R, net expectancy
-0.036R per trade** over 45 days (≈7.1 candidate trades/day across 5 symbols).

### Skip-reason breakdown

| symbol | counter_trend | ranging_regime | weak_trend | no_fill | regime_counter_trend | total skipped |
|--------|---------------|----------------|------------|---------|---------------------|---------------|
| APTUSDT | 199 | 94 | 18 | 25 | 0 | 336 |
| ARBUSDT | 217 | 106 | 12 | 22 | 0 | 357 |
| SOLUSDT | 158 | 77 | 17 | 28 | 2 | 282 |
| UNIUSDT | 229 | 154 | 19 | 13 | 0 | 415 |
| XRPUSDT | 204 | 124 | 20 | 33 | 1 | 382 |
| **TOTAL** | **1007** | **555** | **86** | **121** | **3** | **1772** |

`counter_trend` is 57% of all skips, `ranging_regime` a further 31% — the
trend/regime gates, not the R:R or risk filters, are what remove candidates.
`no_fill` (the LIMIT midpoint was never touched within the 60-minute signal
age) is 121 candidates, 7% of skips; `rr_at_fill` never fired.

## Statistical power (the part that decides the verdict)

Every cell clears the 20-trade inconclusive floor set in `analysis/AGENTS.md:21`
(52–77 closed trades), but "clears the floor" is not "significant". Measured on
the per-trade net-R distribution of the same pinned window (throwaway
`/tmp/fubot_enabled_stats.py`, which reproduces the CLI table exactly):

| symbol | n | mean netR | SD | SE | t | 95% CI (normal approx.) |
|--------|---|-----------|----|----|---|------------------------|
| APTUSDT | 71 | +0.056 | 0.983 | 0.117 | +0.48 | [-0.173, +0.285] |
| ARBUSDT | 54 | -0.216 | 0.775 | 0.106 | **-2.05** | **[-0.423, -0.009]** |
| SOLUSDT | 77 | -0.004 | 0.899 | 0.102 | -0.04 | [-0.205, +0.197] |
| UNIUSDT | 52 | +0.157 | 0.904 | 0.125 | +1.26 | [-0.088, +0.403] |
| XRPUSDT | 67 | -0.174 | 0.887 | 0.108 | -1.60 | [-0.386, +0.038] |
| **POOLED** | **321** | **-0.036** | **0.902** | **0.050** | **-0.71** | **[-0.134, +0.063]** |

**Exactly one cell's confidence interval excludes zero, and it points the wrong
way** (ARBUSDT, t = -2.05). UNIUSDT — the best cell — has t = +1.26: a 95% CI
of [-0.088, +0.403] cannot exclude zero at n = 52. The pooled TOTAL is also
indistinguishable from zero (t = -0.71). CI is the normal approximation;
Student-t at df = 51–320 is ≤ 2.7% wider, which changes no conclusion.

The honest reading: **this sample does not resolve the sign of any
per-symbol expectancy except ARB's negative.** The point estimates are
directionally consistent with every other measurement of this strategy, but the
sample is too small to prove them.

## Where the R actually comes from

| metric | value |
|--------|-------|
| Exit mix | BE_STOP 218 (67.9%), SL_HIT 102 (31.8%), **TP_HIT 1 (0.3%)** |
| Winners | 116 trades, mean **+0.942R** (net R per winning trade) |
| Losers | 205 trades, mean **-0.589R** (net R per losing trade) |
| Realised net win/loss ratio | 0.942 / 0.589 = **1.6 : 1** at a 36.1% net win rate (the gross win rate is also 36.1% — the same count within rounding) |
| Fee drag | mean **0.0734 R/trade**, **23.55R total**, median 0.0648, range 0.0109–0.2001 |
| Net-R distribution | ≥+2.5: 1 · +1.0..2.5: 40 · 0..+1.0: 75 · -0.5..0: 103 · -1.5..-0.5: 101 · ≤-1.5: 1 |

**The 3R target is essentially unreachable in this configuration**: 1 trade in
321 reached it, while 68% of trades exit at the break-even/profit-lock trail
for roughly 0R gross. The realised net win/loss ratio is 1.6:1, not the 3:1 the
`rr_min=3.0` setting advertises, and the gross expectancy (+0.038R/trade) is
smaller than the fee drag (0.073R/trade) — which is why the net number is
negative. This is the same structural finding as the production forensics
("zero TP hits" in 35 live trades,
`analysis/production-trade-analysis-2026-11-05.md`) and R5/R6 in
`analysis/session-symbol-research-2026-11-05.md`.

## Behaviour under the deployed live guards

The raw replay is per-symbol and ignores the portfolio guards. A second
simulation replays the same candidate stream through the deployed guard set
(`max_positions=3`, `max_daily_trades=12`, per-symbol cooldown 240 min,
daily breaker -3R, weekly breaker -5R on the Monday-UTC boundary):

| Simulation | trades taken | netR | expNetR | trading days | days halted | weeks halted | worst day |
|------------|--------------|------|---------|--------------|-------------|--------------|-----------|
| A — slots + cooldown + daily trade cap | 257 | -7.44 | -0.0290 | 43 | 0 | 0 | -4.30R |
| B — A + daily -3R breaker | 239 | -6.82 | -0.0285 | 44 | 6 | 0 | -4.35R |
| C — B + weekly -5R breaker | 194 | -8.02 | -0.0413 | 44 | 5 | 2 | -4.35R |

Uncapped daily net R over the window: 43 trading days, mean **-0.266R/day**,
worst -5.50R, best +4.25R; 5 days fell below -3R and 1 below -5R.

At the deployed `risk_percent = 1.0` (1R = 1% of equity — the 2026-10-23
production setting), simulation C is **≈ -8% of equity per 45-day window** if
the weekly guard does not latch first — and the weekly guard latches **twice in
45 days** here, the same mechanism as the live halt on 2026-11-04 (root
AGENTS.md deploy log: the guard halted the scanner on a corrected weekly net
loss of 7.57R against the 5R limit). Simulation assumptions, stated
because they are approximations: R is booked on the **exit** day (as the live
code books it), breakers are evaluated at entry time on **realized** R only
(the live breaker also counts open-position unrealized net R), and positions
already open when a breaker trips keep booking their R afterwards — which is
why a "capped" day still prints -4.35R.

## Comparison with the 7-symbol D2 run

`analysis/backtest-45d-2026-11-05.md` decomposition row **D2** is the same
configuration (atr 1.5 + min-risk 0.5%) on the old 7-symbol universe:

| metric | 7-symbol D2 (old universe) | enabled universe (this run) |
|--------|----------------------------|------------------------------|
| symbols | APE, BNB, BTC, LINK, OP, SOL, UNI | APT, ARB, SOL, UNI, XRP |
| closed trades | 448 | 321 |
| gross totalR | +30.75 | +12.11 |
| **gross R/trade** | **+0.069** | **+0.038** |
| fee drag | 41.75R (0.093 R/trade) | 23.55R (0.073 R/trade) |
| netR | -11.00 | -11.44 |
| **expNetR** | **-0.025** | **-0.036** |

**The enabled universe is worse per trade: -0.036 vs -0.025 expNetR.** The
mechanism is visible in the gross column: the enabled set's gross edge
(+0.038R/trade) is roughly **half** the old set's (+0.069R/trade), while its
fee drag per trade is actually *lower* (0.073 vs 0.093). The universe change
traded a cost advantage for a gross-edge loss. The similar TOTAL netR
(-11.44 vs -11.00) is a coincidence of different trade counts (321 vs 448),
not evidence of parity.

**Harness reproducibility cross-check** — SOL and UNI are in both universes:

| symbol | 7-symbol run A (atr 1.5, min-risk 0.05%) | this run (atr 1.5, min-risk 0.5%) |
|--------|------------------------------------------|------------------------------------|
| SOLUSDT | 75 closed, expNetR +0.004 | 77 closed, expNetR **-0.004** |
| UNIUSDT | 53 closed, expNetR +0.152 | 52 closed, expNetR **+0.157** |

Both cells reproduce to within ±0.005R and ±2 trades across two separate
processes and windows, which is the expected behaviour — and shows the 0.5%
risk floor is not what moves SOL or UNI.

## Verdict

**Expected net expectancy of the CURRENT live book: -0.036R per trade**
(321 closed trades, 45 days, 95% CI [-0.134, +0.063]).

1. **The live book has no demonstrated edge, and its point estimate is
   negative.** -0.036R/trade is worse than the -0.025 of the old universe, and
   the whole universe is losing money in expectation.
2. **The difference versus the old universe is not statistically significant**
   (-0.011R/trade against a pooled SE of 0.050). The defensible claim is
   "no better, point estimate worse", not "measurably worse".
3. **The enabled set is negatively selected on exactly the part that had no
   evidence.** ARB and XRP — the two of the three symbols with **zero live
   trades ever** — are the two worst cells (-0.216, -0.174) and carry
   **-23.32R** of the -11.44R total; the two positive cells (UNI +8.19,
   APT +3.98) carry +12.17R; SOL is flat (-0.29R). The three unmeasured
   symbols split into one mildly positive and **two clearly negative**.
   ARB is the only statistically defensible cell in the whole table, and it
   points the wrong way for the live book.
4. **ARB is the one result strong enough to act on**: 95% CI [-0.423, -0.009],
   t = -2.05, 24.1% win rate, 1.2 trades/day. It is the single cell where the
   evidence says the symbol is a net drain on the live book. XRP trends the
   same way (t = -1.60) but its CI includes zero.
5. **The blocker is not the universe — it is the exit structure.** With 1 TP
   hit in 321 trades, a 1.6:1 realised net win/loss ratio and a gross edge
   (+0.038R/trade) below the fee drag (0.073R/trade), re-cutting the symbol
   list cannot manufacture an edge: the best cell (UNI) is +0.157R/trade with
   a CI that includes zero. Raising the target's reachability or cutting the
   cost per trade is where the evidence points.
6. **Restarting the scanner on this book is not supported by this evidence.**
   Under the deployed guards the replay loses ≈ 7–8% of equity per 45-day
   window and the weekly guard latches twice in 45 days — the current halted
   state is the correct state for a negative-expectancy configuration.

Any universe change (drop ARB/XRP, add LINK, re-enable UNI-only, etc.) is the
user's decision and needs explicit approval — nothing here changes a setting,
and the scanner stays halted.

## Caveats

1. **One window, one regime.** 2026-09-21 → 2026-11-05 is a single 45-day
   sample, and it is the **same window** as the 7-symbol runs, so this is not
   an out-of-sample test of anything. It is a different *universe*, not a
   different period.
2. **Sample size.** Every cell clears the 20-trade inconclusive floor
   (52–77 closed) but only ARB's CI excludes zero. The pooled TOTAL
   (n = 321) still has SE 0.050R — the strategy's true expectancy could be
   anywhere in [-0.13, +0.06] and this run cannot tell.
3. **Harness omissions** (same as `backtest-45d-2026-11-05.md`): no
   `PositionManager._check_profit_trailing_2` (stage-2 profit-lock), no
   queue/partial-fill modelling, no funding rate, touch-fill entries only.
4. **The raw replay is optimistic vs live.** Production rests a LIMIT at the
   zone midpoint and skips the signal when it never fills, and it re-checks
   R:R at the *fill* price; the harness fills on touch and does not re-check.
   Live throughput was 2.2 trades/day over the 35-trade sample, below the
   5.7–7.1 trades/day this replay takes.
5. **The guard simulation is an approximation** — realized-only breaker
   checks, exit-day R booking, post-trip positions still booking. It is a
   magnitude estimate, not a ledger.
6. **Multiple comparisons.** The universe was pre-registered (it is what
   production has enabled), but "which symbols are good" is a selection over 5
   cells and will produce false positives; the pre-registered table above is
   the honest one.
7. **No live corroboration.** APT, ARB and XRP still have zero live trades;
   SOL and UNI remain conflicting between live and backtest samples
   (`analysis/session-symbol-research-2026-11-05.md` §1.3).

## Reproduce

- CLI: the command above (raw output `/tmp/backtest_enabled_universe_2026-11-05.log`).
- Statistics + guard simulation: `/tmp/fubot_enabled_stats.py` (throwaway, per
  `analysis/AGENTS.md` work guidance) → `/tmp/fubot_enabled_stats.log`. It
  replays the same pinned window through the same `Backtester` settings and
  reproduces the CLI table **exactly**, then adds the per-trade distribution,
  the exit mix, the fee-drag decomposition and the guard simulation.
- Window pin: `start=1789966800000` (2026-09-21 05:00 UTC),
  `end=1793854800000` (2026-11-05 05:00 UTC).
