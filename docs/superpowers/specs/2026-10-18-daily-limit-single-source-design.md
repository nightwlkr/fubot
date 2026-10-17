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

The VPS runs `Europe/Berlin` (UTC+2) — **east** of UTC, so local time runs
*ahead*. For two hours every night, 22:00–24:00 UTC, `date.today()` returns
**tomorrow's** date, not yesterday's. The dashboard then queries
`DailySummary.date == tomorrow`, a row that **does not exist yet**, so `daily` is
`None` and the endpoint renders a **freshly zeroed day** — `used_r = 0.0`,
`remaining_r` at full limit — while the breaker is enforcing today's real,
possibly over-limit, row.

This is worse than reading a stale day: a full-looking limit bar is the most
reassuring thing the panel can show, and it is exactly what it shows for two
hours every night.

### Failure 3 — the 48h sync ignores the configured limit entirely

`exchange_sync` writes `daily_loss_limit_hit = (t.total_r <= -3.0)`. The `-3.0`
is a literal. Setting the limit to −5 leaves the sync latching days at −3.

Two secondary defects in the same expression:

- It compares `total_r <= -3.0` rather than mirroring the breaker's own
  `used_r >= limit`, so the two use different comparisons of the same quantity.
- It had no NULL guard, so a day with trades but no summed R (`SUM(pnl_r)` over
  no rows, or all-NULL `pnl_r`) made the comparison NULL and left
  `daily_loss_limit_hit` NULL too.

  It also had **no floor**, and still does not — but see the correction in
  §3: the winning day is excluded by the limit's positivity, not by a floor.

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

`used_r_from` gets the mirror-image treatment, for the same reason: an unusable
number must HALT, never release. `max(0.0, -float("nan"))` is **0.0** — every
comparison against NaN is False — so a non-finite `total_pnl_r` would read as
"nothing consumed" and silently disable the breaker for the day. A non-finite
total therefore returns `inf`, which satisfies `used_r >= limit` on every finite
limit and stops the bot. This is deliberately the exact opposite of the naive
reading: "an unreadable total" must never mean "the day is free".

### 2. Settings source is deliberately left duplicated

The breaker reads the hot-reloaded in-memory settings dict; the dashboard reads
the `Setting` row. They agree because `PUT /settings/trading` mutates the dict in
place (the 2026-10-15 hot-reload fix). That is two sources kept in sync by
convention.

This spec does **not** change that — collapsing it was the rejected `app.state`
option. What changes is that both now apply identical clamping, so a malformed
row cannot make them disagree about the limit. The residual divergence risk is
recorded in `backend/AGENTS.md` rather than papered over.

### 2b. Known residuals (pre-existing, deliberately not fixed here)

- **The settings source is still duplicated** (above). They agree only because
  `PUT /settings/trading` mutates the in-memory dict in place — and that mutation
  sits inside a swallowing `try/except`, so a failed hot-reload can leave the
  breaker and the dashboard disagreeing.
- **The sync groups by `DATE(closed_at)` — session-LOCAL — while
  `daily_summary.date` is written in UTC** by the accumulator. On a host east of
  UTC (the VPS) the two disagree for ~4 hours a day. The consequence is **not**
  merely "matches zero rows", and the earlier framing of this residual
  understated it: a sync run inside that window (22:00–24:00 UTC, reachable only
  by a manual maintenance `POST`) writes a `daily_summary` row keyed to the LOCAL
  day, so **`WHERE daily_summary.date = t.d` overwrites TODAY's UTC row with a
  PARTIAL day** — the trades before the offset that land on the local date and
  the ones after it that do not. On a losing day that can push `used_r` back
  **under** the limit, i.e. silently re-open an allowance the breaker had
  already consumed. Only reachable via a manual sync in that window (the
  scheduled run is 48h), so it is not a live money path today. The scheduled
  fix is `DATE(closed_at AT TIME ZONE 'UTC')`.

### 3. `exchange_sync` binds the limit

```sql
daily_loss_limit_hit = (COALESCE(-t.total_r, 0) >= :limit_r)
```

- `-3.0` literal replaced by a bound parameter, read through `limit_r_from`
- comparison flipped to mirror the breaker's own `used_r >= limit`
- `COALESCE(-t.total_r, 0)` is **defensive only** and guards nothing reachable:
  the subquery already emits `COALESCE(SUM(pnl_r),0) as total_r`, so `total_r`
  is non-NULL by the time the comparison reads it. The earlier claim here — that
  it guards "a day with trades but no summed R" — is **factually wrong**: a
  group whose `SUM(pnl_r)` is NULL (every `pnl_r` NULL) is already coalesced to
  0 by the inner `COALESCE`. The explicit NULL guard is kept because it makes
  the comparison's non-NULL-ness local and obvious; it is not a fix for
  anything, and must not be cited as one.

**`COALESCE` is not a floor, and must not be read as one.** It does not spell
`max(0, …)`: on a +4.5R day it evaluates to `-4.5`. The winning day fails to
latch because `limit_r` is strictly **positive**, so `-4.5 >= 3.0` is false.
Do **not** rewrite this into `GREATEST(0, -total_r)` "for symmetry" with
`used_r_from`: the equivalence with the breaker's spelling already holds on
every input the limit can actually take, because `limit_r_from` only ever
returns values in `(0, MAX]`. `backend/AGENTS.md` and the source comment in
`routers/settings.py` record the same rule.

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
   the sync agrees with the breaker on **every** day, winning or losing. The two
   spellings differ (`used_r` floors at zero, the SQL does not) and are still
   equivalent because `limit_r` is always strictly positive — see §3. Do not
   "restore" the floor in the SQL for symmetry; it is unnecessary and the
   guardrail against it is a test.
7. Open (unrealised) positions never count toward `used_r`. Only closed trades
   reach `daily_summary`.

## Tests

New `tests/backend/test_daily_limit_rules.py`:

- `limit_r_from`: `9999 → 3.0`, `0 → 3.0`, `-5 → 5.0`, `"abc" → 3.0`,
  `20 → 20`, `20.1 → 3.0`, missing key → `3.0`
- `used_r_from`: winning day → `0.0`, losing day → abs, exactly-zero day → `0.0`,
  `None` → `0.0`, **non-finite (`nan`/`±inf`) → `inf`, i.e. halted**
- **Source contract:** the `used_r` formula — matched as an AST shape (a `max()`
  over a negated daily-pnl expression), not as a literal string, because the
  non-finite guard hoisted the `float()` into a local and a substring ban would
  have silently passed over zero matches — occurs exactly once in the repo, and
  no dashboard module recomputes it
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
- **Unclamped-comparison regression:** a winning day must not latch in the sync,
  and the limit must be strictly positive for **every** input `limit_r_from` can
  be handed — including the ones it rejects — since that positivity is the whole
  reason the SQL needs no floor. The latch expression must also carry no
  `GREATEST`.

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