# FUBOT — Net-of-Fees Performance Analysis (2026-11-03)

**Scope.** Every figure below is fill-level **net-of-fees truth reconstructed from Binance**
(`/fapi/v1/income` + `/fapi/v1/userTrades`, BNB commissions converted to USDT), **not** the
database baseline. Production read-only queries were run against `contabo01` (Postgres on the
host at `127.0.0.1:5432`, database `fubot`). No source file and no production row was modified.

- Population: bot window from **2026-10-20 13:40 UTC** (first bot entry fee) to **2026-11-03**.
- Independent trades: **33** (Binance fill-matched). DB rows: **34**.
- Risk unit: `1R = positions.initial_risk_distance x quantity` (frozen at open), never the
  trailed live stop — see §9.

---

## 1. Headline

| Metric | Value | Notes | Confidence |
|---|---|---|---|
| DB trade rows | **34** | `trades` table, all modes | High (direct count) |
| Binance independent trades | **33** | row 24 = PHANTOM (no fill match) | High |
| DB net P&L (33 comparable rows) | **-12.42 USDT** | sum of `trades.pnl_usdt` excl. row 24 | High |
| DB net P&L (all 34 rows) | -11.64 USDT | includes phantom +0.78 | High |
| **Binance net P&L (truth)** | **-13.486 USDT** | gross + entry fee + exit fee + funding | High |
| Gross realized (truth) | **-7.637 USDT** | `REALIZED_PNL` from fills | High |
| Commissions (truth) | **-5.841 USDT** | entry + exit, BNB -> USDT | High |
| Funding (truth) | **-0.007 USDT** | `FUNDING_FEE`, negligible but included | High |
| DB win rate | **12/33 (36%)** | `pnl_usdt > 0` | High |
| Truth win rate | **11/33 (33.3%)** | net-of-fees | High |
| Mean net R | **-0.420** | t = **-2.44** (n=33) | Medium (small n) |
| Mean gross R | **-0.034** | fees turn a ~flat book negative | Medium |
| Avg winner / avg loser | **+0.766 R / -1.013 R** | asymmetry, not win count, is the killer | Medium |
| Break-even win rate required | **56.9%** | vs 33.3% achieved | High (algebra) |
| DB overstatement of net P&L | **+1.066 USDT** | DB -12.42 vs truth -13.486 | High |

### The previously-quoted baseline was WRONG

An earlier summary of this work quoted **"33 trades / -10.64 USDT / 39% win rate"**. That
baseline is wrong on all three counts and must not be reused:

1. **34 DB rows** exist, not 33 — row 24 is a phantom (duplicate of position 23, no Binance
   fill match, `signal_id IS NULL`, `strategy_type='UNKNOWN'`).
2. **DB total is -12.42 USDT** over the 33 comparable rows (-11.64 across all 34 rows),
   not -10.64.
3. **True win rate is 33.3%** (11/33) net of fees; the DB's own `pnl_usdt > 0` count is 36%
   (12/33) because the DB overstates P&L.

**Why the DB overstates by +1.066 USDT:** the stored `trades.pnl_usdt` was computed with an
incomplete fee formula on many rows, so the DB **omits one or both commission legs** on a large
share of the book.

- **24 of 33 rows** are overstated at all; **18 rows** are overstated by more than **0.01 USDT**
  (+1.654 USDT in aggregate among those 18).
- **16 rows store a formula that omits one or both commission legs** outright (the fitted
  per-row formula is `gross + entry fee` only, or `gross + exit fee` only, or `gross` only);
  **15 of the 18 material rows** are in that group.
- The other 3 material rows (trades 5, 7, 29) use the complete net formula but carry a residual
  from the BNB->USDT conversion / fill-price differences.
- Netting the single row that is **understated** by -0.586 (trade 3, OPUSDT) against the
  overstatements gives the **+1.066 USDT** headline gap.

No row reconciles exactly: **0 of 33** have `db == truth` to the cent.

---

## 2. The primary defect: the exit design, not symbol selection

**This is the headline finding. Symbol selection is secondary.**

Across all **33** independent exits the `exit_reason` distribution is:

| exit_reason | n | share |
|---|---|---|
| SL_HIT | **32** | 97.0% |
| EXTERNAL_CLOSE | **1** | 3.0% |
| TP_HIT | **0** | **0.0%** |

(Take-profit was *set* on 33 of 34 positions — `take_profit IS NOT NULL` — so targets existed and
were simply never reached.)

Consequences:

- **Zero TP_HITs in 33 trades.** Every trade that was allowed to run ended on a stop, either
  the original stop or a trailed one.
- **Winners are capped at +0.766 R on average** while **losers average -1.013 R**. Trailing
  cannot run a winner: the trail moves the stop toward profit and then the stop takes it out.
- Live settings confirm the mechanism (production `settings` table):
  `be_trail_trigger_r = 0.5`, `profit_lock_trigger_r = 0.5`, `profit_lock_trail_r = 0.5`,
  `profit_lock_trigger_r2 = 1.0`, `profit_lock_trail_r2 = 0.25`.
  i.e. **BE-trail at +0.5 R**, plus a **profit-lock at +1.0 R with a 0.25 R trail** — a winner
  is closed for roughly +0.25 R once the trigger fires.
- Expectancy arithmetic: with avg winner +0.766 R and avg loser -1.013 R, break-even needs
  **56.9%** win rate; the strategy achieved **33.3%**. At 33.3% wins the expected value per
  trade is **-0.420 R**.
- Fees are the second-order problem: the gross book is roughly flat (**-0.034 R mean gross**),
  and commissions turn it into **-0.420 R net**. But fixing the fee model on a strategy whose
  exits cap winners below its losers does not produce a profitable book.

### The "inverted stop" in SQL is NOT a bug

A naive read of `positions.stop_loss` looks inverted (on 16 of 34 positions the stop sits on
the *profitable* side of entry). That is a **trailing artifact, not corruption**: trailing
mutates `positions.stop_loss` toward profit at close time. `risk_units.risk_distance()`
deliberately scores R against the **frozen `initial_risk_distance`** written once at open,
precisely so a trailed stop is never used to price a loss.

Verification: recomputing `pnl_usdt / (initial_risk_distance * quantity)` reproduces the stored
`pnl_r` on **23 of 31** rows to within 0.005; the 8 rows that differ (max deviation **0.0412**,
trade 1) differ only because `pnl_usdt` is stored at `numeric(18,2)` — the underlying value has
more precision than the stored P&L. Meanwhile the **live** stop distance deviates from frozen
risk by up to **4.93x** (trade 8) and is negative (profitable-side) on 16 rows. Scoring R from
the live stop would be materially wrong; scoring from frozen risk is correct.

---

## 3. Full 34-row reconciliation (DB vs Binance truth)

Columns: `tid | symbol | side | DB pnl (trades.pnl_usdt) | truth net | gross | fee (entry+exit)
| funding | DB R (stored) | true R | risk$ (1R) | fee formula stored`.

Rounding: truth net/gross/fee to 4 dp, R to 3 dp, risk to 3 dp.

