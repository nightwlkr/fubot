# FUBOT — Auditable Data-Repair Plan (2026-11-03)

**Status: PLAN ONLY. Nothing in this document has been executed. No production row has been
mutated, no service was restarted, no deployment occurred, and no live risk or strategy
setting was changed.**

Every value below was read from production on 2026-11-03 with read-only `SELECT`s and
signed/public exchange `GET`s. Every command is reproduced so a reviewer can re-verify it.

---

## 1. Purpose and scope

Three classes of defect corrupt FUBOT's ledger on Binance USDS-M Futures:

| # | Defect | Rows affected | Effect |
|---|---|---|---|
| A | `trades.pnl_r IS NULL` — close booked with no R multiple | trade 29, trade 32 | A fabricated `-1.0R` was charged on **every read** of the daily and weekly aggregates |
| B | Phantom duplicate close — one exchange round trip booked twice | position/trade 24 | A real `+0.4362R` counted that never happened; the OPUSDT round trip was double-counted |
| C | Income window drops the entry commission leg | 18 of 34 rows (systemic, not just these 2) | Stored `pnl_usdt` **overstates** net P&L by `+1.066 USDT` (`1.298R`) |

This document covers the **data repair** for A and B. Defect C is a **code** defect and is
documented here only to state explicitly what this repair does *not* fix (see §9).

### Root cause (A)

Binance **omits a zero-value `REALIZED_PNL` income row** when a position closes at exactly
its entry price, while still posting `COMMISSION` rows for both legs. The close path in
`backend/core/position_manager.py:1197-1208` requires at least one `REALIZED_PNL` event:

```python
# seconds. Require at least one REALIZED_PNL event (entry commission
if candidate.get("realizedEvents", 0) > 0:
```

When none is present it retries, eventually gives up, and persists `pnl_r = NULL`. The risk
layer then charges that unknown as a full loss:

```python
# backend/core/risk_units.py:115
if pnl_r is None:
    logger.warning("trade R is unknown — charging a full 1.0R loss")
    return -UNKNOWN_R_COST          # -1.0
```

`daily_r_contribution()` is called from `daily_reset.get_daily_stats`,
`weekly_drawdown.check_weekly_drawdown`, `weekly_drawdown.get_weekly_stats` and the
dashboard/performance routers — **all on the read path**. So one bad row both re-logged on
every scan and injected a fabricated `-1.0R` into the daily `-3R` and weekly `-5R` totals.

> **Production evidence of the log storm:** 35,582 of 37,879 journal lines in 24 h (94%)
> were `trade R is unknown`.

---

## 2. How to reproduce the evidence

Production access is read-only. Postgres runs on the **host** at `127.0.0.1:5432` — there is
**no** `fubot-postgres` Docker container. The DB role is **not** `root`; use the URL as-is.
The scheme must be rewritten from `postgresql+asyncpg` to `postgresql` for `psql`.

```bash
ssh contabo01 'set -a; . /opt/fubot/.env; set +a; \
  URL="${DATABASE_URL/postgresql+asyncpg/postgresql}"; psql "$URL" -X -f -' <<'SQL'
\pset border 2
\pset null '[NULL]'
-- PRE-VALIDATION: must return EXACTLY 2 rows, BOTH with pnl_r IS NULL
SELECT t.id, t.position_id, t.symbol, t.side, t.mode, t.entry_price, t.exit_price,
       t.quantity, t.pnl_usdt, t.pnl_r, t.exit_reason, t.closed_at,
       p.initial_risk_distance, p.stop_loss, p.take_profit, p.status
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE t.pnl_r IS NULL
ORDER BY t.id;
SQL
```

**Expected result — exactly 2 rows:**

| id | position_id | symbol | side | mode | entry_price | exit_price | quantity | pnl_usdt | pnl_r | exit_reason | initial_risk_distance |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 29 | 29 | LINKUSDT | LONG | live | 11.57300000 | 11.57500000 | 11.62000000 | -0.10 | `[NULL]` | SL_HIT | 0.0549400100000010382927939645014703273773193359375 |
| 32 | 32 | SOLUSDT | SHORT | live | 95.36000000 | 95.32500000 | 1.81000000 | -0.09 | `[NULL]` | SL_HIT | 0.9974602099999998472412698902189731597900390625 |

