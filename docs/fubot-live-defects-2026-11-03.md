# FUBOT — Live Defects Operator Checklist (2026-11-03)

**One page. Read before any production write.** Derived from
`docs/perf-analysis-2026-11-03.md` (33 independent trades, Binance fill-level truth).

Ground rules:
- Read-only verification SQL is safe to run any time. **All mutations below are TEXT ONLY — they
  were NOT executed.** Each one requires **Vick's approval** because they alter the live
  performance record.
- Production access: `ssh contabo01 'sudo -u fubot psql -d fubot -f <file>'`
  (Postgres on the host at `127.0.0.1:5432`; `DATABASE_URL` in `/opt/fubot/.env`).
- Always take a `pg_dump` of `trades` and `positions` before any mutation:
  `sudo -u fubot pg_dump -d fubot -t trades -t positions > /root/fubot_trades_positions_$(date +%F_%H%M).sql`

---

## 1. The seven defects at a glance

| # | Defect | Severity | Needs Vick's approval? | Fix type |
|---|---|---|---|---|
| D1 | Net-of-fees breakage: `trades.pnl_usdt` omits commission legs on 16 rows; 18 rows overstated by >0.01 USDT; total DB overstatement **+1.066 USDT** | **HIGH** (invalidates all DB-based reporting) | **YES** — backfill rewrites live P&L history | Code + backfill |
| D2 | Income window has no end bound: 5-minute lookback before entry, none after close | **HIGH** (cross-trade fee contamination) | **YES** for the historical rewrite; code fix is low-risk | Code (+ optional backfill) |
| D3 | `trades.id != trades.position_id` on 9 rows (ids assigned out of order; the `position_id` links themselves are correct) | **MEDIUM** (join hazard only) | **NO** — no data change needed | Query discipline |
| D4 | Phantom position 24 + trade 24 (no Binance fill match) | **MEDIUM** (inflates trade count and DB P&L by +0.78) | **YES** — deletes live rows | Row deletion |
| D5 | Mode contradiction on trade 6 (`trades.mode='paper'` vs `positions.mode='live'`) | **MEDIUM** (live/paper splits unreliable) | **YES** to correct the row | Row update |
| D6 | Fee constants inconsistent/stale: deployed `TAKER_FEE=0.00045`; DB rows 2–6 priced at 0.036%, rows 7/29/32 at 0.045%; commissions are **BNB-denominated** | **MEDIUM** | **YES** — recalibration changes live-money accounting | Code |
| D7 | Zero TP_HIT ever reached (32 SL_HIT / 1 EXTERNAL_CLOSE / 0 TP_HIT in 33 exits) | **CRITICAL** (this is the expectancy killer) | **YES** — strategy change on live money | Strategy redesign |

D7 is the headline: it is not a bug in the data, it is the bot losing money by design.

---

## 2. Verify each defect is gone — exact SELECTs

Run each after the corresponding fix. Expected "clean" result is stated.

There is **no** `income_events` / `fill_matches` table in the production DB (tables are:
accounts, daily_risk_state, daily_summary, equity_log, orders, positions, settings, signals,
symbols, trades, users). The exchange-truth side of every check below therefore has to come
from the Binance API reconciliation (the same procedure used for this report), pasted in as a
literal VALUES list or a CTE. The DB-side checks are pure SQL and run as-is.