| tid | symbol | side | DB pnl | truth net | gross | fee | funding | DB R | true R | risk$ | fee formula stored |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | UNIUSDT | LONG | 0.00 | -0.0085 | -0.0020 | -0.0065 | +0.0000 | -0.0412 | -0.173 | 0.049 | gross only |
| 2 | BTCUSDT | LONG | 13.93 | 13.4139 | 14.3040 | -0.8901 | +0.0000 | 2.3441 | 2.257 | 5.942 | -exit fee; residual 0.07 |
| 3 | OPUSDT | LONG | -8.83 | -8.2445 | -7.7866 | -0.4698 | +0.0120 | -1.0476 | -0.978 | 8.426 | net; residual 0.59 |
| 4 | SOLUSDT | LONG | -4.57 | -4.6975 | -4.2777 | -0.4198 | +0.0000 | -1.0829 | -1.113 | 4.220 | -exit fee; residual 0.08 |
| 5 | BTCUSDT | LONG | -1.92 | -1.9508 | -1.5030 | -0.4478 | +0.0000 | -1.2318 | -1.249 | 1.561 | net; residual 0.03 |
| 6 | LINKUSDT | LONG | -4.25 | -4.5266 | -4.1778 | -0.3488 | +0.0000 | -1.1300 | -1.203 | 3.762 | paper model; residual 0.07 |
| 7 | BNBUSDT | SHORT | -0.93 | -0.9434 | -0.7696 | -0.1738 | +0.0000 | -1.2509 | -1.274 | 0.741 | net; residual 0.01 |
| 8 | BNBUSDT | LONG | -0.04 | -0.0390 | 0.1275 | -0.1665 | +0.0000 | -1.6467 | -1.646 | 0.024 | net |
| 9 | BNBUSDT | LONG | -0.59 | -0.5862 | -0.4200 | -0.1662 | +0.0000 | -1.4933 | -1.493 | 0.393 | net |
| 10 | BNBUSDT | SHORT | -0.72 | -0.7212 | -0.5550 | -0.1662 | +0.0000 | -1.1391 | -1.139 | 0.633 | net |
| 11 | BTCUSDT | SHORT | 0.17 | 0.1687 | 0.3144 | -0.1457 | +0.0000 | 0.8607 | 0.861 | 0.196 | net |
| 12 | UNIUSDT | SHORT | -1.64 | -1.6408 | -1.5540 | -0.0868 | +0.0000 | -1.0751 | -1.075 | 1.526 | net |
| 13 | SOLUSDT | LONG | -2.06 | -2.0578 | -1.9323 | -0.1083 | -0.0172 | -1.0988 | -1.099 | 1.873 | net |
| 14 | BTCUSDT | LONG | -0.74 | -0.7444 | -0.5998 | -0.1446 | +0.0000 | -1.2735 | -1.274 | 0.585 | net |
| 15 | LINKUSDT | LONG | 0.96 | 0.9636 | 1.0660 | -0.1024 | +0.0000 | 0.6718 | 0.672 | 1.434 | net |
| 16 | BNBUSDT | LONG | 0.62 | 0.5329 | 0.7000 | -0.1671 | +0.0000 | 0.6526 | 0.564 | 0.944 | -exit fee |
| 17 | LINKUSDT | SHORT | 0.90 | 0.8490 | 0.9435 | -0.0945 | +0.0000 | 1.0919 | 1.034 | 0.821 | -entry fee |
| 18 | BTCUSDT | SHORT | -0.50 | -0.5334 | -0.4611 | -0.0723 | +0.0000 | -1.1000 | -1.180 | 0.452 | -entry fee |
| 19 | APEUSDT | SHORT | 0.25 | 0.2107 | 0.2908 | -0.0801 | +0.0000 | 0.1769 | 0.149 | 1.418 | -exit fee |
| 20 | APEUSDT | SHORT | -0.36 | -0.3580 | -0.2619 | -0.0961 | +0.0000 | -1.0380 | -1.038 | 0.345 | net |
| 21 | SOLUSDT | SHORT | -1.80 | -1.7967 | -1.7248 | -0.0988 | +0.0269 | -1.0463 | -1.046 | 1.717 | net |
| 22 | APEUSDT | SHORT | 0.05 | -0.0111 | 0.1130 | -0.1241 | +0.0000 | 0.0334 | -0.007 | 1.528 | -exit fee |
| 23 | OPUSDT | SHORT | 0.78 | 0.7169 | 0.8402 | -0.1234 | +0.0000 | 0.4709 | 0.433 | 1.654 | -entry fee |
| 25 | BTCUSDT | LONG | 0.14 | 0.1015 | 0.1740 | -0.0725 | +0.0000 | 0.5777 | 0.426 | 0.238 | -exit fee |
| 26 | SOLUSDT | LONG | 1.70 | 1.6484 | 1.7848 | -0.1178 | -0.0186 | 1.0085 | 0.978 | 1.686 | -entry fee |
| 27 | LINKUSDT | LONG | 0.33 | 0.2713 | 0.3804 | -0.0988 | -0.0104 | 0.2296 | 0.188 | 1.442 | -entry fee |
| 28 | UNIUSDT | SHORT | 0.86 | 0.8141 | 0.8970 | -0.0829 | +0.0000 | 0.9127 | 0.868 | 0.938 | -entry fee |
| 29 | LINKUSDT | LONG | -0.10 | -0.1211 | 0.0000 | -0.1211 | +0.0000 | NULL | -0.190 | 0.638 | net; residual 0.02 |
| 30 | BTCUSDT | SHORT | -0.31 | -0.3834 | -0.2378 | -0.1456 | +0.0000 | -1.3868 | -1.711 | 0.224 | -exit fee |
| 31 | LINKUSDT | LONG | -1.16 | -1.1612 | -1.0363 | -0.1249 | +0.0000 | -1.1346 | -1.135 | 1.023 | net |
| 32 | SOLUSDT | SHORT | -0.09 | -0.1555 | 0.0000 | -0.1555 | +0.0000 | NULL | -0.086 | 1.805 | -entry fee; residual 0.01 |
| 33 | APEUSDT | SHORT | -1.50 | -1.4960 | -1.3717 | -0.1243 | +0.0000 | -1.1484 | -1.148 | 1.303 | net |
| 34 | OPUSDT | SHORT | -1.00 | -0.9999 | -0.9012 | -0.0987 | +0.0000 | -1.0274 | -1.027 | 0.973 | net |
| 24 | OPUSDT | SHORT | 0.78 | n/a (NO_FILL_MATCH) | n/a | n/a | n/a | 0.4362 | n/a | n/a | PHANTOM (duplicate of position 23; signal_id NULL, strategy UNKNOWN) |