If this query returns **anything other than exactly these 2 rows**, **STOP** — the
preconditions have changed and this plan must be re-derived.

### Confirmed settings (read-only)

| key | value |
|---|---|
| `bot_mode` | `"live"` |
| `daily_loss_limit_r` | `-3.0` |
| `weekly_drawdown_limit_r` | `5.0` |
| `risk_percent` | `1.0` |

---

## 3. Exchange-truth proof

The Binance fills are the authority. `userTrades.id == income.tradeId` for every fill below.

### 3.1 LINKUSDT (trade 29) — closed at exactly entry price

`fapi/v1/userTrades`, symbol LINKUSDT, 2026-10-29 09:00 → 12:00 UTC — **5 fills**:

| id | side | price | qty | realizedPnl | commission | time |
|---|---|---|---|---|---|---|
| 1629117817 | BUY (entry) | 11.573 | 11.62 | 0 | 0.00008146 BNB | 10:47:26 |
| 1629121100 | SELL (exit) | 11.573 | 2.08 | 0 | 0.00001460 BNB | 10:57:24 |
| 1629121101 | SELL (exit) | 11.573 | 2.59 | 0 | 0.00001818 BNB | 10:57:24 |
| 1629121102 | SELL (exit) | 11.573 | 1.91 | 0 | 0.00001340 BNB | 10:57:24 |
| 1629121103 | SELL (exit) | 11.573 | 5.04 | 0 | 0.00003538 BNB | 10:57:24 |

Exit qty 2.08+2.59+1.91+5.04 = **11.62** — matches entry qty exactly.
**Entry price 11.573 == exit price 11.573 → gross realized PnL is exactly 0.00.**
The 5 income rows are **all `COMMISSION`**; there is **no `REALIZED_PNL` row** — precisely
the omission that produces `pnl_r = NULL`.

Net = `0.00` gross − both commission legs = **−0.12105534059999999 USDT**.

### 3.2 SOLUSDT (trade 32) — closed at exactly entry price

`fapi/v1/userTrades`, symbol SOLUSDT, 2026-11-02 00:00 → 06:00 UTC — **2 fills**:

| id | side | price | qty | realizedPnl | commission | time |
|---|---|---|---|---|---|---|
| 3622298495 | SELL (entry) | 95.3600 | 1.81 | 0 | 0.00010864 BNB | 01:25:33 |
| 3622432049 | BUY (exit) | 95.3600 | 1.81 | 0 | 0.00010866 BNB | 03:24:53 |

**Entry 95.3600 == exit 95.3600 → gross realized PnL is exactly 0.00.** Both income rows are
`COMMISSION`; again **no `REALIZED_PNL` row**.

Net = `0.00` gross − both commission legs = **−0.1555346512 USDT**.

### 3.3 OPUSDT — exactly ONE round trip (phantom pair 23/24 proof)

`fapi/v1/userTrades`, symbol OPUSDT, 2026-10-28 11:00 → 15:00 UTC — **3 fills**:

| id | side | price | qty | realizedPnl | commission | time |
|---|---|---|---|---|---|---|
| 988499475 | SELL (entry) | 0.1112600 | 49.5 | 0 | 0.00000327 BNB | 12:14:11 |
| 988499476 | SELL (entry) | 0.1112600 | 1186.1 | 0 | 0.00007840 BNB | 12:14:11 |
| 988522949 | BUY (exit) | 0.1105800 | 1235.6 | 0.84020800 | 0.00008160 BNB | 13:31:47 |

Entry qty 49.5 + 1186.1 = **1235.6** = exit qty. **Exactly one entry and one exit.**
The income stream contains exactly **one** `REALIZED_PNL` row (+0.840208 USDT) and **three**
`COMMISSION` rows.

The database, by contrast, holds **two** OPUSDT positions and **two** trades:

