# Session & Symbol Research — 2026-11-05

**Scope**: Tasks 8 (session filters) and 12 (symbol-specific performance) of
`analysis/trade-loss-fix-tasks.md`.
**Data source**: production PostgreSQL on `contabo01`, **read-only** SQL only
(`SELECT` on `trades`, `positions`, `signals`, `settings`, `symbols`).
No orders, no settings changes, no service restarts, no writes to production.
**Sample**: 35 live trades, first entry 2026-10-20 13:46 UTC, last entry
2026-11-04 05:40 UTC. All 35 are `strategy_type = '3CANDLE'` (FVG and OBFVG
were disabled for the whole window).
**Companion artifacts**: `analysis/production-trade-analysis-2026-11-05.md`
(the parent forensic analysis), `analysis/backtest-45d-2026-11-05.md` (the
independent 45-day backtest used here as a second sample).

**Status**: ANALYSIS ONLY. No strategy change is applied from this artifact —
per `analysis/AGENTS.md:23`, changes need the user's explicit approval, the
test suite, and a recorded deploy in `analysis/panel/DECISION.md`.

---

## Summary (10 lines)

1. Live sample: **35 trades, -14.9088R net, 12W/23L, -0.426R/trade**, SD(R)=0.984.
2. **Correction to the parent analysis**: fee drag is **15.89R (0.454R/trade)**, not 26.61R (0.758R/trade) — the parent used the *trailed* stop as the denominator instead of the frozen risk basis.
3. **Correction to the parent analysis**: gross PnL was **-1.89R (-0.054R/trade)**, not +11.62R — the parent's gross figure was derived circularly as `net + mis-computed fee`, so it could not confirm its own thesis.
4. **The strategy had no measurable gross edge** in this sample; fees turned roughly flat gross PnL into -14.91R. Fee cuts alone cannot make it profitable.
5. **Per symbol**: LINK +0.134R (n=5) is the only positive cell; BNB -0.976R (n=5) is the worst. No symbol reaches significance on its own (smallest p = 0.174).
6. **Cross-validated against the 45-day backtest** (`analysis/backtest-45d-2026-11-05.md:53-59`): **BTC and BNB are negative in BOTH independent samples** — that is the strongest symbol evidence available.
7. **Session**: Asian 00-07 UTC is -0.739R/trade (n=14) vs London-core 08-16 UTC +0.0155R/trade (n=17); raw permutation p = 0.034 but the Bonferroni threshold over the ~50 slices examined is ≈0.001 → **not significant**.
8. Every session cell is under 20 trades → **inconclusive per `analysis/AGENTS.md:21`**; a 174,632-signal test (χ²=4.82, df=4, p≈0.30) finds no session effect on signal→trade conversion either.
9. **Recommendation**: do **not** ship a session filter (Task 8 stays open pending a backtest with a session filter); do **not** disable BNB/BTC on this evidence alone — but **do not re-enable** them either (both are already disabled, and both are negative in two independent samples).
10. **The live universe is now unmeasured**: `symbols.enabled` is APT, ARB, SOL, UNI, XRP — APT, ARB and XRP have **zero trades in the entire database**, so 60% of the current live universe has no evidence at all.

---

## 0. Corrections to `analysis/production-trade-analysis-2026-11-05.md` (read first)

Three numbers in the parent analysis do not reproduce from the production
database, and one of them changes the root-cause conclusion. The parent is
not wrong about the *direction* (fees dominate); it is wrong about the
*magnitude*, and the "gross is positive" claim that anchors its narrative is
circular.

### 0.1 The fee-drag denominator is the wrong column

The parent computed fee drag as

```sql
-- analysis/production-trade-analysis-2026-11-05.md:502-507
ABS(p.entry_price - p.stop_loss) / p.entry_price ...
```

`positions.stop_loss` is **mutable** — BE-trailing moves it toward and past
entry (`backend/AGENTS.md`, "BE-trailing is price-based"). For the 12 trades
whose stop was trailed into profit, `|entry - stop_loss|` is the *profit*
distance, not the risk distance, so the parent measured the trailing stop as a
"tight stop". The authoritative 1R is the frozen
`positions.initial_risk_distance` (`backend/models/position.py:19`,
`backend/core/risk_units.py:113-121`, used at
`backend/core/position_manager.py:1174-1177`).

The parent's own formula, applied to the same rows, reproduces its headline
exactly (26.5352R vs the stated 26.61R), which confirms the diagnosis rather
than the number.

| Measure | Parent (trailed stop) | Corrected (frozen risk basis) |
|---------|----------------------|--------------------------------|
| Total fee drag | 26.61R (doc) / **26.5352R** (recomputed) | **15.8862R** |
| Fee per trade | 0.758R | **0.4539R** |
| Fee model | `entry × 0.0011 / abs(entry - stop_loss)` | `entry × 0.0011 / initial_risk_distance` (matches `backend/core/cost_model.py:82-93`) |

Consequences: the parent's headline examples are wrong. Trade 2 was reported as
a "0.01% stop with 7.53R fee drag"; its frozen risk basis was **0.606%** and
its true fee drag **0.182R**. Trade 29 was reported as "0.02% / 6.27R"; its
basis was **0.475% / 0.232R**. Trade 32 was reported as "0.04% / 2.62R"; its
basis was **1.046% / 0.105R**. Only **trade 8** (BNBUSDT LONG, 2026-10-24
01:53 UTC) is genuinely degenerate: risk basis **0.0128%**, fee drag
**8.579R** — and it alone carries 54% of the corrected total fee drag.

### 0.2 "Gross +11.62R" is circular

The parent's gross figure is `net + fee_drag` (−14.9088 + 26.5352 = +11.6264R,
i.e. exactly the stated +11.62R). A gross number derived from the net number
and the fee assumption cannot independently confirm that fees caused the loss.

Recomputing gross directly from the exchange-anchored P&L — stored `pnl_usdt`
plus the modelled taker fee the live close path actually charges
(`backend/core/position_manager.py:1159-1162`, `TAKER_FEE = 0.00045`,
`backend/core/cost_model.py:38`) — gives:

| Quantity | Value |
|----------|-------|
| Net R (stored `trades.pnl_r`) | **-14.9088R** |
| Modelled taker fee (0.045%/side on entry+exit notional) | **-13.0018R** |
| **Gross R (before fees)** | **-1.8921R (-0.054R/trade)** |
| Cross-check: gross − fee | -14.8939R ≈ stored net −14.9088R ✓ |