**Row 24 is not a trade.** It is listed last, out of ID order, so the 33 real rows read
continuously. Position 24 opened **0.5 s after** position 23 with the identical entry price
(0.11126000) and identical quantity (1235.60000000), carries `signal_id = NULL`,
`strategy_type = 'UNKNOWN'`, `initial_risk_distance = NULL`, was closed with
`exit_reason = 'EXTERNAL_CLOSE'`, and **matches no Binance fill** — its +0.78 USDT is a copy of
trade 23's +0.78.

**Formula legend** (last column):
- `net` — stored value = gross + entry fee + exit fee (+ funding) = truth.
- `-entry fee` / `-exit fee` — stored value omits that commission leg.
- `gross only` — stored value omits **both** commission legs.
- `paper model` — paper-mode row, priced by the model, not by exchange fills.
- `; residual X.XX` — the fitted formula is the closest match but leaves a residual of X USDT
  (usually a fill-price or rounding difference).
- `PHANTOM` — no Binance fill match; row does not represent an independent trade.

---

## 4. Per-symbol verdict — ALL 7 SYMBOLS KILL

| symbol | n | truth net (USDT) | WR | mean R | t | gross mean R | verdict | confidence |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 7 | **+10.072** | 42.9% | -0.267 | -0.48 | +0.096 | **KILL** | **Low** — one +2.26 R trade (tid 2, +13.414 USDT) carries all the profit; leave-one-out gives a **-26.90 USDT** portfolio |
| UNIUSDT | 3 | -0.835 | 33.3% | -0.127 | -0.23 | -0.034 | **KILL** | **Very low** (n=3) |
| LINKUSDT | 6 | -3.725 | 50.0% | -0.106 | -0.28 | **+0.006** | **KILL** | **Low** — the gross book is essentially flat (+0.006 mean gross R), so the loss is *entirely* fees: fee-sensitive, but no edge survives the fee load |
| APEUSDT | 4 | -1.654 | 25.0% | -0.511 | -1.51 | -0.383 | **KILL** | **Low** |
| BNBUSDT | 5 | -1.757 | 20.0% | -0.998 | **-2.49** | +0.626 | **KILL** | **Medium** — the only symbol with t <= -2; gross is positive (+0.626 R) yet the book loses on 20% win rate and -1R losers |
| SOLUSDT | 5 | -7.059 | 20.0% | -0.473 | -1.15 | -0.398 | **KILL** | **Low** |
| OPUSDT | 3 | -8.528 | 33.3% | -0.524 | -1.09 | -0.447 | **KILL** | **Very low** (n=3) |

**Every one of the 7 symbols is a KILL.** Not one has a positive mean net R. And the structure
is the same everywhere: fees turn a roughly flat gross book (**-0.034 R gross mean** across all
33 trades) into **-0.420 R net**. Choosing a "better" subset cannot fix this — the defect is
in how trades are exited (and, secondarily, in how P&L is computed), not in which symbols the
bot traded.