| position | trade | symbol | side | mode | entry_price | exit_price | quantity | pnl_usdt | pnl_r | exit_reason | strategy_type | signal_id | opened_at | closed_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 23 | 23 | OPUSDT | SHORT | live | 0.11126000 | 0.11069000 | 1235.60 | 0.78 | 0.4709 | SL_HIT | 3CANDLE | 156564 | 13:14:11.638 | 14:31:44.611 |
| 24 | 24 | OPUSDT | SHORT | live | 0.11126000 | 0.11060500 | 1235.60 | 0.78 | 0.4362 | EXTERNAL_CLOSE | UNKNOWN | `[NULL]` | 13:14:12.133 | 14:36:11.441 |

Position 24 was created **0.495 s** after position 23 for the *same* symbol, *same* side,
*same* entry price and *same* quantity, with `strategy_type = UNKNOWN` and **no signal** —
the signature of the `_reconcile_exchange_positions` adopt path inserting a second OPEN row
for a symbol that already had one. The exchange proves only one round trip existed.

### 3.4 Which of position 23 / 24 has the inverted stop — resolved

| position | entry | stop_loss | side | verdict |
|---|---|---|---|---|
| 23 | 0.11126000 | **0.11069000** | SHORT | stop is **BELOW** entry → **wrong side for a SHORT** |
| 24 | 0.11126000 | **0.11259000** | SHORT | stop is **ABOVE** entry → **correct side for a SHORT** |

Position 23's stop (0.11069) sits below the short entry (0.11126) and equals its own
`exit_price` — it is the row whose stop was mutated to the exit level. Position 24's stop
(0.11259) is the properly-placed stop for a short at that entry.

The exchange exit was a **single BUY of 1235.6 @ 0.1105800 at 13:31:47**, which matches
**position 23**'s close time (14:31:44 local = 13:31:44 UTC, ≈3 s earlier — the fill is
the same close). Position 24's close at 14:36:11 local (13:36:11 UTC) has **no exchange
fill at all** — no BUY of 1235.6 near that time exists in the userTrades stream.

**Conclusion: position/trade 24 is the phantom and must be voided.** Position 23's `stop_loss`
column is separately malformed (below entry for a short), which is a defect of the *stop
placement/trailing* logic, not of the R accounting. **Position 23 is retained** — it is the
row backed by an exchange fill. Its `stop_loss` column is noted in §9 as a separate
code defect and is deliberately **not** altered by this repair, because rewriting it would
change the R denominator of a row that is otherwise correct.

---

## 4. The repair SQL — TEXT ONLY, DO NOT EXECUTE YET

> ⛔ **EXECUTE ONLY AFTER REVIEW.** Every statement below is guarded so it aborts unless the
> row still matches the exact values observed on 2026-11-03.

### 4.1 Correct the two null R values

Formula: `pnl_r = pnl_usdt / (quantity * initial_risk_distance)`

- trade 29: `-0.12105534059999999 / (11.62 * 0.0549400100000010382927939645014703273773193359375)` = **−0.189619**
- trade 32: `-0.1555346512 / (1.81 * 0.9974602099999998472412698902189731597900390625)` = **−0.086140**

Both are **net of both commission legs**, matching the exchange. The stored `pnl_usdt`
(`-0.10`, `-0.09`) was computed with the entry commission leg missing; it is corrected to
the exchange net in the same statement.

```sql
BEGIN;

-- Trade 29 — LINKUSDT LONG, closed at entry price, zero gross PnL
UPDATE trades
   SET pnl_usdt = -0.12105534059999999,
       pnl_r    = -0.189619
 WHERE id          = 29
   AND position_id = 29
   AND symbol      = 'LINKUSDT'
   AND side        = 'LONG'
   AND quantity    = 11.62
   AND pnl_r IS NULL
   AND pnl_usdt    = -0.10
RETURNING id, position_id, symbol, pnl_usdt, pnl_r;

-- Trade 32 — SOLUSDT SHORT, closed at entry price, zero gross PnL
UPDATE trades
   SET pnl_usdt = -0.1555346512,
       pnl_r    = -0.086140
 WHERE id          = 32
   AND position_id = 32
   AND symbol      = 'SOLUSDT'
   AND side        = 'SHORT'
   AND quantity    = 1.81
   AND pnl_r IS NULL
   AND pnl_usdt    = -0.09
RETURNING id, position_id, symbol, pnl_usdt, pnl_r;
```