```sql
-- D1: DB P&L must equal exchange truth. Currently 18 rows differ by >0.01 USDT,
-- total gap +1.0658 USDT. Clean => 0 rows.
-- `truth_net` below are the Binance-reconciled values for the 33 independent trades.
WITH truth(id, truth_net) AS (VALUES
  (1,-0.0085),(2,13.4139),(3,-8.2445),(4,-4.6975),(5,-1.9508),(6,-4.5266),(7,-0.9434),
  (8,-0.0390),(9,-0.5862),(10,-0.7212),(11,0.1687),(12,-1.6408),(13,-2.0578),(14,-0.7444),
  (15,0.9636),(16,0.5329),(17,0.8490),(18,-0.5334),(19,0.2107),(20,-0.3580),(21,-1.7967),
  (22,-0.0111),(23,0.7169),(25,0.1015),(26,1.6484),(27,0.2713),(28,0.8141),(29,-0.1211),
  (30,-0.3834),(31,-1.1612),(32,-0.1555),(33,-1.4960),(34,-0.9999)
),
j AS (
  SELECT t.id, t.pnl_usdt AS db_pnl, x.truth_net
  FROM trades t JOIN truth x ON x.id = t.id
  WHERE t.id <> 24
)
SELECT count(*) AS rows_over_0_01 FROM j WHERE db_pnl - truth_net > 0.01;  -- expect 0 (currently 18)
SELECT round(sum(db_pnl - truth_net),4) AS total_gap FROM j;                 -- expect 0.0000 (currently +1.0658)

-- D1b: no trade may be missing a commission leg. Every row must equal
--      gross + entry fee + exit fee + funding. Reuse the `truth` CTE above.
WITH truth(id, truth_net) AS (VALUES
  (1,-0.0085),(2,13.4139),(3,-8.2445),(4,-4.6975),(5,-1.9508),(6,-4.5266),(7,-0.9434),
  (8,-0.0390),(9,-0.5862),(10,-0.7212),(11,0.1687),(12,-1.6408),(13,-2.0578),(14,-0.7444),
  (15,0.9636),(16,0.5329),(17,0.8490),(18,-0.5334),(19,0.2107),(20,-0.3580),(21,-1.7967),
  (22,-0.0111),(23,0.7169),(25,0.1015),(26,1.6484),(27,0.2713),(28,0.8141),(29,-0.1211),
  (30,-0.3834),(31,-1.1612),(32,-0.1555),(33,-1.4960),(34,-0.9999)
)
SELECT t.id, t.symbol, t.pnl_usdt, x.truth_net, round(t.pnl_usdt - x.truth_net,4) AS gap
FROM trades t JOIN truth x ON x.id = t.id
WHERE abs(t.pnl_usdt - x.truth_net) > 0.005
ORDER BY abs(t.pnl_usdt - x.truth_net) DESC;
-- expect 0 rows (currently 19 rows exceed 0.005, 18 exceed 0.01; total gap +1.0658)

-- D2: no position may absorb commissions outside its own entry/exit window.
--      The commission events live in Binance, not in the DB, so this check is run
--      against the /fapi/v1/income dump for each symbol, joined to the DB rows
--      below. `commission_events` = the Binance income rows (symbol, type, ts,
--      usdt) loaded into a temp table.
--      Clean => no COMMISSION event in [opened_at-300s, opened_at) beyond that
--      trade's own entry-fill commission.
CREATE TEMP TABLE commission_events (symbol text, type text, ts timestamptz, usdt numeric);
-- \copy commission_events FROM '/tmp/commission_events.csv' CSV HEADER   -- Binance dump
SELECT t.id, t.symbol, p.opened_at, count(e.*) AS pre_entry_commissions
FROM trades t JOIN positions p ON p.id = t.position_id
JOIN commission_events e ON e.symbol = t.symbol AND e.type='COMMISSION'
 AND e.ts < p.opened_at AND e.ts >= p.opened_at - interval '300 seconds'
GROUP BY 1,2,3 HAVING count(e.*) > 1;
-- expect 0 rows (currently 13 rows are contaminated; excess -0.934 USDT)

-- D3: trade/position linkage. The `position_id` link must point at the matching
--     position (same symbol AND side) and never dangle. The id EQUALITY is not
--     required: trades.id and positions.id were assigned out of order on 9 rows
--     (1,2,17,18,19,20,21,25,26) and that is harmless as long as you join on
--     `trades.position_id`.
SELECT t.id, t.position_id, t.symbol, p.symbol AS pos_symbol, p.side AS pos_side,
       p.initial_risk_distance
FROM trades t LEFT JOIN positions p ON p.id = t.position_id
WHERE p.id IS NULL OR p.symbol <> t.symbol OR p.side <> t.side;
-- expect 0 rows (currently 0 -- the links are correct; only the id ORDER differs)
-- and note: SELECT count(*) FROM trades t JOIN positions p ON p.id = t.id;  -- 25, not 33

-- D4: phantom. Clean => every position has a signal and a known strategy, every
--      trade has exactly one position, and every trade has a Binance fill match.
--      (the fill-match half is checked against the Binance userTrades dump.)
SELECT t.id, t.position_id, t.symbol, t.pnl_usdt, t.exit_reason, p.signal_id, p.strategy_type
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE p.signal_id IS NULL OR p.strategy_type='UNKNOWN'
   OR p.initial_risk_distance IS NULL
   OR NOT EXISTS (SELECT 1 FROM trades t2 WHERE t2.position_id = t.position_id AND t2.id <> t.id);
-- expect 0 rows (currently trade/position 24)
SELECT count(*) AS positions_without_trade FROM positions p
  WHERE NOT EXISTS (SELECT 1 FROM trades t WHERE t.position_id = p.id);   -- expect 0

-- D5: mode contradiction. Clean => 0 rows.
SELECT t.id, t.mode AS trade_mode, p.mode AS pos_mode
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE t.mode IS DISTINCT FROM p.mode;
-- expect 0 rows (currently trade 6)

-- D6: fee-rate calibration. There is no stored gross on `positions`, so compare the
--      stored P&L against the exchange-truth net for the same trades (the `truth`
--      CTE in D1) and then against the observed per-fill rates from the Binance
--      userTrades dump (taker 0.0449%-0.0451%, maker 0.018%).
WITH truth(id, truth_net) AS (VALUES
  (1,-0.0085),(2,13.4139),(3,-8.2445),(4,-4.6975),(5,-1.9508),(6,-4.5266),(7,-0.9434),
  (8,-0.0390),(9,-0.5862),(10,-0.7212),(11,0.1687),(12,-1.6408),(13,-2.0578),(14,-0.7444),
  (15,0.9636),(16,0.5329),(17,0.8490),(18,-0.5334),(19,0.2107),(20,-0.3580),(21,-1.7967),
  (22,-0.0111),(23,0.7169),(25,0.1015),(26,1.6484),(27,0.2713),(28,0.8141),(29,-0.1211),
  (30,-0.3834),(31,-1.1612),(32,-0.1555),(33,-1.4960),(34,-0.9999)
)
SELECT t.id, t.symbol, t.pnl_usdt, x.truth_net,
       round((t.pnl_usdt - x.truth_net) / nullif(p.entry_price*p.quantity,0), 6) AS implied_fee_rate
FROM trades t JOIN truth x ON x.id = t.id JOIN positions p ON p.id = t.position_id
ORDER BY abs(implied_fee_rate) DESC LIMIT 10;
-- rows 2-6 currently price at 0.036%; deployed constant is 0.00045

-- D7: exit distribution. Clean => TP_HIT count > 0 and stop-only rate has fallen.
SELECT exit_reason, count(*) n, round(sum(pnl_usdt),2) db_pnl
FROM trades GROUP BY 1 ORDER BY 2 DESC;
-- currently SL_HIT 32, EXTERNAL_CLOSE 2, TP_HIT 0

-- Guard rails after every mutation (must always hold)
SELECT count(*) FILTER (WHERE pnl_r IS NULL) AS null_pnl_r FROM trades;              -- expect 0
SELECT count(*) AS trades FROM trades;                                               -- expect 33
SELECT count(*) AS positions FROM positions;                                         -- expect 33
SELECT count(*) AS trades_joined FROM trades t JOIN positions p ON p.id=t.position_id; -- expect 33
SELECT exit_reason, count(*) FROM trades GROUP BY 1;                                 -- expect TP_HIT > 0
-- NOTE: `daily_summary` is built from trades.pnl_usdt / trades.pnl_r, so it
-- inherits the D1 overstatement. Re-check it after the D1 backfill:
SELECT date, total_trades, wins, losses, total_pnl_usdt, total_pnl_r FROM daily_summary ORDER BY date;
```