---

## 5. Per-strategy

| strategy_type | n | truth net (USDT) | WR | mean R | t | verdict | confidence |
|---|---|---|---|---|---|---|---|
| 3CANDLE | **33** | **-13.486** | 33.3% | **-0.420** | **-2.44** | **KILL** | **Medium** (single strategy, n=33) |
| UNKNOWN | 1 | +0.78 (phantom) | n/a | n/a | n/a | **DELETE row 24** | High — no fill match, no signal, duplicate of position 23 |

---

## 6. Side and mode slices

| slice | n | truth net (USDT) | WR | mean R | t | verdict | confidence |
|---|---|---|---|---|---|---|---|
| LONG | 17 | -7.206 | 35.3% | -0.381 | -1.44 | KILL | Medium |
| SHORT | 16 | -6.280 | 31.2% | -0.462 | -2.04 | KILL | Medium |
| live only | 32 | -8.959 | 34.4% | -0.395 | -2.25 | KILL | Medium |
| paper only | 1 | -4.527 | 0.0% | -1.203 | n/a | KILL (paper) | n/a (n=1) |

Neither side nor mode separates the book. Note the paper row (trade 6) is scored
**-1.203 R** while its position is `mode='live'` in `positions` — see defect D5.

---

## 7. Binance account reconciliation (account-wide vs bot-attributed)

Account-wide totals use every `REALIZED_PNL`, `COMMISSION` and `FUNDING_FEE` event on the 7
symbols after 2026-10-20 13:40 UTC. The bot-attributed column is the sum of the 33
fill-matched trades. The residual is **manual trading on the same account** and is **not**
FUBOT performance.

| symbol | acct gross | acct fees | acct funding | **acct net** | **bot net** | unattributed (manual) |
|---|---|---|---|---|---|---|
| APEUSDT | -1.230 | -0.425 | 0.000 | -1.654 | -1.654 | 0.000 |
| BNBUSDT | -0.917 | -0.840 | 0.000 | -1.757 | -1.757 | 0.000 |
| BTCUSDT | +11.991 | -1.991 | 0.000 | +10.000 | +10.072 | -0.073 |
| LINKUSDT | -2.824 | -0.890 | -0.010 | -3.725 | -3.725 | 0.000 |
| OPUSDT | -7.848 | -0.692 | +0.012 | -8.528 | -8.528 | 0.000 |
| SOLUSDT | -6.016 | -1.756 | -0.009 | -7.781 | -7.059 | **-0.722** |
| UNIUSDT | -0.661 | -0.183 | 0.000 | -0.844 | -0.835 | -0.009 |
| **TOTAL** | **-7.505** | **-6.776** | **-0.007** | **-14.289** | **-13.486** | **-0.803** |

- **Account net -14.289 USDT** vs **bot net -13.486 USDT** vs **unattributed -0.803 USDT**
  (manual trading; **SOLUSDT -0.722** dominant).
- **Do not attribute manual trades to FUBOT.** Any report that quotes the account-wide
  -14.289 as bot performance is wrong by -0.803 USDT.
- Funding total is **-0.007 USDT** — negligible, but it is included in every net figure here.

---

## 8. The 7 evidence-backed defects