Using the full cost model (0.11% round trip including slippage) instead:
gross −1.89R − fee 15.89R = −17.78R, which is *not* the stored net because the
live close charges only the taker fee, not slippage. Both readings point the
same way: **gross ≈ 0, and fees are the difference between flat and -14.91R.**

Excluding trade 8 (whose DB `exit_price` is the stop *trigger*, not the fill —
Binance booked it as a loss of $0.039 on a 0.25 BNB position) the gross is
**-7.2265R over 34 trades (-0.212R/trade)**.

**This is the single most decision-relevant correction in this document:** the
strategy did not have a gross edge that fees consumed. It had approximately
**no gross edge at all** in this sample. Fee reduction is necessary but not
sufficient.

### 0.3 Fee drag by risk-distance bucket, corrected

The parent's bucket table used the trailed stop, so its "tight" bucket is
mostly a trailing artefact. Rebuilt on the frozen risk basis:

| Risk basis (`initial_risk_distance` / entry) | Trades | W | Net R | Avg net R | 95% CI | **Gross R avg** | Fee R avg |
|---|---|---|---|---|---|---|---|
| < 0.20% | 3 | 1 | -2.1728 | -0.7243 | [-1.647, +0.861] | **+1.846** | 3.427 |
| 0.20 – 0.50% | 8 | 1 | -7.0385 | -0.8798 | [-1.270, -0.380] | -0.608 | 0.340 |
| 0.50 – 1.00% | 12 | 4 | -2.7623 | -0.2302 | [-0.841, +0.472] | -0.131 | 0.152 |
| ≥ 1.00% | 12 | 6 | -2.9352 | -0.2446 | [-0.655, +0.170] | -0.187 | 0.089 |

Fee drag is still concentrated: **11 trades (31.4%) with a basis < 0.5% carry
13.0020R (81.8%) of the total 15.8862R**, and 3 trades (8.6%) carry 10.2807R
(64.7%). The parent's "49% of trades had stops tighter than 0.5%" is 31.4% on
the frozen basis.

Note the < 0.20% bucket has the **best** gross (+1.846R avg) and still loses —
that is the fee-drag mechanism in its purest form, and it is exactly what the
Phase-1 stop-floor changes target.

### 0.4 Two appendix rows are mis-transcribed

The parent appendix does not match the database for two of the 35 rows
(verified by `SELECT * FROM trades WHERE mode='live'`, 35 rows):