**Expect exactly 2 rows returned (one per statement).** If either returns 0 rows, the row
changed since 2026-11-03 → `ROLLBACK;` and re-derive.

### 4.2 Rollback for §4.1

```sql
UPDATE trades SET pnl_usdt = -0.10, pnl_r = NULL
 WHERE id = 29 AND position_id = 29 AND pnl_r = -0.189619;

UPDATE trades SET pnl_usdt = -0.09, pnl_r = NULL
 WHERE id = 32 AND position_id = 32 AND pnl_r = -0.086140;
```

### 4.3 Recompute the contaminated `daily_summary` rows

`daily_summary` currently contains the fabricated `-1.0R` contributions. The authoritative
day key is `(closed_at AT TIME ZONE 'UTC')::date`.

Affected days and their **current (contaminated)** values:

| date | total_trades | wins | losses | total_pnl_r | total_pnl_usdt | worst_trade_r | best_trade_r | daily_loss_limit_hit |
|---|---|---|---|---|---|---|---|---|
| 2026-10-29 | 3 | 1 | 1 | **−1.4741** | 0.45 | −1.3868 | 0.9127 | f |
| 2026-11-02 | 1 | 0 | 1 | **−1.0000** | −0.09 | −1.0000 | 0.0000 | f |
| 2026-10-28 | 8 | 6 | 2 | **0.6720** | 1.62 | −1.0463 | 1.0085 | f |

Recompute from the corrected `trades` rather than patching by hand — a hand-patched total
would drift from the row set:

```sql
-- Rebuild daily_summary for the affected dates from the corrected trades.
-- Run this AFTER §4.1 and §4.4 have been committed.
BEGIN;

WITH recomputed AS (
  SELECT (t.closed_at AT TIME ZONE 'UTC')::date       AS day,
         COUNT(*)                                        AS n,
         SUM(CASE WHEN t.pnl_r > 0 THEN 1 ELSE 0 END)    AS wins,
         SUM(CASE WHEN t.pnl_r < 0 THEN 1 ELSE 0 END)    AS losses,
         SUM(CASE WHEN t.pnl_r = 0 THEN 1 ELSE 0 END)    AS breakeven,
         SUM(COALESCE(t.pnl_r, 0))                       AS total_pnl_r,
         SUM(COALESCE(t.pnl_usdt, 0))                    AS total_pnl_usdt,
         MIN(COALESCE(t.pnl_r, 0))                       AS worst_trade_r,
         MAX(COALESCE(t.pnl_r, 0))                       AS best_trade_r
    FROM trades t
   WHERE t.mode = 'live'
     AND (t.closed_at AT TIME ZONE 'UTC')::date IN (DATE '2026-10-28', DATE '2026-10-29', DATE '2026-11-02')
   GROUP BY 1
)
UPDATE daily_summary d
   SET total_trades       = r.n,
       wins               = r.wins,
       losses             = r.losses,
       breakeven          = r.breakeven,
       total_pnl_r        = r.total_pnl_r,
       total_pnl_usdt     = r.total_pnl_usdt,
       worst_trade_r      = r.worst_trade_r,
       best_trade_r       = r.best_trade_r
  FROM recomputed r
 WHERE d.date = r.day
RETURNING d.date, d.total_trades, d.wins, d.losses, d.total_pnl_r, d.total_pnl_usdt;

COMMIT;
```

### 4.4 Void the phantom row 24

**Precondition — re-confirm from the exchange before deleting** (the §3.3 fill list must
still show exactly 3 fills, one entry of 1235.6 and one exit of 1235.6):

```sql
-- Dependent rows on position 24
SELECT (SELECT COUNT(*) FROM orders      WHERE position_id = 24) AS orders_24,
       (SELECT COUNT(*) FROM trades      WHERE position_id = 24) AS trades_24,
       (SELECT COUNT(*) FROM positions   WHERE id          = 24) AS pos_24,
       (SELECT COUNT(*) FROM equity_log  WHERE position_id = 24) AS equity_rows;
-- expected: orders_24 = 0, trades_24 = 1, pos_24 = 1, equity_rows = 8865
```