| # | Defect | Evidence (verified) | Impact |
|---|---|---|---|
| D1 | **Net-of-fees breakage**: `trades.pnl_usdt` omits commission legs | 24 of 33 rows overstated, **18 by >0.01 USDT**; **16 rows** store a formula omitting one or both legs (15 of them material); net gap **+1.066 USDT** | DB overstates P&L; every per-symbol/strategy verdict computed from the DB is wrong |
| D2 | **Entry-fee window is too wide**: `position_income_start_ms()` looks back **5 minutes** (300 s) before `opened_at` and has **no end bound** | Actual entry-fill-to-position-open gap: **31 of 33 gaps are <= 2.33 s** (min **0.349 s**, median **0.405 s**); only two outliers — 2.601 s (tid 5) and 24.049 s (tid 1). A 300 s lookback is **~130x the median gap** (and up to ~860x the minimum), so it swallows unrelated commissions: **67 pre-entry COMMISSION events worth 3.785 USDT** sit inside the 33 windows, and **13 rows** carry commission events that are not their own entry fee (excess **-0.934 USDT**) | Fees attributed to the wrong trade; residual and unexplained deltas on otherwise-"net" rows |
| D3 | **`trades.id != trades.position_id` on 9 rows** | Rows 1,2,17,18,19,20,21,25,26 point at a different position id than their own. The `position_id` links are **correct** (symbol and side match the position they point to, verified on all 9) — the two ids were simply assigned out of order | Any join on `trades.id = positions.id` silently mismatches; the join key must be `trades.position_id` |
| D4 | **Phantom position + trade 24** | Position 24: `signal_id NULL`, `strategy_type 'UNKNOWN'`, `initial_risk_distance NULL`, opened 0.5 s after position 23 with identical entry/quantity, `exit_reason 'EXTERNAL_CLOSE'`, **no Binance fill match**; trade 24 books +0.78 (duplicate of trade 23) with a stored `pnl_r` of 0.4362 that cannot be recomputed (NULL risk distance) | Inflates trade count and DB P&L; a stored R that cannot be reproduced |
| D5 | **Mode contradiction on trade 6** | `trades.mode='paper'` but the linked `positions.mode='live'` | Paper/live splits (daily reset limits, live-only aggregates) are unreliable; the row's -1.203 R depends on which side you read |
| D6 | **Fee model constants inconsistent and stale** | Deployed `backend/core/cost_model.py`: `TAKER_FEE = 0.00045` (0.045%), `MAKER_FEE = 0.00018` (0.018%), commented as "0.05% taker x ~0.9 BNB discount". But **DB rows 2–6 were priced with a 0.036% rate** (0.04% base x 0.9 discount) and rows 7/29/32 with 0.045%. Measured per-fill effective rates from Binance: taker **0.0449–0.0451%**, maker **0.018%** (3 maker entries: tids 13, 21, 26); **blended realized rate over the 33 bot trades = 0.0440%** of notional. Commissions are **BNB-denominated**, so the USDT cost drifts with the BNB price — a single static constant cannot represent it | Wrong fee load on historical rows; per-trade fee residual; no way to reconcile a BNB-denominated charge with a fixed rate |
| D7 | **Zero TP_HIT ever reached** | `exit_reason` over 33 exits: 32 SL_HIT, 1 EXTERNAL_CLOSE, **0 TP_HIT** — while `take_profit` was set on 33 of 34 positions | The take-profit path is either never triggered or is unreachable; winners are structurally capped at +0.766 R vs -1.013 R losers -> 56.9% required vs 33.3% achieved |

---

## 9. Key production SQL used

All read-only. Run with `ssh contabo01 'sudo -u fubot psql -d fubot -f <file>'`.

