# P0 execution + accounting fixes — 2026-10-19

Four defects in the money path, all independently verified against source.

## P0-1 Live PnL and R ignore fees and funding
`binance_service.get_position_income` sends `incomeType=REALIZED_PNL`, so Binance
filters server-side; the `COMMISSION` / `FUNDING_FEE` branches are unreachable and
`fees`/`funding` are structurally always 0. Every live `pnl_usdt`, `pnl_r` and the
-3R breaker are gross of costs.

Fix: fetch the window unfiltered and classify client-side; add an `end_time` bound
so a later same-symbol position's events cannot leak into an earlier close.

## P0-4 Closing market orders lack reduceOnly
`place_market_order` sends no `reduceOnly`, so a close that races an exchange-side
TP/SL fill opens a new opposite position nothing manages.

Fix: `reduceOnly=true` on closing orders only; entries must not set it.

## P0-6 Entry orders can double-submit
`_signed_request` retries bare `Exception` (incl. httpx timeouts) for every method,
`POST /fapi/v1/order` included, with no client order id.

Fix: unique `newClientOrderId` on every order POST; never blindly retry a
non-idempotent POST after a transport error.

## P2-1 Secret prefix logged
`auth_service.py:79` logs `FUBOT_SECRET_KEY[:4]` on every failed login.

## P0-7 frontend/.env is tracked
Tracked despite `.gitignore` (gitignore never untracks). Untrack, keep
`.env.example`, treat contents as exposed.

## Sequencing note
Do NOT recompute historical trades until P0-1 is correct AND the R denominator
fix (backend/core/risk_units.py, migration 010) is in place. Recomputing against a
fee-blind income query would write confidently wrong numbers.

## Verification
`PYTHONPATH=. pytest tests/backend/ -q`