| Row | Parent appendix | Production DB |
|-----|-----------------|---------------|
| Trade 2 (BTCUSDT LONG) | "−0.04R, +$13.93, SL_HIT, 0.01% stop" | **+2.3441R, +$13.93, SL_HIT, risk basis 0.606%** |
| Trade 20 (APEUSDT SHORT) | "+0.18R, +$0.25, SL_HIT, 0.33% stop" | **-1.0380R, -$0.36, EXTERNAL_CLOSE, risk basis 0.324%** (entry 0.1221, qty 873 — the parent duplicated trade 19's values) |

Everything else in the parent's appendix, the per-symbol table
(`:126-141`), the daily table (`:155-169`) and the weekly table (`:181-185`)
reproduces exactly from the DB — the weekly figures
(-5.7287 / -3.5167 / -5.6634R) match to 4 decimals.

---

## 1. Per-symbol breakdown (Task 12)

### 1.1 Live results, 35 trades

`fee R avg` is the cost-model round trip (0.11% of notional ÷ frozen risk
basis). `Gross R avg` is exchange-anchored (stored `pnl_usdt` + modelled taker
fee, ÷ risk basis). **The two columns use different fee models, so
`Gross − Fee ≠ Net` here** — the net/gross reconciliation in §0.2 uses the
0.09% modelled taker fee (what the live close actually charges), not the 0.11%
cost model.

| Symbol | n | W | L | Net R | Avg net R | 95% CI (bootstrap) | Gross R avg | Fee R avg | Avg risk basis | Min risk basis | Perm. p vs rest |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LINKUSDT | 5 | 3 | 2 | **+0.6691** | **+0.1338** | [-0.584, +0.752] | +0.248 | 0.139 | 0.915% | 0.475% | 0.167 |
| UNIUSDT | 3 | 1 | 2 | -0.2036 | -0.0679 | [-1.075, +0.913] | +0.040 | 0.113 | 1.096% | 0.679% | 0.513 |
| BTCUSDT | 8 | 3 | 5 | -2.3144 | -0.2893 | [-1.049, +0.648] | +0.048 | 0.408 | 0.387% | 0.121% | 0.661 |
| OPUSDT | 3 | 1 | 2 | -1.6041 | -0.5347 | [-1.048, +0.471] | -0.457 | 0.094 | 1.233% | 0.892% | 0.838 |
| SOLUSDT | 6 | 1 | 5 | -3.3822 | -0.5637 | [-1.081, +0.130] | -0.478 | 0.105 | 1.067% | 0.900% | 0.721 |
| APEUSDT | 5 | 2 | 3 | -3.1962 | -0.6392 | [-1.155, -0.117] | -0.517 | 0.152 | 0.959% | 0.324% | 0.614 |
| BNBUSDT | 5 | 1 | 4 | **-4.8774** | **-0.9755** | [-1.506, -0.135] | +0.638 | **1.984** | **0.293%** | **0.013%** | 0.174 |

Every cell is under 20 trades → **inconclusive** per `analysis/AGENTS.md:21`.
Permutation p-values (200k label shuffles) are computed against the other 30
trades; the smallest is 0.174 (BNB), far from significance.

Side-level split (parent's table reproduced exactly, so it is not re-derived
here beyond the two additions):

| Symbol | LONG | | SHORT | | Net R both sides |
|---|---|---|---|---|---|
| BNBUSDT | n=3, 1W, **-0.8291R/trade** | | n=2, 0W, **-1.1950R/trade** | | **-4.8774R** |
| BTCUSDT | n=4, 2W, **+0.1041R/trade** | | n=4, 1W, **-0.6827R/trade** | | -2.3144R |
| LINKUSDT | n=4, 2W, -0.1057R/trade | | n=1, 1W, +1.0919R/trade | | +0.6691R |
| SOLUSDT | n=3, 1W, -0.3911R/trade | | n=3, 0W, -0.7363R/trade | | -3.3822R |
| APEUSDT | n=1, 0W, -1.2201R/trade | | n=4, 2W, -0.4940R/trade | | -3.1962R |
| OPUSDT | n=1, 0W, -1.0476R/trade | | n=2, 1W, -0.2783R/trade | | -1.6041R |
| UNIUSDT | n=1, 0W, -0.0412R/trade | | n=2, 1W, -0.0812R/trade | | -0.2036R |

**BTC LONG is the only positive symbol-side cell in the live sample**
(+0.4165R over 4 trades), confirming the parent analysis. **BNB loses on both
sides** (-4.8774R over 5 trades), also confirming the parent.

### 1.2 Why BNB is the worst — and it is *not* a directional problem

BNB's exchange-anchored gross R avg is **+0.638R**, the highest in the table —
but that number is dominated by trade 8, whose DB `exit_price` is the stop
*trigger* rather than the fill (see §0.2). **Excluding trade 8, BNB's gross avg
is -0.536R over 4 trades**, i.e. BNB also lost on direction, not only on fees.

Its fee/geometry problem is nevertheless the largest in the set: average risk
basis **0.293%** versus 0.915–1.233% for the non-BTC/BNB symbols, and a
minimum of **0.0128%** (trade 8). At a 0.0128% basis the 0.11% round trip
costs **8.579R** — more than five times the entire recorded loss of that
trade.

Excluding trade 8, BNB is 4 trades, -3.2307R, -0.808R/trade, gross avg
-0.536R, fee avg 0.274R, average basis 0.363% — still the worst symbol, but
for a mix of both reasons rather than one pathological row.

The same mechanism, ranked by risk basis across all 35 trades:

| | Avg risk basis | Avg fee R |
|---|---|---|
| BNBUSDT | 0.293% | 1.984R |
| BTCUSDT | 0.387% | 0.408R |
| LINKUSDT | 0.915% | 0.139R |
| APEUSDT | 0.959% | 0.152R |
| SOLUSDT | 1.067% | 0.105R |
| UNIUSDT | 1.096% | 0.113R |
| OPUSDT | 1.233% | 0.094R |

Fee drag is a near-monotonic function of risk basis, and BTC/BNB sit an order
of magnitude tighter than the rest. **This is the mechanical link between the
Phase-1 stop-floor work and the symbol table**: a wider ATR floor
(`atr_stop_mult` 1.5 → 2.5) mechanically raises the risk basis of exactly
these two symbols the most, because their stops were the tightest.

### 1.3 The independent second sample: 45-day backtest

`analysis/backtest-45d-2026-11-05.md` replayed the same strategy over 45 days
with the deployed configuration (Run A baseline: `atr_stop_mult=1.5`,
`min_risk_pct=0.0005`) and 472 closed trades. Every per-symbol cell there has
n = 53–80, i.e. **above the 20-trade inconclusive floor**. This is a much
stronger sample than the live one and is independent of it (historical candles,
OHLC fills, different execution model).

| Symbol | Live avg net R (n) | 45d backtest `expNetR` (n) | Direction |
|---|---|---|---|
| LINKUSDT | **+0.134** (5) | **+0.119** (80) | ✅ both positive, similar magnitude |
| UNIUSDT | -0.068 (3) | **+0.152** (53) | ⚠️ live cell is n=3, inconclusive |
| SOLUSDT | -0.564 (6) | +0.004 (75) | ⚠️ conflict (backtest ≈ flat) |
| APEUSDT | -0.639 (5) | -0.054 (73) | ✅ both negative (backtest near flat) |
| OPUSDT | -0.535 (3) | -0.047 (57) | ✅ both negative (backtest near flat) |
| **BTCUSDT** | **-0.289** (8) | **-0.247** (70) | ✅ **both negative, similar magnitude** |
| **BNBUSDT** | **-0.976** (5) | **-0.242** (64) | ✅ **both negative, live 4x worse** |

Under the corrected configuration (Run B, `atr_stop_mult=2.5`,
`min_risk_pct=0.005`) the backtest flips UNI positive (+0.054, n=32) and
everything else negative — LINK flips to -0.085 (n=50). So the symbol ranking
is **config-sensitive**, which is exactly why a per-symbol config (Task 13)
must be decided against a backtest, not against 5 live trades.

**Cross-validated conclusion**: **BTCUSDT and BNBUSDT are net-negative in two
independent samples** (live n=13 combined, backtest n=134 combined) with
consistent magnitude for BTC. **LINKUSDT is net-positive in both** (live n=5,
backtest n=80). Everything else is inconclusive or conflicting.

### 1.4 The live universe has changed and is now mostly unmeasured

`SELECT symbol, enabled FROM symbols` on production right now:

| Enabled | Disabled |
|---|---|
| **APTUSDT, ARBUSDT, SOLUSDT, UNIUSDT, XRPUSDT** | AAVE, ADA, ALGO, APE, AVAX, **BNB**, **BTC**, DOGE, DOT, ETH, FIL, GRT, INJ, **LINK**, MATIC, NEAR, **OP**, STX, SUI, TEST, TIA |

`SELECT symbol, mode, COUNT(*) FROM trades GROUP BY symbol, mode` over the
**entire** database returns 35 live + 1 paper trade, all in the old 7-symbol
set. **APTUSDT, ARBUSDT and XRPUSDT have zero trades, ever.**

Consequences for any symbol recommendation:

- Recommendations about **BTC, BNB, LINK, APE, OP** are historical — they are
  already disabled, and the recommendation "disable them" has effectively been
  executed (though not through `PUT /api/settings/symbols/{symbol}`: the
  journal shows only a `GET /api/settings/symbols` on 2026-11-04 13:27 and no
  `PUT` anywhere after 2026-11-01, so the `symbols` table was changed outside
  the API).
- Recommendations about **SOL** (live n=6, -0.564R/trade; backtest n=75,
  +0.004R/trade — conflicting) and **UNI** (live n=3, -0.068R/trade; backtest
  n=53, +0.152R/trade and +0.054R under the corrected config) are the only
  live-relevant ones, and both are inconclusive on live data while the
  backtest mildly favours UNI.
- **APT, ARB and XRP carry zero evidence of any kind** — no live trades, no
  backtest run (the 45d runs covered the old 7). Enabling them is an
  unmeasured risk.

---

## 2. Session / time-of-day analysis (Task 8)

### 2.1 Method

- **Timestamp used**: `positions.opened_at` — the entry time — converted with
  `AT TIME ZONE 'UTC'`. The production PostgreSQL session timezone is
  `Europe/Berlin` (`SHOW timezone`), so any query that does not convert will
  be two hours wrong.
- **Why `opened_at` and not `signals.created_at`**: `Signal.created_at` was
  written as a naive UTC timestamp for the first rows of the sample and is
  stored two hours behind true UTC — trades 1–5 show a signal→entry gap of
  exactly 120.02–120.06 minutes while every later trade shows 0.02–0.10
  minutes (`backend/AGENTS.md`, "Signal persistence uses aware UTC
  timestamps"; the fix affects new rows only). `positions.opened_at` is aware
  UTC by construction and is used for the entry-time bucketing. A session
  filter implemented on `signals.created_at` would inherit that skew on
  historical rows.
- **Buckets used** (UTC hours of entry):

| Label | Hours (UTC) | Rationale |
|---|---|---|
| Asian | 00:00–07:59 | Tokyo/Asia liquidity window |
| London | 08:00–12:59 | London open through pre-overlap |
| London/NY overlap | 13:00–16:59 | highest-liquidity window |
| New York | 17:00–21:59 | NY session proper |
| Late / quiet | 22:00–23:59 | post-NY |

The brief for this task suggested "Asian 00-08, London 08-16, NY 13-21 UTC
roughly"; the parent analysis only records that session filters are "not
currently implemented" (`analysis/production-trade-analysis-2026-11-05.md:393-398`).
The buckets above are finer and non-overlapping, and the coarse "London 08-16"
grouping is also reported because it is the only one that separates the sample
at all.

### 2.2 Session results

`Gross R avg` is exchange-anchored and `Fee R avg` is the 0.09% modelled taker
fee (the same pair as §1.1); the coarse table below instead quotes the 0.11%
cost-model fee, which is why its fee column differs.

| Session | n | W | L | Net R | Avg net R | 95% CI (bootstrap) | Gross R avg | Fee R avg | Avg risk basis |
|---|---|---|---|---|---|---|---|---|---|
| Asian 00–07 | 14 | 3 | 11 | **-10.3474** | **-0.7391** | [-1.123, -0.290] | -0.060 | 0.682 | 0.757% |
| London 08–12 | 8 | 4 | 4 | -0.8243 | -0.1030 | [-0.730, +0.507] | +0.039 | 0.141 | 0.764% |
| Overlap 13–16 | 9 | 5 | 4 | **+1.0873** | **+0.1208** | [-0.539, +0.852] | +0.250 | 0.124 | 0.903% |
| NY 17–21 | 2 | 0 | 2 | -2.6377 | -1.3188 | [-1.387, -1.251] | -0.878 | 0.442 | 0.262% |
| Late 22–23 | 2 | 0 | 2 | -2.1867 | -1.0934 | [-1.139, -1.048] | -0.933 | 0.159 | 0.974% |

Coarse grouping used by the permutation tests:

| Group | n | Net R | Avg net R | Fee R avg |
|---|---|---|---|---|
| Asian 00–07 | 14 | -10.3474 | -0.7391 | 0.834 |
| Non-Asian 08–23 | 21 | -4.5614 | -0.2172 | 0.201 |
| **London-core 08–16** | **17** | **+0.2630** | **+0.0155** | 0.161 |

### 2.3 Hour-of-day detail

| Entry hour (UTC) | n | W | L | Net R | Avg net R | Trades |
|---|---|---|---|---|---|---|
| 00 | 1 | 0 | 1 | -1.0380 | -1.0380 | APE SHORT |
| 01 | 3 | 0 | 3 | -2.8157 | -0.9386 | SOL LONG, BNB LONG, SOL SHORT |
| 02 | 1 | 0 | 1 | -1.2318 | -1.2318 | BTC LONG |
| 03 | 2 | 1 | 1 | -0.2144 | -0.1072 | BTC SHORT, UNI SHORT |
| 04 | 2 | 0 | 2 | -2.3249 | -1.1625 | APE LONG, BTC SHORT |
| 05 | 4 | 2 | 2 | -1.2293 | -0.3073 | SOL LONG, APE SHORT, UNI SHORT, SOL SHORT |
| 06 | 0 | | | | | no trades |
| 07 | 1 | 0 | 1 | -1.4933 | -1.4933 | BNB LONG |
| 08 | 0 | | | | | no trades |
| 09 | 2 | 1 | 1 | -0.6017 | -0.3009 | BTC LONG, LINK LONG |
| 10 | 2 | 1 | 1 | +0.4630 | +0.2315 | BNB LONG, LINK LONG |
| 11 | 0 | | | | | no trades |
| 12 | 4 | 2 | 2 | -0.6856 | -0.1714 | LINK SHORT, BTC SHORT, OP SHORT, APE SHORT |
| 13 | 5 | 3 | 2 | **+2.8617** | +0.5723 | UNI LONG, BTC LONG, BTC LONG, SOL LONG, OP SHORT |
| 14 | 1 | 1 | 0 | +0.2296 | +0.2296 | LINK LONG |
| 15 | 1 | 0 | 1 | -1.0463 | -1.0463 | SOL SHORT |
| 16 | 2 | 1 | 1 | -0.9577 | -0.4789 | APE SHORT, LINK LONG |
| 17 | 0 | | | | | no trades |
| 18 | 1 | 0 | 1 | -1.3868 | -1.3868 | BTC SHORT |
| 19 | 1 | 0 | 1 | -1.2509 | -1.2509 | BNB SHORT |
| 20, 21, 22 | 0 | | | | | no trades |
| 23 | 2 | 0 | 2 | -2.1867 | -1.0934 | OP LONG, BNB SHORT |

Hour 13 (n=5, +0.572R avg) is the best hour and the largest sample among the
hours; hours 01 and 04 are the worst (n=3 and n=2, both ~-1R avg). Every hour
cell is far below 20 trades.

### 2.4 Statistical tests

Permutation test on the difference of means (200,000 label shuffles, seed
fixed):

| Comparison | Diff (avg R) | p | Verdict |
|---|---|---|---|
| Asian 00–07 vs all others | -0.5219 | 0.125 | not significant |
| Asian 00–07 **excluding trade 8** vs all others | -0.4521 | 0.193 | not significant |
| **London-core 08–16 vs Asian 00–07** | **+0.7546** | **0.034** | raw p < 0.05, not significant after correction |

The London-core vs Asian comparison is the **only** result in this document at
p < 0.05. It does not survive multiplicity correction: the number of slices
examined (7 symbols + 14 symbol-sides + 17 hours + 5 sessions + 7 weekdays ≈
50) gives a Bonferroni threshold of α' = 0.001, against which p = 0.034 is
ordinary. It is also **fragile**: removing the single trade with the 0.0128%
risk basis (trade 8, BNB LONG, 01:53 UTC, Asian) moves the Asian cell from
-0.739R to -0.669R and the p-value from 0.125 to 0.193 — i.e. the effect
survives the removal but its significance does not.

Power: with SD(R) = 0.9837, detecting the observed -0.43R/trade expectancy at
80% power and α = 0.05 needs **83 trades per group**; detecting the
session gap of 0.74R/trade needs **28 per group**. The sample has 14 and 17.

### 2.5 The larger-N cross-check: 174,632 signals

Because trade counts are tiny, the same window was re-examined at signal
level — the population the scanner actually saw:

| Session | Signals (2026-10-20 → 2026-11-05) | TRIGGERED | Trigger rate | SKIPPED | Skip rate |
|---|---|---|---|---|---|
| Asian 00–07 | 54,412 | 14 | 0.0257% | 98 | 0.180% |
| London 08–12 | 38,016 | 10 | 0.0263% | 9 | 0.024% |
| Overlap 13–16 | 29,188 | 7 | 0.0240% | 0 | 0.000% |
| NY 17–21 | 37,939 | 3 | 0.0079% | 0 | 0.000% |
| Late 22–23 | 15,077 | 2 | 0.0133% | 0 | 0.000% |

χ² = **4.82** on 4 df (critical value 9.49 at α = 0.05) → **p ≈ 0.30**. The
signal→trade conversion rate is statistically indistinguishable across
sessions. Two observations worth recording:

- **The Asian session is the least active and the most filtered.** It yields the
  **fewest signals per hour** (54,412 / 8 h = 6,801/h versus 7,297–7,603/h
  elsewhere) and carries by far the **highest filter-rejection rate**
  (98 of 54,412 = **0.180% SKIPPED**, versus 0.024% in London and 0.000% in
  the other three sessions). So the regime/trend filters reject roughly 7–8×
  more of what Asia produces — and the 14 that survive are the worst trades in
  the sample. **Hypothesis, not established**: Asian-session setups are
  lower-quality survivors of a more permissive filter. (The alternative
  reading — that Asia is simply quieter and the filters reject fewer *absolute*
  signals — is not supported: 98 absolute skips is the largest count of any
  session.)
- Signal volume per hour is roughly flat across the day (~6,800–7,600/h), so the
  scanner is not blind in any session; the sample imbalance (14 Asian trades
  vs 2 NY trades) reflects *which setups passed the filters and then filled*,
  not a scan-rate artefact.

### 2.6 Weekday view (hypothesis only)

| Day | n | W | Net R | Avg net R |
|---|---|---|---|---|
| Mon | 5 | 1 | -2.8617 | -0.5723 |
| Tue | 9 | 4 | -0.0978 | -0.0109 |
| Wed | 10 | 5 | -3.1670 | -0.3167 |
| Thu | 5 | 1 | -2.9784 | -0.5957 |
| Fri | 1 | 0 | -1.2509 | -1.2509 |
| **Sat** | 3 | 0 | **-4.2791** | **-1.4264** |
| Sun | 2 | 1 | -0.2739 | -0.1370 |
| **Sat+Sun** | 5 | 1 | -4.5530 | -0.9106 |

**Hypothesis only** — 5 weekend trades, 1 win. The three Saturday trades are
all from 2026-10-24 and are all BNB (LONG, LONG, SHORT). Note that trade 8
(the 0.0128% basis, 8.58R fee) and trade 9 are both Saturday Asian-session BNB
trades, so the "weekend is bad" reading is largely the same two rows as the
"Asian is bad" reading. Do not build a weekend filter on this.

### 2.7 Verdict on the session question

**Insufficient evidence. Do not implement a session filter from this data.**

The one encouraging result (London-core 08–16 UTC, +0.0155R/trade over 17
trades vs Asian 00–07, -0.7391R/trade over 14) is at raw p = 0.034 against a
Bonferroni threshold of ~0.001, rests on a sample where every cell is below
the 20-trade inconclusive floor set by `analysis/AGENTS.md:21`, and is
partly explained by one pathological row. The independent 174k-signal test
finds no session effect at all (χ² p ≈ 0.30).

The honest description of the sample: **winners and losers both occur across
most hours of the day.** Winners appear in hours 03, 05, 09, 10, 12, 13, 14
and 16 UTC — 8 of the 17 hours in which a trade was taken. Losers appear in
hours 00, 01, 02, 03, 04, 05, 07, 09, 10, 12, 13, 15, 16, 18, 19 and 23 UTC —
16 of those 17 hours; the only trade-containing hour with no loser is 14:00.
The two thin-hour buckets (NY 17–21 and Late 22–23) produced four trades, all
losers, at very tight risk bases (avg 0.262% and 0.974%) — but n=2 each.

---

## 3. Recommendations

Format: each claim carries a number with its sample size; hypotheses are
marked; nothing here is applied.

### R1 — Do NOT ship a session filter yet (Task 8 stays open)

**Evidence**: Asian 00–07 = -0.7391R/trade (n=14); London-core 08–16 =
+0.0155R/trade (n=17); permutation p = 0.034 raw, 0.001 Bonferroni threshold;
χ² on 174,632 signals p ≈ 0.30. **In-sample** exclusion of Asian hours would
have turned -14.9088R into -4.5614R over 21 trades — but that is selection on
the same 35 rows that produced the hypothesis, i.e. an upper bound, not an
estimate.

**Recommendation**: keep Task 8 open. Add a `--session` filter to
`backend/backtest.py` and measure it on the 45-day window (n = 472 baseline),
which has per-cell sample sizes of 53–80 — above the inconclusive floor. Only
if the backtest reproduces the session split **out of sample** should a
session setting be added to `backend/services/signal_service.py`. Add the
filter to the backtest harness **first**, never to the live path first.

### R2 — Do NOT re-enable BTCUSDT or BNBUSDT; do NOT ship a "disable" action either

**Evidence**: BTC -0.289R/trade live (n=8) and -0.247R/trade in the 45-day
backtest (n=70); BNB -0.976R/trade live (n=5) and -0.242R/trade in the
backtest (n=64). Both negative in two independent samples. Both are already
`enabled=false` in production.

**Recommendation**: leave them disabled. Do **not** build per-symbol
configuration (Task 13) on the live sample — 5–8 trades per symbol cannot
support it. If per-symbol configuration is built, it must be calibrated
against the backtest, whose per-symbol cells are 32–80 trades. Note that BTC
and BNB are precisely the two symbols whose risk basis is tightest
(0.387% / 0.293% vs 0.915–1.233%), so the Phase-1 stop-floor change interacts
with them: under the corrected backtest config, BTC gets *worse* (-0.180 vs
-0.247) and BNB *better* (-0.120 vs -0.242).

### R3 — LINKUSDT is the one candidate worth keeping

**Evidence**: +0.1338R/trade live (n=5) and +0.119R/trade in the 45-day
backtest baseline (n=80) — positive in both, similar magnitude. It is
currently disabled in production.

**Recommendation**: LINKUSDT is the single best-supported symbol. It is
**disabled today** while three symbols with zero evidence (APT, ARB, XRP) are
enabled. If the universe is being re-cut, LINK is the strongest candidate to
re-include — but this is a decision for the user, and it must be weighed
against the fact that LINK flipped negative (-0.085R, n=50) under the
corrected stop-floor config. Marked as a **hypothesis pending one backtest
run** with the final parameter set.

### R4 — The current enabled universe (APT, ARB, SOL, UNI, XRP) is largely unmeasured

**Evidence**: `SELECT symbol, mode, COUNT(*) FROM trades GROUP BY symbol, mode`
returns 35 live + 1 paper trade, all in the old 7-symbol set. APT, ARB and
XRP: **0 trades, ever**, and they were not in the 45-day backtest universe
either.

**Recommendation**: treat the current enabled set as an unvalidated bet.
Before any live restart, run the 45-day backtest on the *enabled* universe
(APT, ARB, SOL, UNI, XRP) so the symbols that will actually trade have at
least one out-of-sample measurement. Until then, the honest expected
expectancy of the live book is **unknown**, not "the backtest's -0.045R".

### R5 — Fee reduction is necessary but NOT sufficient; the missing thing is gross edge

**Evidence**: gross PnL over the 35 live trades is **-1.8921R (-0.054R/trade)**
(-7.2265R / -0.212R/trade excluding trade 8); modelled taker fee is
**13.0018R (0.3715R/trade)**; full cost model (0.11% round trip) is
**15.8862R (0.4539R/trade)**.

**Recommendation**: state the goal correctly. A maker entry (`hybrid` fill
mode: 0.083% round trip vs 0.11%, `backend/core/cost_model.py:70-71`) would
cut modelled fee drag from 15.8862R to ~11.99R over the same 35 trades —
**saving ≈ 3.90R, i.e. -14.91R → ≈ -11.01R (-0.315R/trade)**. That is a
material improvement and it is the right direction (Task 5; local commit
`86c379a` "maker-friendly entry at zone edge for GTX post-only fills" already
exists), **but it does not make the strategy profitable**, because gross was
already ≈ 0. Do not let a fee-reduction win be reported as an edge.

Caveat on the maker estimate: `backend/core/cost_model.py:19-27` documents that
27 of 30 consecutive live entries filled *better* than the planned midpoint,
i.e. they crossed the spread as takers — a post-only order would have been
rejected on those. A maker-entry backtest must therefore model a reduced fill
rate, not just a lower fee.

### R6 — The stop-floor work is the right lever for fee drag but the wrong lever for edge

**Evidence**: 11 trades with a risk basis < 0.5% (31.4%) carried 13.0020R
(81.8%) of the 15.8862R total fee drag; the three trades under 0.20% carried
64.7% of it. Excluding the 11 sub-0.5% trades, the sample is -5.6975R over 24
trades (-0.2374R/trade) instead of -14.9088R over 35 (-0.4260R/trade).

**Recommendation**: keep the Phase-1 stop-floor changes (Task 1/2,
`da597ca`/`a429cea`) as a **cost** control. Do **not** present them as an
edge fix, because the independent 45-day backtest measured them making
expectancy *worse* (expNetR -0.045 → -0.089, `analysis/backtest-45d-2026-11-05.md:96-103`),
and the live-sample implication above is in-sample selection. Record the
conflict; the user's deploy decision stands.

### R7 — Fix the fee-drag measurement in the analysis pipeline (documentation defect)

**Evidence**: §0.1-0.3 — the deployed analysis used `positions.stop_loss`
(mutable) instead of `positions.initial_risk_distance` (frozen) and derived
gross PnL circularly. Fee drag came out 10.65R too high (the parent's 26.5352R
is 67% above the corrected 15.8862R), and a gross edge of +11.62R was reported
where the exchange-anchored figure is -1.89R.

**Recommendation**: any future trade-analysis artifact must compute fee drag
as `entry × 0.0011 / initial_risk_distance` and gross as
`pnl_usdt + modelled_fees`, both from `trades` joined to `positions`. Add this
as a note to `analysis/production-trade-analysis-2026-11-05.md` so the parent
document is not read as the source of truth for the fee-drag figure.

---

## 4. Data limitations

1. **35 trades is a small sample.** SD(R) = 0.9837. At 80% power and α = 0.05
   the sample cannot resolve a -0.43R/trade expectancy without ~83 trades per
   group. Every cell in every table here is below the 20-trade inconclusive
   floor set by `analysis/AGENTS.md:21`, with the single exception of the
   cross-referenced 45-day backtest cells (n = 32–80).
2. **Multiplicity.** ~50 slices were examined (7 symbols, 14 symbol-sides, 17
   hours, 5 sessions, 7 weekdays). At raw p = 0.05, one or two "significant"
   results are expected by chance. The Bonferroni threshold is ≈ 0.001.
3. **The regime changed on 2026-11-05.** Since the sample ends: `rr_min`
   4.0 → 3.0, `trade_min_trend_strength` STRONG → MODERATE (both hot-reloaded
   in production); the ATR floor 1.5 → 2.5 and the `min_risk_pct` floor
   0.05% → 0.5% are local-only (`da597ca`/`a429cea`, not deployed). Every
   number in this document describes the **old** configuration.
4. **The universe changed.** The 35 trades came from BTC/BNB/SOL/APE/UNI/LINK/OP
   (plus XRP/APT/ARB signals without trades). The enabled set is now
   APT/ARB/SOL/UNI/XRP. Symbol-level conclusions do not transfer.
5. **The scanner was halted for part of the window.** The weekly guard tripped
   on 2026-11-04 at -5.6634R (week starting Nov 2). Trades are therefore
   missing after that point, and the absence of trades in the last sessions is
   a policy outcome, not a market observation.
6. **Gross PnL is an estimate, not a ledger.** It is derived from the stored
   `pnl_usdt` plus the modelled taker fee. For the 12 trades whose stop was
   trailed, `positions.exit_price` is the stop *trigger*, not the fill —
   clearest on trade 8, where the stored exit (740.245) sits *above* the
   739.78 LONG entry while Binance booked a $0.039 loss (an effective exit
   near 739.62). At trade 8's 0.0128% risk basis a $0.039 loss is 1.65R and
   the modelled fee alone is 7.02R, so every gross figure for that row is
   dominated by its tiny denominator. Gross numbers here are therefore
   **upper bounds** for trailed trades. The **net** R figures are
   exchange-corrected where the 2026-10-25 live-close fix applied (trades 9,
   10, 11 and later) and are the authoritative quantity.
7. **Trade 8 is a known data artefact** and is called out wherever it moves a
   number. It should be flagged in the trade log.
8. **Signal timestamps are skewed by 2h for the first 5 rows** of the sample
   (naive-UTC write, `backend/AGENTS.md`). A session filter keyed on
   `signals.created_at` must convert explicitly and must not trust pre-fix
   rows.
9. **No paper-mode corroboration** — the database contains exactly 1 paper
   trade, so there is no second live-like sample from the same period.
10. **The 45-day backtest is a different execution model** (OHLC fills, no
    queue position, no shared portfolio slots, no
    `PositionManager._check_profit_trailing_2`). It is a strong independent
    sample for *ranking*, not for absolute levels.

---

## Appendix A — Per-trade table (production, `mode='live'`)

Risk basis % = `initial_risk_distance / entry_price`. Gross R is
exchange-anchored (stored `pnl_usdt` + modelled taker fee) ÷ risk basis. Fee R
uses the 0.11% cost model ÷ risk basis. Entry time is `positions.opened_at`
in UTC.

| ID | Symbol | Side | Entry (UTC) | Session | Day | Risk basis % | Net R | Gross R (ex-fee) | Fee R (0.11%) | Exit |
|----|--------|------|------------|---------|-----|---------------|-------|-------------------|---------------|------|
| 1 | UNIUSDT | LONG | 10-20 13:46 | Overlap | Tue | 0.679 | -0.0412 | +0.133 | 0.162 | SL_HIT |
| 2 | BTCUSDT | LONG | 10-20 13:46 | Overlap | Tue | 0.606 | +2.3441 | +2.494 | 0.182 | SL_HIT |
| 3 | OPUSDT | LONG | 10-21 23:00 | Late | Wed | 1.604 | -1.0476 | -0.992 | 0.069 | SL_HIT |
| 4 | SOLUSDT | LONG | 10-22 01:40 | Asian | Thu | 0.900 | -1.0829 | -0.983 | 0.122 | SL_HIT |
| 5 | BTCUSDT | LONG | 10-22 02:19 | Asian | Thu | 0.313 | -1.2318 | -0.943 | 0.351 | SL_HIT |
| 7 | BNBUSDT | SHORT | 10-23 19:19 | NY | Fri | 0.384 | -1.2509 | -1.021 | 0.286 | SL_HIT |
| 8 | BNBUSDT | LONG | 10-24 01:53 | Asian | Sat | 0.013 | -1.6467 | +5.334 | 8.579 | SL_HIT |
| 9 | BNBUSDT | LONG | 10-24 07:02 | Asian | Sat | 0.212 | -1.4933 | -1.080 | 0.518 | SL_HIT |
| 10 | BNBUSDT | SHORT | 10-24 23:31 | Late | Sat | 0.343 | -1.1391 | -0.875 | 0.320 | SL_HIT |
| 11 | BTCUSDT | SHORT | 10-25 03:17 | Asian | Sun | 0.121 | +0.8607 | +1.610 | 0.909 | SL_HIT |
| 12 | UNIUSDT | SHORT | 10-26 03:42 | Asian | Mon | 1.596 | -1.0751 | -1.018 | 0.069 | SL_HIT |
| 13 | SOLUSDT | LONG | 10-26 05:02 | Asian | Mon | 1.080 | -1.0988 | -1.017 | 0.102 | SL_HIT |
| 14 | BTCUSDT | LONG | 10-26 09:20 | London | Mon | 0.363 | -1.2735 | -1.019 | 0.303 | SL_HIT |
| 15 | LINKUSDT | LONG | 10-26 09:29 | London | Mon | 1.267 | +0.6718 | +0.741 | 0.087 | SL_HIT |
| 16 | BNBUSDT | LONG | 10-27 10:46 | London | Tue | 0.510 | +0.6526 | +0.833 | 0.216 | SL_HIT |
| 17 | LINKUSDT | SHORT | 10-27 12:39 | London | Tue | 0.779 | +1.0919 | +1.211 | 0.141 | SL_HIT |
| 18 | BTCUSDT | SHORT | 10-27 12:23 | London | Tue | 0.565 | -1.1000 | -0.946 | 0.195 | SL_HIT |
| 19 | APEUSDT | SHORT | 10-27 16:18 | Overlap | Tue | 1.592 | +0.1769 | +0.233 | 0.069 | SL_HIT |
| 20 | APEUSDT | SHORT | 10-28 00:20 | Asian | Wed | 0.324 | -1.0380 | -0.765 | 0.340 | EXTERNAL_CLOSE |
| 21 | SOLUSDT | SHORT | 10-27 15:20 | Overlap | Tue | 1.105 | -1.0463 | -0.966 | 0.100 | SL_HIT |
| 22 | APEUSDT | SHORT | 10-28 05:46 | Asian | Wed | 1.108 | +0.0334 | +0.114 | 0.099 | SL_HIT |
| 23 | OPUSDT | SHORT | 10-28 12:14 | London | Wed | 1.203 | +0.4709 | +0.546 | 0.091 | SL_HIT |
| 25 | BTCUSDT | LONG | 10-28 13:49 | Overlap | Wed | 0.296 | +0.5777 | +0.892 | 0.371 | SL_HIT |
| 26 | SOLUSDT | LONG | 10-28 13:48 | Overlap | Wed | 0.907 | +1.0085 | +1.108 | 0.121 | SL_HIT |
| 27 | LINKUSDT | LONG | 10-28 14:17 | Overlap | Wed | 1.317 | +0.2296 | +0.297 | 0.084 | SL_HIT |
| 28 | UNIUSDT | SHORT | 10-29 05:12 | Asian | Thu | 1.014 | +0.9127 | +1.006 | 0.109 | SL_HIT |
| 29 | LINKUSDT | LONG | 10-29 10:47 | London | Thu | 0.475 | -0.1896 | +0.002 | 0.232 | SL_HIT |
| 30 | BTCUSDT | SHORT | 10-29 18:01 | NY | Thu | 0.139 | -1.3868 | -0.734 | 0.793 | SL_HIT |
| 31 | LINKUSDT | LONG | 11-01 16:38 | Overlap | Sun | 0.735 | -1.1346 | -1.011 | 0.150 | SL_HIT |
| 32 | SOLUSDT | SHORT | 11-02 01:25 | Asian | Mon | 1.046 | -0.0861 | -0.003 | 0.105 | SL_HIT |
| 33 | APEUSDT | SHORT | 11-03 12:16 | London | Tue | 0.948 | -1.1484 | -1.056 | 0.116 | SL_HIT |
| 34 | OPUSDT | SHORT | 11-03 13:23 | Overlap | Tue | 0.892 | -1.0274 | -0.926 | 0.123 | SL_HIT |
| 35 | APEUSDT | LONG | 11-04 04:13 | Asian | Wed | 0.821 | -1.2201 | -1.109 | 0.134 | SL_HIT |
| 36 | BTCUSDT | SHORT | 11-04 04:54 | Asian | Wed | 0.691 | -1.1048 | -0.973 | 0.159 | SL_HIT |
| 37 | SOLUSDT | SHORT | 11-04 05:40 | Asian | Wed | 1.365 | -1.0766 | -1.007 | 0.081 | SL_HIT |

## Appendix B — Reproducing the numbers

All queries are read-only `SELECT`s against `sudo -u postgres psql -d fubot`
on `contabo01`. The PostgreSQL session timezone is `Europe/Berlin`, so every
time expression must be converted with `AT TIME ZONE 'UTC'`.

```sql
-- B1. Per-trade source of truth (fee basis, entry time, exit time)
SELECT tr.id, tr.symbol, tr.side, tr.pnl_r, tr.pnl_usdt, tr.exit_reason,
       tr.strategy_type, tr.mode,
       p.opened_at  AT TIME ZONE 'UTC' AS entry_utc,
       t.closed_at  AT TIME ZONE 'UTC' AS close_utc,
       EXTRACT(HOUR   FROM p.opened_at AT TIME ZONE 'UTC')::int AS entry_hour_utc,
       EXTRACT(ISODOW FROM p.opened_at AT TIME ZONE 'UTC')::int AS entry_dow_utc,
       p.initial_risk_distance, p.stop_loss, p.take_profit,
       (tr.entry_price * 0.0011) / p.initial_risk_distance           AS fee_r_costmodel,
       (tr.entry_price * 0.0011) / ABS(tr.entry_price - p.stop_loss)  AS fee_r_TRAILED_STOP,
       (CASE WHEN tr.side='LONG' THEN (tr.exit_price-tr.entry_price)*tr.quantity
             ELSE (tr.entry_price-tr.exit_price)*tr.quantity END)
         / (tr.quantity * p.initial_risk_distance)                    AS gross_r_price,
       (tr.pnl_usdt + (tr.entry_price+tr.exit_price)*tr.quantity*0.00045)
         / (tr.quantity * p.initial_risk_distance)                    AS gross_r_exchange_anchored
FROM trades tr JOIN positions p ON p.id = tr.position_id
WHERE tr.mode = 'live'
ORDER BY tr.id;

-- B2. Per-symbol and per-session aggregates (session CASE expression below)
-- B3. Signal-level trigger rate by session (n = 174,632 over the window)
SELECT CASE WHEN EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int BETWEEN 0 AND 7  THEN 'Asian 00-07'
            WHEN EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int BETWEEN 8 AND 12 THEN 'London 08-12'
            WHEN EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int BETWEEN 13 AND 16 THEN 'Overlap 13-16'
            WHEN EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::int BETWEEN 17 AND 21 THEN 'NY 17-21'
            ELSE 'Late 22-23' END AS sess,
       COUNT(*) AS signals,
       COUNT(*) FILTER (WHERE status='TRIGGERED') AS triggered,
       COUNT(*) FILTER (WHERE status='SKIPPED')   AS skipped
FROM signals
WHERE created_at >= '2026-10-20' AND created_at < '2026-11-05'
GROUP BY 1;

-- B4. Current enabled universe (read-only; use the settings/symbols endpoint, not UPDATE)
SELECT symbol, enabled, max_leverage, position_limit_usdt FROM symbols ORDER BY symbol;

-- B5. Trade history for every symbol/mode (shows APT/ARB/XRP have none)
SELECT symbol, mode, COUNT(*) AS trades,
       MIN(closed_at AT TIME ZONE 'UTC') AS first, MAX(closed_at AT TIME ZONE 'UTC') AS last
FROM trades GROUP BY symbol, mode ORDER BY symbol, mode;
```

**Statistical method**: two-sample permutation test on the difference of
means (200,000 label shuffles, fixed seed), bootstrap 95% CI (20,000
resamples, fixed seed), and a Pearson χ² goodness-of-fit on the signal
trigger rates. Pure standard-library Python; no numpy/scipy in the local
environment.

---

**Document status**: complete.
**Next review**: after the 45-day backtest is re-run on the *enabled*
universe (APT, ARB, SOL, UNI, XRP) and with a session filter added to the
harness (R1, R4).
**Owner**: FUBOT project team.