```sql
-- S1/S2: schema introspection
\d trades
\d positions

-- S3: trade <-> position linkage mismatch  (9 rows)
SELECT t.id, t.position_id, t.symbol, t.mode, t.strategy_type, t.exit_reason, t.pnl_usdt, t.pnl_r
FROM trades t WHERE t.position_id IS DISTINCT FROM t.id ORDER BY t.id;

-- S4: mode contradiction  (1 row: trade 6)
SELECT t.id AS trade_id, t.position_id, t.symbol, t.mode AS trade_mode, p.mode AS pos_mode,
       t.exit_reason, t.pnl_usdt, t.strategy_type
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE t.mode IS DISTINCT FROM p.mode;

-- S5: exit_reason distribution  (SL_HIT 32 / EXTERNAL_CLOSE 2 over 34 rows)
SELECT exit_reason, count(*) n, round(sum(pnl_usdt),2) db_pnl
FROM trades GROUP BY 1 ORDER BY 2 DESC;

-- S6: phantom 23 vs 24 comparison
SELECT p.id, p.signal_id, p.symbol, p.side, p.mode, p.strategy_type, p.status,
       p.entry_price, p.quantity, p.opened_at, p.closed_at,
       t.id AS trade_id, t.pnl_usdt, t.pnl_r, t.exit_reason
FROM positions p LEFT JOIN trades t ON t.position_id = p.id
WHERE p.id IN (23,24) ORDER BY p.id;

-- S7: per-symbol aggregation joined on position_id (NOT trades.id)
SELECT t.symbol, count(*) n, round(sum(t.pnl_usdt),2) db_pnl,
       count(*) FILTER (WHERE t.pnl_usdt>0) db_wins,
       count(*) FILTER (WHERE t.pnl_r IS NULL) null_r,
       round(sum(t.pnl_r),4) db_net_r
FROM trades t JOIN positions p ON p.id = t.position_id GROUP BY 1 ORDER BY 3;

-- S8: stored R vs recomputed R from the FROZEN initial_risk_distance
SELECT t.id, t.symbol, t.pnl_usdt, t.pnl_r AS stored_r,
       round(t.pnl_usdt / (p.initial_risk_distance * p.quantity), 4) AS recomputed_r
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE p.initial_risk_distance IS NOT NULL ORDER BY t.id;

-- S9: signals vs positions vs trades counts
SELECT status, count(*) n FROM signals GROUP BY 1 ORDER BY 2 DESC;   -- TRIGGERED 33, EXPIRED 173518, SKIPPED 96, PENDING 2
SELECT count(*) AS positions_total, count(*) FILTER (WHERE status='CLOSED') AS closed FROM positions;  -- 34 / 34
SELECT count(*) AS trades_total FROM trades;                          -- 34

-- S10: null pnl_r rows (29, 32)
SELECT id, position_id, symbol, side, mode, pnl_usdt, pnl_r, exit_reason
FROM trades WHERE pnl_r IS NULL ORDER BY id;

-- Settings driving the exit design
SELECT key, value FROM settings
WHERE key ILIKE ANY(ARRAY['%trail%','%profit%','%lock%','%be%','%breakeven%','%risk%','%fee%','%stop%','%exit%'])
ORDER BY key;
-- atr_stop_mult 1.5 | be_trail_trigger_r 0.5 | max_aggregate_risk_pct 3.0
-- profit_lock_trail_r 0.5 | profit_lock_trail_r2 0.25 | profit_lock_trigger_r 0.5
-- profit_lock_trigger_r2 1.0 | risk_percent 1.0

-- Trailed (profitable-side) stops vs frozen risk  (16 of 34)
SELECT id, symbol, side, entry_price, stop_loss, take_profit, mode, strategy_type,
  ROUND((CASE WHEN side='LONG' THEN (entry_price-stop_loss) ELSE (stop_loss-entry_price) END)::numeric,8) AS live_stop_dist,
  ROUND(initial_risk_distance::numeric,8) AS frozen_risk
FROM positions
WHERE (side='LONG' AND stop_loss > entry_price) OR (side='SHORT' AND stop_loss < entry_price)
ORDER BY id;

SELECT count(*) FILTER (WHERE (side='LONG' AND stop_loss > entry_price) OR (side='SHORT' AND stop_loss < entry_price)) AS trailed_to_profit,
       count(*) AS total FROM positions;
SELECT count(*) AS with_tp FROM positions WHERE take_profit IS NOT NULL;  -- 33
```

Binance-side truth was reconstructed from `/fapi/v1/income` (`REALIZED_PNL`, `COMMISSION`,
`FUNDING_FEE`, with BNB commissions converted to USDT) and `/fapi/v1/userTrades` (per-fill
price/qty/commission/maker flag), using the app's own credentials on the production host.

---

## 10. Recommended next actions

### Pure correctness fixes (no live-money risk, no strategy change) — can proceed without approval

1. **Fix the income window.** Keep the 5-minute lookback (entry commission can precede the
   persisted row) but **add `end_time = closed_at + 5 s`** to the income query so a position
   cannot absorb commissions from later, unrelated trades on the same symbol. This directly
   addresses D2. It is a code change with no strategy effect, but it changes live-money
   accounting, so **flag it to Vick before deploying**.
2. **Reconcile `trades.mode` from `positions.mode`** (and stop writing the two independently)
   — D5. Read `positions.mode` as the source of truth wherever a mode is needed.