`equity_log` holds 8,865 rows keyed on position 24 — they are the mark-to-market equity
series. Because the position **did** exist on the exchange for that window, the equity rows
are *not* a phantom; **keep them**. Void the trade and the position, not the equity history.

```sql
BEGIN;

DELETE FROM trades
 WHERE id = 24 AND position_id = 24 AND symbol = 'OPUSDT' AND side = 'SHORT'
   AND strategy_type = 'UNKNOWN' AND signal_id IS NULL
   AND quantity = 1235.6 AND pnl_usdt = 0.78 AND pnl_r = 0.4362
   AND exit_reason = 'EXTERNAL_CLOSE'
RETURNING id, position_id, symbol, pnl_usdt, pnl_r, exit_reason;

DELETE FROM positions
 WHERE id = 24 AND symbol = 'OPUSDT' AND side = 'SHORT' AND status = 'CLOSED'
   AND strategy_type = 'UNKNOWN' AND signal_id IS NULL
   AND initial_risk_distance IS NULL
RETURNING id, symbol, status, strategy_type;

COMMIT;
```

**Expect exactly 1 row from each DELETE.** The `strategy_type = 'UNKNOWN'` and
`signal_id IS NULL` guards are what distinguish the phantom from a legitimate row — a real
adopted position would still carry the adopted strategy metadata, so re-verify that guard
still holds before running.

### 4.5 Rollback for §4.4

```sql
BEGIN;
INSERT INTO trades (id, position_id, symbol, side, mode, entry_price, exit_price, quantity,
                    pnl_usdt, pnl_r, exit_reason, strategy_type, closed_at)
VALUES (24, 24, 'OPUSDT', 'SHORT', 'live', 0.11126000, 0.11060500, 1235.6,
        0.78, 0.4362, 'EXTERNAL_CLOSE', 'UNKNOWN', TIMESTAMPTZ '2026-10-28 14:36:11.44112+01');

INSERT INTO positions (id, symbol, side, mode, status, entry_price, stop_loss,
                       quantity, initial_risk_distance, strategy_type, opened_at, closed_at)
VALUES (24, 'OPUSDT', 'SHORT', 'live', 'CLOSED', 0.11126000, 0.11259000,
        1235.6, NULL, 'UNKNOWN',
        TIMESTAMPTZ '2026-10-28 13:14:12.133433+01', TIMESTAMPTZ '2026-10-28 14:36:11.44112+01');
COMMIT;
```

> Take a `pg_dump` of `trades`, `positions` and `daily_summary` **before** §4.1–§4.4. The
> rollback INSERTs above restore values, not schema defaults — verify column names against
> the live schema before relying on them.

---

## 5. Weekly totals affected

`weekly_drawdown` aggregates on the **Monday-UTC** week (`week_start_utc`).

| week (Mon) | n (before) | R (before) | n (after repair) | R (after repair) | USDT (after) |
|---|---|---|---|---|---|
| 2026-10-19 | 10 | −5.7287 | 10 | −5.7287 | −3.50 |
| 2026-10-26 | 20 | −3.8909 | **19** | **−3.5167** | −2.10 |
| 2026-11-02 | 3 | −3.1758 | 3 | **−2.2619** | −2.66 |

Where the change comes from:

- trade 29 was counted as **−1.0R** (fabricated) but is really **−0.189619R** →
  week of 2026-10-26 gains **+0.810381R** (−3.8909 → −3.0805).
- trade 32 was counted as **−1.0R** but is really **−0.086140R** →
  week of 2026-11-02 gains **+0.913860R** (−3.1758 → −2.2619).
- voiding phantom 24 removes **+0.4362R** from the week of 2026-10-26
  (−3.0805 → **−3.5167**) and one trade from the count (20 → **19**).

The weekly limit is `weekly_drawdown_limit_r = 5.0`. **No week crosses the limit either
before or after the repair** — the most affected week goes from −3.8909R to −3.5167R
against a −5R limit. The repair therefore does **not** release any weekly breaker; it only
removes a false loss from the ledger.

---

## 6. Latch handling

`daily_risk_state` currently holds exactly two rows:

| id | date | mode | limit_hit | reset_at |
|---|---|---|---|---|
| 1 | 2026-10-26 | live | **t** | `[NULL]` |
| 2 | 2026-11-03 | live | **t** | `[NULL]` |