---

## 3. Production mutations — TEXT ONLY, NOT EXECUTED

Each block below is a proposal. **Do not run without Vick's approval.** Pre-validation SELECTs
first, then the mutation, then the guard-rail SELECTs in §2.

### 3.1 Backfill `pnl_r` for the two NULL rows (trades 29 and 32)

These are the only two rows with `pnl_r IS NULL`. `pnl_r` must be derived from the **frozen**
`initial_risk_distance` (never the trailed live stop).

**Pre-validation**

```sql
SELECT t.id, t.symbol, t.side, t.mode, t.pnl_usdt, t.pnl_r,
       p.initial_risk_distance, p.quantity,
       round(p.initial_risk_distance * p.quantity, 6) AS risk_usdt,
       round(t.pnl_usdt / (p.initial_risk_distance * p.quantity), 4) AS recomputed_r_db_basis,
       round(-0.121055 / (p.initial_risk_distance * p.quantity), 4) AS recomputed_r_truth_basis
FROM trades t JOIN positions p ON p.id = t.position_id
WHERE t.pnl_r IS NULL
ORDER BY t.id;
```

Verified expected output (2 rows):

| id | symbol | side | mode | pnl_usdt | pnl_r | risk_usdt | recomputed_r_db_basis | recomputed_r_truth_basis |
|---|---|---|---|---|---|---|---|---|
| 29 | LINKUSDT | LONG | live | -0.10 | NULL | 0.638403 | **-0.1566** | -0.1896 |
| 32 | SOLUSDT | SHORT | live | -0.09 | NULL | 1.805403 | **-0.0499** | -0.0861 |

