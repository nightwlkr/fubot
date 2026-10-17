# Daily loss limit — single source of truth

**Date:** 2026-10-18
**Status:** Approved
**Scope:** Engineering only. No strategy change, no schema change, no production settings change.

## Problem

The daily loss breaker's inputs — `used_r` (R consumed today), `limit_r` (the
configured limit), and which calendar day "today" is — are computed in **six**
independent places across three files:

| What | Where | Clamped? | Consumed by |
|---|---|---|---|
| `used_r` | `backend/core/daily_reset.py:94` | — | **the breaker** |
| `used_r` | `backend/routers/dashboard.py:178` | — | **what the operator sees** |
| `limit_r` | `DailyResetService.get_limit_r()` | yes, `(0, 20]` | the breaker |
| `limit_r` | `dashboard.today_stats()` | **no** | what the operator sees |
| limit | `routers/settings.py exchange_sync` | **no**, hardcoded `-3.0` | latches the day |
| trading day | `daily_reset` (UTC) vs `dashboard.py:109,162` (local) | **no** | which row is read |

Nothing keeps these in agreement. They match today by coincidence, and Failure 2
shows a case where they already do not.

### Failure 1 — the dashboard can report the breaker is fine when it has halted

Set `daily_loss_limit_r = 9999` in the DB — the exact value exploited before the
2026-10-17 hardening. `get_limit_r()` clamps it to 3.0 and the breaker keeps
protecting the account. `dashboard.today_stats()` parses 9999 unclamped and
renders *"limit 9999R · remaining 9999R"* while the bot has actually stopped for
the day. The safety net holds; the panel denies it fired.

### Failure 2 — the dashboard reads a different DAY than the breaker

`daily_reset` resolves today as `datetime.now(timezone.utc).date()`. Both
dashboard endpoints resolve it as `date.today()` — **local** time.

The VPS runs `Europe/Berlin` (UTC+2). For two hours every night, 22:00–24:00 UTC,
`date.today()` returns *yesterday's* date. In that window the breaker reads
today's `daily_summary` row while `/dashboard/overview` and `/dashboard/today`
read yesterday's — so the panel shows a stale day that may be under the limit
while the breaker is enforcing today's. This is live now: at time of writing it
is 21:05 UTC / 23:05 CEST, and in 55 minutes the dashboard silently starts
reporting the previous day.

### Failure 3 — the 48h sync ignores the configured limit entirely

`exchange_sync` writes `daily_loss_limit_hit = (t.total_r <= -3.0)`. The `-3.0`
is a literal. Setting the limit to −5 leaves the sync latching days at −3.

Two secondary defects in the same expression:

- It compares `total_r <= -3.0` rather than mirroring the breaker's own
  `used_r >= limit`, so the two use different comparisons of the same quantity.
- It has no `max(0, …)` floor. `used_r` is clamped at zero so a winning day
  cannot trip the breaker; the sync's raw `total_r` comparison is unclamped.

## Considered and rejected

**Gross-loss counting** (`used_r` = sum of losing trades only, wins never
offset). Proposed by the user, then declined in favour of keeping net semantics.

Rejected on evidence: at the live 53% loss rate the third loss arrives after
roughly 5–6 trades, against a `max_daily_trades` of 12. The breaker would bind
before the trade cap on most days, roughly halving the rate at which the
per-strategy sample accumulates — which directly undermines the strategy
judgement the per-strategy monitor was built to support. Net semantics stay.

**Dashboard calling the live service via `app.state`.** Would remove the
duplication by construction rather than by shared code, but couples a request
handler to the engine lifecycle and requires graceful degradation when the engine
is uninitialised.

**A dedicated `backend/core/limit_rules.py` module.** Cleaner layering, but a new
file for ~10 lines, and it moves the rules away from the breaker that uses them.

## Design

### 1. Three pure helpers in `backend/core/daily_reset.py`

```python
def today_utc() -> date:
    """The trading day. UTC, because every breaker decision is made in UTC."""

def limit_r_from(settings: dict) -> float:
    """Daily loss limit in R — positive, clamped to (0, MAX]. Single source."""

def used_r_from(summary) -> float:
    """R consumed against the limit. Only losses count; wins never offset."""
```

All three are pure functions of data the caller already holds, so no session
plumbing and no service lifecycle coupling.

- `DailyResetService.get_limit_r()` → `return limit_r_from(self.settings)`
- `DailyResetService.get_daily_stats()` → `used_r = used_r_from(summary)`,
  and its four internal `datetime.now(timezone.utc).date()` calls → `today_utc()`