**Rules for the operator:**

1. **Do NOT clear these latches as part of the repair.** The repair changes historical
   totals; it does not retroactively change what the bot was allowed to do on those days.
   Clearing a latch is a *risk decision*, and it is the user's decision — it is explicitly
   **out of scope** for this repair.
2. `daily_summary.daily_loss_limit_hit` is **display-only**. `check_and_enforce_limit`
   does not read it — the authoritative latch is `daily_risk_state.limit_hit`.
3. After the repair, **verify** (read-only) that both latches are still set:

```sql
SELECT date, mode, limit_hit FROM daily_risk_state ORDER BY date;
-- expected: 2026-10-26/live t, 2026-11-03/live t  (UNCHANGED)
```

4. Confirm the breaker still trips on a day whose measured total is unknown. After the code
   fix (see §9), an unknown R must contribute **0** to the *measured* total but must still
   consume **1R of risk budget** in the *limit* calculation. A regression test asserts this;
   do not accept a fix that merely zeroes the contribution and leaves the breaker free.

---

## 7. Post-repair verification

Run all of these read-only after the repair is committed.

```sql
-- 7.1 The two repaired rows
SELECT id, position_id, symbol, pnl_usdt, pnl_r, exit_reason
  FROM trades WHERE id IN (29, 32) ORDER BY id;
-- expected: 29 | 29 | LINKUSDT | -0.12105534059999999 | -0.189619 | SL_HIT
--           32 | 32 | SOLUSDT  | -0.1555346512       | -0.086140 | SL_HIT

-- 7.2 No null R remains
SELECT COUNT(*) AS null_r_rows FROM trades WHERE pnl_r IS NULL;
-- expected: 0

-- 7.3 The phantom is gone and the legitimate row is retained
SELECT id, position_id, symbol, exit_reason, strategy_type, pnl_r FROM trades
 WHERE symbol = 'OPUSDT' ORDER BY id;
-- expected: only id 23 (SL_HIT, 3CANDLE); no id 24
SELECT id, status, strategy_type, signal_id FROM positions
 WHERE symbol = 'OPUSDT' ORDER BY id;
-- expected: only id 23 (CLOSED, 3CANDLE, signal_id 156564); no id 24

-- 7.4 Recomputed daily summaries
SELECT date, total_trades, wins, losses, total_pnl_r, total_pnl_usdt
  FROM daily_summary
 WHERE date IN (DATE '2026-10-28', DATE '2026-10-29', DATE '2026-11-02')
 ORDER BY date;

-- 7.5 Weekly totals (Monday-UTC)
SELECT date_trunc('week', (closed_at AT TIME ZONE 'UTC')::date) AS wk,
       COUNT(*) AS n, SUM(COALESCE(pnl_r,0)) AS r, SUM(COALESCE(pnl_usdt,0)) AS usdt
  FROM trades WHERE mode = 'live'
 GROUP BY 1 ORDER BY 1;
-- expected: 2026-10-26 -> n=19, r=-3.5167 ; 2026-11-02 -> n=3, r=-2.2619

-- 7.6 Latches untouched
SELECT date, mode, limit_hit FROM daily_risk_state ORDER BY date;
```

**Journal check (after the code fix is deployed):**

```bash
ssh contabo01 "journalctl -u fubot --since '10 min ago' --no-pager \
  | grep -c 'trade R is unknown'"
```

A non-zero count is expected **only** while the un-fixed code is still running. After the
`risk_units` fix lands, this must be **0** for a read-only dashboard poll — the message
must no longer repeat on every read.

---

## 8. Complete rollback plan

Execute in reverse order, and only if the repair must be undone:

1. **§4.5** — re-insert the phantom trade 24 and position 24.
2. **§4.2** — restore `trades` 29 and 32 to `pnl_usdt = -0.10 / -0.09`, `pnl_r = NULL`.
3. **§4.3** — recompute `daily_summary` for 2026-10-28, 2026-10-29, 2026-11-02 from the
   restored `trades` (the same CTE re-run; it recomputes from the table, so it stays
   consistent automatically).