3. **Make `pnl_r` non-nullable with a fallback** — a NOT NULL column plus a computed fallback
   (`pnl_usdt / (initial_risk_distance * quantity)`), with `risk_units.UNKNOWN_R_COST = 1.0`
   fail-closed behaviour preserved. Backfills: trade 29 -> **-0.1566**, trade 32 -> **-0.0499**
   (from frozen risk; truth-based values -0.190 and -0.086 respectively — see the operator
   checklist for the exact SQL and rollback values).

### Live-money / strategy decisions — **require Vick's approval**

4. **Delete or backfill the phantom pair (row 24 + position 24)** — D4. Deleting live rows is a
   record change, so it needs approval even though it is a data-hygiene fix. Exact mutation
   text, pre-validation SELECTs and rollback INSERTs are in the operator checklist.

5. **Recalibrate `TAKER_FEE` from observed fills** — D6. The deployed constant is **0.045%**
   (`TAKER_FEE = 0.00045`) while the actual charge is **0.044% blended** (0.0449–0.0451% on
   taker fills, 0.018% on the 3 maker fills): the model is ~0.001 pp high on every taker leg and
   **2.5x high on a maker fill**. Worse, the historical rows were priced at a *third* constant
   (**0.036%**, the 0.04% base x 0.9 discount) for trades 2–6. Because commissions are
   **BNB-denominated** a single static rate will never be exact: decide whether to store the BNB
   charge and convert at fill time instead of modelling it.
6. **REDESIGN THE EXITS — this is the one that matters.** D7/D2: 32 of 33 exits are SL_HIT and
   **0 are TP_HIT**; BE-trail at +0.5 R and profit-lock at +1.0 R/0.25 R trail cap winners at
   **+0.766 R** against **-1.013 R** losers, requiring a **56.9%** win rate against 33.3%
   achieved. Options to evaluate: stop trailing before the trail threshold, widen the
   profit-lock trail, or require a minimum R before a trail arm engages — then **re-measure**
   over a fresh sample.

### Measurement discipline going forward

7. Report P&L **net of fees and funding** from exchange truth, never from `trades.pnl_usdt`.
8. Quote **n** with every statistic, and use **leave-one-out** on the largest winner (BTCUSDT
   trade 2: removing it turns the portfolio into **-26.90 USDT**).
9. Keep manual account activity separate from bot-attributed P&L (-0.803 USDT unattributed).

---

## 11. CONFIDENCE AND LIMITATIONS

- **The sample is small: 33 independent trades over ~14 days.** Every statistic in this
  document is a small-sample estimate. No confidence interval is implied beyond the standard
  errors and t-statistics printed with each aggregate.
- **The KILL calls are directional, not absolute.** "KILL" here means: *on the evidence
  available, this configuration should be stopped, fixed and re-measured* — it is **not** proof
  of a permanent absence of edge. A redesigned exit set on the same symbols could behave
  differently; conversely the defects in D1–D5 mean the *historical* numbers were measured on
  a corrupted P&L record and cannot be trusted as evidence of what the strategy would have done.
- **Per-symbol verdicts for UNIUSDT (n=3) and OPUSDT (n=3) are Very low confidence** — the
  direction is consistent with the book but a single trade moves the mean.
- **BTCUSDT is Low confidence and highly concentrated**: one trade (+2.26 R, +13.414 USDT)
  carries the symbol's entire profit; leave-one-out gives -26.90 USDT. The symbol's positive
  truth net (+10.072) is *not* evidence of a working symbol.
- **BNBUSDT is the only Medium-confidence KILL** (t = -2.49) and the only symbol with a
  statistically negative mean R.
- **Gross/commission reconstruction is BNB-price sensitive**: BNB commissions were converted
  to USDT using per-minute BNB prices derived from the income stream. Small per-trade residuals
  (see the `; residual X.XX` rows) are consistent with that conversion, not with a broken
  formula.
- **Trade 24's exclusion is evidence-backed** (no fill match), but the *correct* remediation
  (delete vs backfill the pair) is an operator decision.
- **The Binance-side numbers were obtained read-only** with the app's own credentials; no
  Binance endpoint was called that mutates state.
- Nothing in this document should be read as a claim of permanent strategy failure. It is a
  measurement of 33 trades plus a list of seven reproducible defects.