- `dashboard.today_stats()` → `used_r = used_r_from(daily)`, `today = today_utc()`
- `dashboard.dashboard_overview()` → `today = today_utc()`

**Hardening added because the helper is now shared:** the current
`abs(float(...))` *raises* on a non-numeric row. Contained inside the breaker
today, but once the dashboard calls the same function it would surface as a 500
on `/dashboard/today`. `limit_r_from` catches `TypeError`/`ValueError` and falls
back to `DEFAULT_DAILY_LOSS_LIMIT_R`, matching the existing fail-closed posture
of the range check.

### 2. Settings source is deliberately left duplicated

The breaker reads the hot-reloaded in-memory settings dict; the dashboard reads
the `Setting` row. They agree because `PUT /settings/trading` mutates the dict in
place (the 2026-10-15 hot-reload fix). That is two sources kept in sync by
convention.

This spec does **not** change that — collapsing it was the rejected `app.state`
option. What changes is that both now apply identical clamping, so a malformed
row cannot make them disagree about the limit. The residual divergence risk is
recorded in `backend/AGENTS.md` rather than papered over.

### 3. `exchange_sync` binds the limit

```sql
daily_loss_limit_hit = (COALESCE(-t.total_r, 0) >= :limit_r)
```

- `-3.0` literal replaced by a bound parameter, read through `limit_r_from`
- comparison flipped to mirror the breaker's own `used_r >= limit`
- `COALESCE(-t.total_r, 0)` supplies the `max(0, …)` floor the raw comparison lacked

The parameter is sourced from the `daily_loss_limit_r` `Setting` row, the same
row the dashboard reads.

## Invariants

1. `wins + losses + breakevens == total_trades` — already pinned, unchanged.
2. **A winning day can never trip the breaker.** `used_r` floors at 0. Pinned by
   the existing `test_daily_reset.py`.
3. `used_r == max(0, -total_pnl_r)`. Unchanged — net semantics are preserved.
4. For the **same** underlying setting value and the **same** `daily_summary`
   row, `/dashboard/today` and `DailyResetService.get_daily_stats()` report
   identical `used_r` and `limit_r`, because both call the same helpers. The
   settings *source* still differs (§2), so this holds conditional on the
   in-memory dict matching the DB row — it is a shared-rules guarantee, not a
   shared-source guarantee.
5. Every consumer resolves the trading day in **UTC**. A malformed
   `daily_loss_limit_r` yields 3.0 everywhere, never an exception and never an
   unbounded limit.
6. The sync's latch threshold equals the breaker's limit for any setting, and
   the sync's comparison is floored at zero exactly as `used_r` is.
7. Open (unrealised) positions never count toward `used_r`. Only closed trades
   reach `daily_summary`.

## Tests

New `tests/backend/test_daily_limit_rules.py`:

- `limit_r_from`: `9999 → 3.0`, `0 → 3.0`, `-5 → 5.0`, `"abc" → 3.0`,
  `20 → 20`, `20.1 → 3.0`, missing key → `3.0`
- `used_r_from`: winning day → `0.0`, losing day → abs, exactly-zero day → `0.0`,
  `None` → `0.0`
- **Source contract:** the literal `max(0.0, -float(` occurs exactly once in the
  repo — this cannot silently re-duplicate
- **Divergence regression:** with a `9999` setting row — set on **both** the DB
  row and the service's settings dict, so the comparison isolates the clamping
  bug rather than the §2 source split — `/api/dashboard/today` and
  `get_daily_stats()` must report identical `limit_r` and `remaining_r`
- **UTC-day regression:** the dashboard must resolve the trading day in UTC. Set
  the process timezone to something ahead of UTC (or freeze time) such that
  `date.today()` differs from the UTC date, then assert `/api/dashboard/today`
  still reads the UTC day's summary
- **Sync regression:** assert `-3.0` is not a literal in the sync SQL, and that
  moving the configured limit moves the latch threshold
- **Unclamped-comparison regression:** a winning day must not latch in the sync

Existing `test_daily_reset.py` stays green unchanged.

## Documentation

`backend/AGENTS.md` records: the helpers as the single source; the deliberately
duplicated settings *source*; and that the sync's threshold must track the
configured limit.

## Out of scope

- Gross-loss breaker semantics (considered, declined)
- Collapsing the settings source via `app.state`
- Any schema change or migration
- Any change to production settings