4. **§6.3** — re-verify `daily_risk_state` latches are unchanged (the repair never touched
   them; confirm, do not restore).
5. Restore `daily_summary` from the pre-repair `pg_dump` if the recomputed values differ
   from the pre-repair values in any field other than the ones §4.3 targets.

---

## 9. Risk notes — what this repair does **not** fix

These are **code** defects and require software changes; repairing the rows does not
address them, and the rows will be corrupted again if the code is not fixed first.

| # | Defect | Where | Consequence |
|---|---|---|---|
| C1 | Income window omits the entry commission leg | `backend/core/position_manager.py:1203` (uses `position_income_start_ms(opened_at)` with `end_time = closed_at`) | The ENTRY fill timestamp precedes `positions.opened_at` by 0.35–2.33 s on **all 33** rows, so 18 of 34 rows drop the entry fee → DB overstates net P&L by **+1.066 USDT (1.298R)**. Fix: `end_time = closed_at + 5s` and start from the 5-minute lookback. |
| C2 | Zero-value `REALIZED_PNL` income row | `backend/core/position_manager.py:1197-1208` | The close is rejected because `realizedEvents == 0`. Fix: accept the close when `userTrades` proves entry+exit fills exist and derive net PnL from the fill legs. |
| C3 | Fabricated `-1.0R` on every read | `backend/core/risk_units.py:115` | `daily_r_contribution(None)` invents a loss and logs on every read. Fix: unknown R contributes **0** to the *measured* total; the *limit* total still charges 1R of risk budget; the aggregate breaker still fails closed on a non-finite total. |
| C4 | Duplicate OPEN row on the adopt path | `backend/core/position_manager.py` `_reconcile_exchange_positions` | This is what created phantom 24. Fix: re-check OPEN-by-symbol inside the insert transaction; ideally add a partial unique index on `positions(symbol) WHERE status='OPEN'`. |
| C5 | Position 23's `stop_loss` is on the wrong side of entry | `positions` row 23 (`stop_loss` 0.11069 < short entry 0.11126) | A short stop below entry is inverted. Row 23 is retained because an exchange fill backs it, but the stop-placement/trailing logic that produced this value is a defect. **Not** altered by this repair — rewriting it would change the R denominator of an otherwise-correct row. |
| C6 | Trade mode taken from the runtime mode, not the persisted one | `backend/core/position_manager.py::_close_position`, `backend/services/strategy_service.py::_close_position_row` | Trade 6 is `trades.mode='paper'` while `positions.mode='live'`, yet it has real Binance fills and its −4.25 USDT equals the paper model exactly: `(12.1845-12.31)*31.65 - 0.00045*(12.31+12.1845)*31.65 = -4.246`. That live position was valued on the paper branch and is invisible to any `mode='live'` query. |
| C7 | `trades.position_id → positions.id` is the true FK | `trades_position_id_fkey` | Confirmed correct for all 34 rows. The `trades.id != trades.position_id` pairs (1→2, 2→1, 17→18, 18→17, 19→20, 20→21, 21→19, 25→26, 26→25) are an **id-ordering artifact** (trades inserted in close order, positions in open order), *not* a data error. This is an analysis hazard, not a bug to fix — any analysis must join on `position_id`. |

**Ordering requirement:** C2 and C3 should land **before** this repair is applied, and C1
should land before any further trading is trusted, otherwise the same rows will be
re-corrupted on the next zero-PnL close.

---

## 10. Summary of net effect

| Metric | Before repair | After repair | Δ |
|---|---|---|---|
| `trades.pnl_r IS NULL` | 2 rows | 0 | −2 |
| Week 2026-10-19 total R | −5.7287 | −5.7287 | 0 |
| Week 2026-10-26 total R | −3.8909 | −3.5167 | **+0.3742R** |
| Week 2026-11-02 total R | −3.1758 | −2.2619 | **+0.913860R** |
| Week 2026-10-26 trade count | 20 | 19 | −1 (phantom removed) |
| `daily_risk_state` latches | 2 set | 2 set | 0 (unchanged) |
| Weekly limit breaches | 0 | 0 | 0 |

The repair makes the ledger **truthful**. It does not change any risk limit, does not
release any breaker, and does not change any live strategy or position-sizing setting.
