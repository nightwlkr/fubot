# analysis/ — Strategy Review Artifacts

## Purpose

Durable records of strategy review cycles: multi-model panel findings, backtest evidence, and the Lead's decision document. These are ANALYSIS artifacts — they explain *why* the trading logic is what it is. Binding implementation contracts live in the repo's code-owning AGENTS.md files (`backend/AGENTS.md`, `tests/AGENTS.md`).

## Ownership

Owner: FUBOT project team (Lead). Panelists write only their own assigned file inside `panel/`.

## Local Contracts

- `panel/<analyst>-<topic>.md` — one file per panelist per round (independent analysis: leaks with `file:line` + numbers, proposals, risks, what to cut).
- `panel/DECISION.md` — Lead-owned. The single source of truth for what was adopted, rejected, or deferred, with the evidence and the resulting commit/deploy for each item. Updated at the end of every review cycle.
- Round 2+ critiques go in `panel/<analyst>-<topic>-r2.md` — never edit another panelist's file.
- Production is live money: every artifact must respect read-only production access (no orders, no settings changes, no service restarts, never write the production DB, never print secrets).
- Every claim carries either a `file:line` citation or a measured number with its sample size; anything else is marked as a hypothesis.

## Work Guidance

- Evidence standard: no lookahead in replays, intrabar ordering stated (stop-before-target), fees stated, and cells with fewer than 20 trades marked inconclusive.
- Reusable experiments belong in `/tmp` on the VPS (or as a throwaway script) — only harness changes that should survive go into `backend/backtest.py` with tests.
- Strategy changes are never applied from an artifact alone: they need the user's explicit approval, the test suite, and a recorded deploy in `DECISION.md`.

## Verification

Artifacts are checked by the Lead for citation/sample-size compliance. The decisions they produce are verified by `PYTHONPATH=. pytest tests/backend/ -q` and by the monitoring window recorded in `DECISION.md`.

## Performance page

- **Endpoints**: `/performance/equity` (account equity from `equity_log`), `/performance/pnl` (cumulative realized R+USD from `trades`), `/performance/monthly` (per-month R/count/win-rate **from `trades`**), `/performance/strategies` (R:R histogram), `/trades/stats` (headline cards).
- **Live-only by default.** Every trade-derived endpoint takes `mode=live|paper|all` and defaults to `live` — paper fills are not real money and must never contaminate a live win-rate or R-per-trade figure. Pass `all` to inspect paper history.
- **`/performance/equity` is exempt from `mode`.** `equity_log` records account balance and has no `mode` column, so no filter is possible; `test_performance_wiring.py` asserts this exemption so it is not "fixed" later with a no-op filter.
- **`equity_log.balance_usdt` must be the TOTAL USDT balance**, never `availableBalance`. `availableBalance` is free margin and dips on every entry — charting it made equity look like it lost money when collateral was merely locked. Binance's `/fapi/v2/balance` returns both; `get_account_balance()` exposes `balance`, `availableBalance`, `maxWithdrawAmount`, `unrealizedProfit`.
- **Monthly Returns derives from `trades`, not `daily_summary`.** `daily_summary` is written by the daily-reset service and backfilled only by the exchange-sync recompute (last 48h), so it is not a projection of `trades` — reading it made Monthly Returns blank or disagree with `/pnl`.
- **Known data defect (2026-10-17):** the dev database (`localhost/fubot`) holds 20 corrupt rows with placeholder entries (`entry_price` exactly 100 or 106.5, qty 4260) whose exits are real market prices. They contribute ~+183,826R of the total and dominate Best Trade / Avg R / the P&L chart. Production must be checked separately. Cleaning them is a user decision — see DECISION.md.

## Child DOX Index

- `panel/` — per-cycle panel artifacts and the Lead decision document; owned by this doc.
- `REVIEW_2026-10-17.md` — full-codebase review (backend + frontend). Read before changing the position lifecycle, the settings API, or the WS/tick path.