**Mutation A (recommended — internal consistency with the stored `pnl_usdt`)**

```sql
BEGIN;
UPDATE trades t
SET pnl_r = round(t.pnl_usdt / (p.initial_risk_distance * p.quantity), 4)
FROM positions p
WHERE p.id = t.position_id
  AND t.pnl_r IS NULL
  AND p.initial_risk_distance IS NOT NULL
  AND p.quantity > 0
  AND t.id IN (29, 32);
-- expect UPDATE 2
-- verify inside the transaction:
SELECT id, pnl_r FROM trades WHERE id IN (29,32) ORDER BY id;   -- 29 -> -0.1566, 32 -> -0.0499
-- COMMIT;  (or ROLLBACK; to abort)
```

**Mutation B (only if D1's P&L backfill is also approved — use the exchange-truth net)**

```sql
BEGIN;
UPDATE trades t
SET pnl_usdt = -0.12, pnl_r = -0.1896
FROM positions p
WHERE p.id = t.position_id AND t.id = 29;
UPDATE trades t
SET pnl_usdt = -0.16, pnl_r = -0.0861
FROM positions p
WHERE p.id = t.position_id AND t.id = 32;
-- COMMIT;  (or ROLLBACK;)
```

**Rollback**

```sql
BEGIN;
UPDATE trades SET pnl_r = NULL WHERE id IN (29, 32);   -- restores the original NULL state
-- COMMIT;  (or ROLLBACK;)
```
Original values (for the record): trade 29 `pnl_r = NULL`, `pnl_usdt = -0.10`, `mode='live'`,
`exit_reason='SL_HIT'`, `position_id=29`; trade 32 `pnl_r = NULL`, `pnl_usdt = -0.09`,
`mode='live'`, `exit_reason='SL_HIT'`, `position_id=32`.

---

### 3.2 Delete the phantom pair: position 24 and trade 24

Justification (verified): position 24 opened **0.5 s after** position 23 with the **identical**
entry price (0.11126000) and quantity (1235.60000000); it has `signal_id = NULL`,
`strategy_type = 'UNKNOWN'`, `initial_risk_distance = NULL`, `take_profit = NULL`,
`exit_reason = 'EXTERNAL_CLOSE'`, and it **matches no Binance fill**. Its +0.78 USDT is a copy of
trade 23's +0.78.

**Pre-validation**

```sql
-- 1. confirm the pair and that nothing else references it
SELECT t.id AS trade_id, t.position_id, t.symbol, t.side, t.entry_price, t.exit_price,
       t.quantity, t.pnl_usdt, t.pnl_r, t.exit_reason, t.strategy_type, t.mode,
       t.created_at, t.closed_at
FROM trades t WHERE t.id = 24;

SELECT p.id, p.signal_id, p.symbol, p.side, p.entry_price, p.quantity, p.leverage,
       p.stop_loss, p.take_profit, p.status, p.opened_at, p.closed_at,
       p.initial_risk_distance, p.strategy_type, p.mode
FROM positions p WHERE p.id = 24;

-- 2. no other trade points at position 24
SELECT id FROM trades WHERE position_id = 24;      -- expect only 24

-- 3. position 24 is the only position with a NULL signal
SELECT count(*) FROM positions WHERE signal_id IS NULL;   -- expect 1

-- 4. snapshot for rollback
\copy (SELECT * FROM trades WHERE id = 24) TO '/tmp/fubot_trade24_rollback.csv' CSV HEADER
\copy (SELECT * FROM positions WHERE id = 24) TO '/tmp/fubot_position24_rollback.csv' CSV HEADER
```

**Mutation (delete the trade first, then the position — FK is `ON DELETE SET NULL` from
positions to trades, so deleting the position alone would orphan the trade row)**

```sql
BEGIN;
DELETE FROM trades WHERE id = 24;          -- expect DELETE 1
DELETE FROM positions WHERE id = 24;       -- expect DELETE 1
-- verify inside the transaction
SELECT count(*) AS trades_after FROM trades;                 -- expect 33
SELECT count(*) AS positions_after FROM positions;           -- expect 33
SELECT count(*) AS orphan_trades FROM trades t
  LEFT JOIN positions p ON p.id = t.position_id WHERE p.id IS NULL;  -- expect 0
-- COMMIT;  (or ROLLBACK;)
```

**Rollback values (exact original rows)**

```sql
BEGIN;
INSERT INTO positions (id, signal_id, symbol, side, entry_price, quantity, leverage,
                       stop_loss, take_profit, status, opened_at, closed_at,
                       initial_risk_distance, strategy_type, mode)
VALUES (24, NULL, 'OPUSDT', 'SHORT', 0.11126000, 1235.60000000, 5,
        0.11259000, NULL, 'CLOSED',
        '2026-10-28 13:14:12.133433+01', '2026-10-28 14:36:11.44112+01',
        NULL, 'UNKNOWN', 'live');

INSERT INTO trades (id, position_id, symbol, side, entry_price, exit_price, quantity,
                    pnl_usdt, pnl_r, exit_reason, strategy_type, mode, created_at, closed_at)
VALUES (24, 24, 'OPUSDT', 'SHORT', 0.11126000, 0.11060500, 1235.60000000,
        0.78, 0.4362, 'EXTERNAL_CLOSE', 'UNKNOWN', 'live',
        '2026-10-28 13:36:11.441669+01', '2026-10-28 14:36:11.440718+01');

SELECT setval('positions_id_seq', GREATEST((SELECT max(id) FROM positions), 24));
SELECT setval('trades_id_seq',     GREATEST((SELECT max(id) FROM trades),     24));
-- COMMIT;  (or ROLLBACK;)
```

> Alternative if Vick prefers **not** to delete history: keep both rows and mark them
> excluded — but the schema has no such flag, so a `notes` column or an external exclusion
> list is required. Deleting is the cleaner option because the row has no exchange
> counterpart at all.

---

### 3.3 Fix the mode contradiction on trade 6 — TEXT ONLY, needs approval

```sql
-- Pre-validation
SELECT t.id, t.mode AS trade_mode, p.mode AS pos_mode, t.pnl_usdt, t.exit_reason
FROM trades t JOIN positions p ON p.id = t.position_id WHERE t.id = 6;
-- currently: trade_mode=paper, pos_mode=live

-- Mutation (source of truth = positions.mode)
BEGIN;
UPDATE trades t SET mode = p.mode FROM positions p
WHERE p.id = t.position_id AND t.id = 6 AND t.mode IS DISTINCT FROM p.mode;
-- expect UPDATE 1
-- COMMIT;  (or ROLLBACK;)
-- Rollback: UPDATE trades SET mode='paper' WHERE id = 6;
```

---

### 3.4 Income-window fix (D2) — code change, live-money impact needs approval

Keep the 5-minute lookback (entry commissions genuinely predate the persisted position row) but
**bound the end of the window**. Target file: `/opt/fubot/backend/services/binance_service.py`
(`position_income_start_ms`, `get_position_income`).

```python
# current: start_time = position_income_start_ms(opened_at)   # opened_at - 300s, no end
# target:
start_ms = position_income_start_ms(opened_at)          # keep 5-min lookback
end_ms   = int(closed_at.timestamp() * 1000) + 5_000     # closed_at + 5s
income = await self.get_position_income(symbol=sym, start_time=start_ms, end_time=end_ms)
```

Deploy with a normal restart of the bot service, then re-run the D2 verification SELECT
(§2) — it must return 0 rows.

---

## 4. Exit redesign (D7) — the change that actually matters

Live settings (verified in production `settings`):

| key | value | effect |
|---|---|---|
| `be_trail_trigger_r` | 0.5 | BE-trail arms at +0.5 R |
| `profit_lock_trigger_r` | 0.5 | profit-lock tier 1 arms at +0.5 R |
| `profit_lock_trail_r` | 0.5 | tier 1 trail distance |
| `profit_lock_trigger_r2` | 1.0 | profit-lock tier 2 arms at +1.0 R |
| `profit_lock_trail_r2` | 0.25 | tier 2 trail distance |
| `atr_stop_mult` | 1.5 | ATR stop multiple |
| `risk_percent` | 1.0 | per-trade risk |
| `max_aggregate_risk_pct` | 3.0 | aggregate risk cap |

Consequence (33 exits): winners average **+0.766 R**, losers **-1.013 R**, break-even win rate
**56.9%**, achieved **33.3%**, expectancy **-0.420 R/trade**. The trailing logic closes winners
before the target can be reached: **0 TP_HIT in 33 trades** while a take-profit was set on 33 of
34 positions.

**Approval required before changing any of these values.** Options to evaluate and back-test:
1. Disable the trail arm entirely below +1.5 R (let the take-profit work).
2. Widen `profit_lock_trail_r2` from 0.25 R toward 0.5 R.
3. Add a minimum-R gate before any trailing arm engages.
4. Recompute expectancy on a fresh sample after the change — do not reuse the current numbers.

---

## 5. Approval matrix

| Action | Type | Approver | Live money? |
|---|---|---|---|
| Add `end_time = closed_at + 5s` to the income window | Code fix | Vick (recommended: yes, it changes live accounting) | Yes (accounting only) |
| Backfill `pnl_r` for trades 29, 32 | Row write | **Vick — required** | Records only |
| Delete trade 24 + position 24 | Row delete | **Vick — required** | Records only |
| Correct `trades.mode` on trade 6 | Row write | **Vick — required** | Records only |
| Backfill `trades.pnl_usdt` to exchange truth (D1) | Row write | **Vick — required** | Records only |
| Recalibrate `TAKER_FEE` from observed fills / store BNB commissions | Code + accounting | **Vick — required** | Yes (accounting) |
| Redesign trailing / profit-lock / TP behaviour | Strategy | **Vick — required** | **Yes — real money** |
| Use `position_id` (never `trades.id`) in every join | Query discipline | No — apply immediately | No |
| Read-only verification SELECTs in §2 | Verification | No — apply immediately | No |
| `pg_dump` backup before any mutation | Ops | No — apply immediately | No |

---

## 6. Reporting rules going forward

1. Report **net of fees and funding** from exchange truth. `trades.pnl_usdt` is not the truth
   (D1).
2. State **n** with every statistic; 33 trades is a small sample. No confidence interval beyond
   what is stated.
3. Quote **leave-one-out** for the biggest winner: BTCUSDT trade 2 (+2.26 R, +13.41 USDT);
   without it the book is **-26.90 USDT**.
4. Keep manual account activity out of bot numbers: account net **-14.289 USDT** vs bot net
   **-13.486 USDT** vs unattributed manual **-0.803 USDT** (SOLUSDT -0.722 dominant).
5. Treat every "KILL" as **fix-then-re-measure**, not as proof of permanent absence of edge.
