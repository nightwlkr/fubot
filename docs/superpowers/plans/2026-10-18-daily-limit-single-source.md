# Daily Loss Limit — Single Source of Truth

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily loss breaker's `used_r`, `limit_r`, and "which day is today" resolve through one shared set of pure helpers, so the dashboard can never disagree with the thing that halts trading.

**Architecture:** Three module-level pure functions in `backend/core/daily_reset.py` — `today_utc()`, `limit_r_from(settings)`, `used_r_from(summary)` — become the single source. `DailyResetService`, both dashboard endpoints, and the 48h `exchange_sync` SQL all route through them. No schema change, no migration, no production settings change.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy (async), Alembic (untouched), pytest + TestClient.

**Design spec:** `docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/core/daily_reset.py` | Owns the breaker. Gains the three pure helpers; the service delegates to them. | Modify |
| `backend/routers/dashboard.py` | Displays today's stats. Stops recomputing `used_r`/`limit_r`/the date. | Modify |
| `backend/routers/settings.py` | `exchange_sync` recompute. Binds the limit instead of hardcoding `-3.0`. | Modify |
| `tests/backend/test_daily_limit_rules.py` | New. Covers helpers, divergence, UTC day, sync threshold. | Create |
| `backend/AGENTS.md` | Records the shared helpers and the deliberately-duplicated settings source. | Modify |
| `tests/AGENTS.md` | Records the new test module. | Modify |

**Not touched:** `backend/models/daily_summary.py` (no new column), any Alembic revision, `tests/backend/test_daily_reset.py` (must stay green unchanged).

---

## Task 1: The three pure helpers

**Files:**
- Modify: `backend/core/daily_reset.py` (insert after `MAX_DAILY_LOSS_LIMIT_R`)
- Test: `tests/backend/test_daily_limit_rules.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_daily_limit_rules.py`:

```python
"""The shared daily-limit rules.

`used_r`, `limit_r` and "which day is today" were each computed in two or three
places with nothing keeping them in agreement. Three concrete failures followed:

  - a `daily_loss_limit_r=9999` row clamped in the breaker but not in the
    dashboard, so the panel showed 9999R remaining while the bot had halted
  - the breaker resolved the trading day in UTC and both dashboard endpoints in
    LOCAL time, so on the UTC+2 VPS they disagreed for two hours every night
  - `exchange_sync` hardcoded `-3.0` in its latch, ignoring the configured limit

Design spec: docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md
"""
import pytest


# ---------------------------------------------------------------- limit_r_from

class TestLimitRFrom:
    def test_a_normal_negative_setting_yields_its_positive_magnitude(self):
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({"daily_loss_limit_r": -3.0}) == pytest.approx(3.0)

    def test_an_out_of_range_value_falls_back_to_the_default(self):
        """9999 is the exact value exploited before the 2026-10-17 hardening.
        It must clamp, not disable the breaker."""
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({"daily_loss_limit_r": 9999}) == pytest.approx(3.0)
        assert limit_r_from({"daily_loss_limit_r": 0}) == pytest.approx(3.0)
        assert limit_r_from({"daily_loss_limit_r": -0.0}) == pytest.approx(3.0)

    def test_the_upper_bound_is_inclusive_and_above_it_falls_back(self):
        from backend.core.daily_reset import MAX_DAILY_LOSS_LIMIT_R, limit_r_from
        assert limit_r_from({"daily_loss_limit_r": -MAX_DAILY_LOSS_LIMIT_R}) \
            == pytest.approx(MAX_DAILY_LOSS_LIMIT_R)
        assert limit_r_from({"daily_loss_limit_r": -20.1}) == pytest.approx(3.0)

    def test_a_non_numeric_value_falls_back_instead_of_raising(self):
        """Previously `abs(float(...))` RAISED here. That was contained inside
        the breaker, but the dashboard now calls the same helper -- an
        exception would surface as a 500 on /dashboard/today."""
        from backend.core.daily_reset import limit_r_from
        for junk in ("abc", None, [], {}, object()):
            assert limit_r_from({"daily_loss_limit_r": junk}) == pytest.approx(3.0), junk

    def test_a_missing_key_falls_back(self):
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({}) == pytest.approx(3.0)
        assert limit_r_from(None) == pytest.approx(3.0)


# ----------------------------------------------------------------- used_r_from

class TestUsedRFrom:
    def _summary(self, total_pnl_r):
        from types import SimpleNamespace
        return SimpleNamespace(total_pnl_r=total_pnl_r)

    def test_a_winning_day_consumes_nothing(self):
        """A winning day must NEVER trip the breaker. This is the single most
        important property of the whole module."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(4.5)) == pytest.approx(0.0)
        assert used_r_from(self._summary(0.01)) == pytest.approx(0.0)

    def test_a_losing_day_consumes_its_magnitude(self):
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(-2.9)) == pytest.approx(2.9)
        assert used_r_from(self._summary(-0.01)) == pytest.approx(0.01)

    def test_an_exactly_flat_day_consumes_nothing(self):
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(0.0)) == pytest.approx(0.0)

    def test_no_summary_yet_consumes_nothing(self):
        """The pre-first-trade-of-the-day case: there is no row at all."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(None) == pytest.approx(0.0)

    def test_a_null_pnl_does_not_crash_the_breaker(self):
        """total_pnl_r is Numeric; a fresh un-flushed row reads as None."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(None)) == pytest.approx(0.0)


# -------------------------------------------------------------------- today_utc

class TestTodayUtc:
    def test_it_returns_the_utc_date_not_the_local_one(self, monkeypatch):
        """23:30 UTC on a UTC+2 host is 01:30 LOCAL the next day. Under the old
        `date.today()` the two disagreed for two hours every night."""
        import time
        from datetime import datetime, timezone
        import backend.core.daily_reset as dr

        monkeypatch.setenv("TZ", "Europe/Berlin")
        time.tzset()
        try:
            class _FixedUTC(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 3, 15, 23, 30, tzinfo=timezone.utc)

            monkeypatch.setattr(dr, "datetime", _FixedUTC)
            assert dr.today_utc() == datetime(2026, 3, 15, tzinfo=timezone.utc).date()
        finally:
            time.tzset()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py -q`

Expected: FAIL — `ImportError: cannot import name 'limit_r_from' from 'backend.core.daily_reset'`

- [ ] **Step 3: Add the helpers**

In `backend/core/daily_reset.py`, immediately after the line
`MAX_DAILY_LOSS_LIMIT_R = 20.0`, insert:

```python


def today_utc() -> date:
    """The trading day.

    Every breaker decision is made in UTC, so every consumer must resolve the
    day in UTC too. Resolving it LOCALLY is a real divergence, not a cosmetic
    one: on a UTC+2 host `date.today()` returns YESTERDAY between 22:00 and
    24:00 UTC, so a consumer resolving it locally reads the previous day's
    summary while the breaker reads today's -- for two hours every night.
    """
    return datetime.now(timezone.utc).date()


def limit_r_from(settings: dict) -> float:
    """Daily loss limit in R (positive), clamped to `(0, MAX]`.

    SINGLE SOURCE for the limit. Shared by the breaker and every display path so
    a malformed row cannot make the panel disagree with the thing that halts
    trading. Before this was shared, the dashboard parsed the setting unclamped
    and rendered "limit 9999R / remaining 9999R" while the breaker -- correctly
    clamping to 3.0 -- had already stopped the bot for the day.

    Fail-closed: unparseable or out-of-range falls back to the 3.0 default.
    """
    raw = (settings or {}).get("daily_loss_limit_r",
                               -DEFAULT_DAILY_LOSS_LIMIT_R)
    try:
        limit = abs(float(raw))
    except (TypeError, ValueError):
        logger.error("daily_loss_limit_r=%r is not a number — falling back to "
                     "%sR", raw, DEFAULT_DAILY_LOSS_LIMIT_R)
        return DEFAULT_DAILY_LOSS_LIMIT_R
    if not (0 < limit <= MAX_DAILY_LOSS_LIMIT_R):
        logger.error("daily_loss_limit_r=%r is out of range (0, %s] — falling "
                     "back to %sR", raw, MAX_DAILY_LOSS_LIMIT_R,
                     DEFAULT_DAILY_LOSS_LIMIT_R)
        return DEFAULT_DAILY_LOSS_LIMIT_R
    return limit


def used_r_from(summary) -> float:
    """R consumed against today's limit.

    NET semantics, deliberately: only a losing day consumes R, and a winning day
    can never trip the breaker. Gross-loss counting (wins never offset) was
    proposed and declined -- at the live 53% loss rate it halts after ~3 losses
    (roughly 5-6 trades) against a `max_daily_trades` of 12, which would halve
    the rate at which the per-strategy sample accumulates. See
    docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md

    `summary` may be None (no row yet -- the pre-first-trade case) or have a
    null `total_pnl_r` (a fresh un-flushed Numeric column).
    """
    if summary is None:
        return 0.0
    return max(0.0, -float(summary.total_pnl_r or 0))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py -q`

Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/core/daily_reset.py tests/backend/test_daily_limit_rules.py
git commit -m "feat: shared pure helpers for the daily-limit rules

today_utc/limit_r_from/used_r_from become the single source for the breaker's
three inputs. limit_r_from also stops raising on a non-numeric row: contained
inside the breaker that was survivable, but a request handler calling it would
have surfaced as a 500."
```

---

## Task 2: The service delegates to the helpers

**Files:**
- Modify: `backend/core/daily_reset.py:41-58` (`get_limit_r`), `:76-107` (`get_daily_stats`)
- Test: `tests/backend/test_daily_reset.py` (must stay green, unmodified)

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/test_daily_limit_rules.py`:

```python
class TestServiceDelegatesToHelpers:
    def test_get_limit_r_returns_exactly_what_limit_r_from_returns(self):
        """The service must not keep its own copy of the clamping rule. If both
        exist, they can drift -- which is the bug this whole change removes."""
        import inspect
        from backend.core.daily_reset import DailyResetService, limit_r_from
        src = inspect.getsource(DailyResetService.get_limit_r)
        assert "limit_r_from" in src, "get_limit_r must delegate to limit_r_from"
        svc = DailyResetService(settings={"daily_loss_limit_r": 9999})
        assert svc.get_limit_r() == pytest.approx(limit_r_from({"daily_loss_limit_r": 9999}))

    def test_get_daily_stats_agrees_with_used_r_from(self):
        from backend.core.daily_reset import DailyResetService, today_utc, used_r_from
        svc = DailyResetService(session_factory=None, settings={})
        # No session factory -> the zero-stats path.
        assert svc.get_daily_stats()["used_r"] == pytest.approx(used_r_from(None))
        assert svc.get_daily_stats()["date"] == today_utc().isoformat()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py::TestServiceDelegatesToHelpers -q`

Expected: FAIL — `get_limit_r` does not mention `limit_r_from`

- [ ] **Step 3: Delegate**

Replace the whole body of `get_limit_r` (everything after its docstring) with:

```python
        return limit_r_from(self.settings)
```

Keep the existing docstring but append this line to it:

```
        The clamping itself lives in `limit_r_from` so the dashboard and the
        48h exchange-sync share it verbatim rather than re-deriving it.
```

In `get_daily_stats`, replace:

```python
        today = datetime.now(timezone.utc).date()
```

with:

```python
        today = today_utc()
```

and replace:

```python
            # Only LOSSES count toward the daily loss limit — a winning day
            # must never trip the -3R stop (abs() would count wins as used).
            used_r = max(0.0, -float(summary.total_pnl_r))
```

with:

```python
            # Only LOSSES count toward the daily loss limit — a winning day
            # must never trip the -3R stop. The rule lives in `used_r_from`.
            used_r = used_r_from(summary)
```

Also replace the remaining `datetime.now(timezone.utc).date()` calls in
`_trigger_daily_stop`, `update_daily_summary` and `check_and_reset` with
`today_utc()` so the day is resolved in exactly one place in this module.

- [ ] **Step 4: Run both test modules**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_reset.py tests/backend/test_daily_limit_rules.py -q`

Expected: PASS. `test_daily_reset.py` must be **unmodified** and still green — it already pins "wins must not count as used R", which net semantics preserve.

- [ ] **Step 5: Commit**

```bash
git add backend/core/daily_reset.py tests/backend/test_daily_limit_rules.py
git commit -m "refactor: DailyResetService delegates to the shared limit helpers

Replaces its private copies of the clamping rule and the used_r formula so
there is exactly one implementation of each. The service's own docstrings
previously described rules that lived in two places."
```

---

## Task 3: The dashboard stops recomputing

**Files:**
- Modify: `backend/routers/dashboard.py:109`, `:162-178`
- Test: `tests/backend/test_daily_limit_rules.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/test_daily_limit_rules.py`:

```python
# --------------------------------------------------- dashboard/breaker parity

@pytest.fixture()
def auth(client, test_user):
    from conftest import TEST_PASSWORD
    res = client.post("/api/auth/login",
                      json={"username": test_user.username,
                            "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    client.cookies.set("fubot_token", res.json()["token"])
    return client


class TestDashboardAgreesWithBreaker:
    def test_the_dashboard_module_no_longer_recomputes_the_rules(self):
        """Source contract. `date.today()` and an inline `max(0.0, -...)` are
        exactly how the two divergences were introduced; if either reappears the
        dashboard starts disagreeing with the breaker again."""
        import pathlib
        src = pathlib.Path("backend/routers/dashboard.py").read_text()
        assert "date.today()" not in src, \
            "the trading day must be resolved in UTC via today_utc(), not locally"
        assert "max(0.0, -float(" not in src, \
            "used_r must come from used_r_from(), not an inline copy"

    def test_the_dashboard_and_the_breaker_report_the_same_limit(self, auth):
        """The live divergence: a 9999 row clamps in the breaker but not in the
        dashboard, which then renders 9999R remaining while the bot has halted.

        Both the DB row and the service's settings dict are set to 9999 so the
        comparison isolates the CLAMPING bug -- the settings *source* still
        differs by design and is deliberately out of scope."""
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        from backend.config import settings as cfg
        from backend.core.daily_reset import DailyResetService
        from backend.models.setting import Setting
        from conftest import run_async

        async def set_row():
            engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                    db.add(Setting(key="daily_loss_limit_r", value={"value": 9999}))
                    await db.commit()
            finally:
                await engine.dispose()

        async def clear_row():
            engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                    await db.commit()
            finally:
                await engine.dispose()

        run_async(set_row())
        try:
            body = auth.get("/api/dashboard/today").json()
            svc = DailyResetService(session_factory=None,
                                    settings={"daily_loss_limit_r": 9999})
            breaker = svc.get_daily_stats()
            assert body["daily_limit_status"]["limit_r"] \
                == pytest.approx(breaker["limit_r"]), \
                f"dashboard {body['daily_limit_status']} vs breaker {breaker}"
            assert body["daily_limit_status"]["remaining_r"] \
                == pytest.approx(breaker["remaining_r"])
        finally:
            run_async(clear_row())
```

Also add this import at the top of the test file, directly under `import pytest`:

```python
from sqlalchemy import delete
from backend.database import AsyncSessionLocal
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py::TestDashboardAgreesWithBreaker -q`

Expected: FAIL on `test_the_dashboard_module_no_longer_recomputes_the_rules` — `date.today()` is present at `dashboard.py:109` and `:162`.

- [ ] **Step 3: Wire the dashboard to the helpers**

In `backend/routers/dashboard.py` add to the imports:

```python
from backend.core.daily_reset import today_utc, used_r_from, limit_r_from
```

In **both** `dashboard_overview()` (line ~109) and `today_stats()` (line ~162),
replace:

```python
    today = date.today()
```

with:

```python
    # UTC, not local: the breaker resolves the trading day in UTC. On a UTC+2
    # host `date.today()` returns yesterday between 22:00-24:00 UTC.
    today = today_utc()
```

In `today_stats()`, replace the whole settings-parsing + `used_r` block:

```python
    # Calculate daily limit status — read from settings table
    from backend.models.setting import Setting
    limit_r = 3.0
    settings_result = await db.execute(select(Setting).where(Setting.key == "daily_loss_limit_r"))
    setting = settings_result.scalar_one_or_none()
    if setting and isinstance(setting.value, dict):
        limit_r = abs(float(setting.value.get("value", -3.0)))
    elif setting and isinstance(setting.value, (int, float)):
        limit_r = abs(float(setting.value))

    # Only losses count toward the limit (a winning day must not trip -3R)
    used_r = max(0.0, -float(daily.total_pnl_r)) if daily else 0.0
```

with:

```python
    # Daily limit status. BOTH values come from the shared helpers the breaker
    # itself uses, so this display cannot disagree with the thing that halts
    # trading. It used to parse the setting unclamped and re-derive used_r
    # inline, which meant a 9999 row rendered "9999R remaining" while the bot
    # had already stopped.
    from backend.models.setting import Setting
    settings_result = await db.execute(select(Setting).where(Setting.key == "daily_loss_limit_r"))
    setting = settings_result.scalar_one_or_none()
    raw_limit = None
    if setting is not None:
        raw_limit = (setting.value.get("value")
                     if isinstance(setting.value, dict)
                     else setting.value)
    limit_r = limit_r_from({"daily_loss_limit_r": raw_limit})

    # Only losses count toward the limit (a winning day must not trip -3R)
    used_r = used_r_from(daily)
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py tests/backend/test_daily_reset.py tests/backend/test_defects_2026_10_17.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/dashboard.py tests/backend/test_daily_limit_rules.py
git commit -m "fix: dashboard must not recompute the breaker's rules

Two divergences, both live:
  - the setting was parsed unclamped, so daily_loss_limit_r=9999 rendered
    9999R remaining while get_limit_r() clamped to 3.0 and had halted the bot
  - the trading day resolved as date.today() (local) while the breaker uses
    UTC, so on the UTC+2 VPS the panel read YESTERDAY for two hours each night

Both now route through today_utc/limit_r_from/used_r_from."
```

---

## Task 4: `exchange_sync` binds the limit

**Files:**
- Modify: `backend/routers/settings.py:373-385`
- Test: `tests/backend/test_daily_limit_rules.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/test_daily_limit_rules.py`:

```python
# ------------------------------------------------------- exchange_sync latch

class TestExchangeSyncLatch:
    def _sync_sql(self):
        import pathlib
        src = pathlib.Path("backend/routers/settings.py").read_text()
        start = src.index("UPDATE daily_summary SET")
        return src[start:src.index('"""', start)]

    def test_the_latch_threshold_is_not_a_hardcoded_literal(self):
        """It was `total_r <= -3.0`, so setting the limit to -5 left the sync
        latching days at -3 — the configured limit was ignored entirely."""
        sql = self._sync_sql()
        assert "-3.0" not in sql, "the sync must bind :limit_r, not hardcode -3.0"
        assert ":limit_r" in sql

    def test_the_latch_comparison_mirrors_the_breaker(self):
        """The breaker tests `used_r >= limit` where used_r = max(0, -total_r).
        The sync tested the raw `total_r <= -3.0`: a different comparison of the
        same quantity, with no floor, so a winning day could latch."""
        sql = self._sync_sql()
        assert "COALESCE(-t.total_r, 0) >= :limit_r" in sql, \
            "the sync must apply the same max(0, ...) floor as used_r_from"

    def test_the_bound_limit_goes_through_the_shared_clamp(self):
        """Whatever row is in the DB, the sync and the breaker must latch on the
        same number."""
        import inspect
        from backend.core.daily_reset import limit_r_from
        import backend.routers.settings as s
        src = inspect.getsource(s.exchange_sync)
        assert "limit_r_from" in src, "the sync must source its limit via limit_r_from"

    def test_a_winning_day_cannot_latch(self):
        """A +5R day: total_r is positive, so COALESCE(-total_r, 0) is 0.0, which
        is below any positive limit. The old `total_r <= -3.0` was also False
        here, but for the wrong reason and with no floor protecting it."""
        from backend.core.daily_reset import used_r_from
        from types import SimpleNamespace
        assert used_r_from(SimpleNamespace(total_pnl_r=5.0)) == pytest.approx(0.0)
        assert 0.0 >= 3.0 is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py::TestExchangeSyncLatch -q`

Expected: FAIL — `-3.0` is a literal in the sync SQL.

- [ ] **Step 3: Bind the limit**

In `backend/routers/settings.py`, in `exchange_sync`, immediately before the
recompute block (the line `    # Recalculate daily summaries for affected dates`),
insert:

```python
    # The latch threshold must equal the breaker's configured limit. It used to
    # be the literal `-3.0`, so `daily_loss_limit_r=-5` left the sync latching
    # days at -3. Read through the shared clamp so a malformed row degrades
    # identically here and in the breaker.
    from backend.core.daily_reset import limit_r_from
    from backend.models.setting import Setting
    limit_row = await db.execute(
        select(Setting).where(Setting.key == "daily_loss_limit_r"))
    limit_setting = limit_row.scalar_one_or_none()
    raw_limit = None
    if limit_setting is not None:
        raw_limit = (limit_setting.value.get("value")
                     if isinstance(limit_setting.value, dict)
                     else limit_setting.value)
    limit_r = limit_r_from({"daily_loss_limit_r": raw_limit})
```

Then replace the `daily_loss_limit_hit = (t.total_r <= -3.0)` line with:

```python
            daily_loss_limit_hit = (COALESCE(-t.total_r, 0) >= :limit_r)
```

and change the bound-parameters dict from:

```python
    }), {"cutoff": cutoff})
```

to:

```python
    }), {"cutoff": cutoff, "limit_r": limit_r})
```

- [ ] **Step 4: Verify the SQL is still valid against the real database**

Run:

```bash
PYTHONPATH=. python - <<'PY'
import asyncio
from sqlalchemy import text
from backend.database import AsyncSessionLocal, engine
from datetime import datetime, timedelta, timezone

SQL = """
UPDATE daily_summary SET
    daily_loss_limit_hit = (COALESCE(-t.total_r, 0) >= :limit_r)
FROM (SELECT DATE(closed_at) as d, COALESCE(SUM(pnl_r),0) as total_r
      FROM trades WHERE closed_at >= :cutoff GROUP BY DATE(closed_at)) t
WHERE daily_summary.date = t.d
"""
async def m():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    async with AsyncSessionLocal() as db:
        await db.execute(text(SQL), {"cutoff": cutoff, "limit_r": 3.0})
        await db.commit()
    await engine.dispose()
asyncio.run(m())
print("sync SQL executed cleanly")
PY
```

Expected: prints `sync SQL executed cleanly`. Run it twice — the statement must
be idempotent (it SETS an absolute flag from a GROUP BY aggregate, never adds).

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py tests/backend/test_defects_2026_10_17.py -q`

Expected: PASS. `test_defects_2026_10_17.py` contains the existing regression
proving the 48h sync **sets absolute aggregates and never calls the
accumulator** — that property is load-bearing and must survive.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/settings.py tests/backend/test_daily_limit_rules.py
git commit -m "fix: exchange_sync must latch on the configured limit

daily_loss_limit_hit was computed as (total_r <= -3.0) -- a literal that
ignored the daily_loss_limit_r setting entirely, so raising the limit to -5
left the sync latching days at -3. Now bound via :limit_r from the same
limit_r_from() clamp the breaker uses, and the comparison mirrors the
breaker's own max(0, -total_r) >= limit instead of a raw total_r <= -3.0."
```

---

## Task 5: Source-contract guard + full verification

**Files:**
- Modify: `tests/backend/test_daily_limit_rules.py`
- Test: full suite

- [ ] **Step 1: Add the re-duplication guard**

Append to `tests/backend/test_daily_limit_rules.py`:

```python
# ------------------------------------------------- re-duplication guard

class TestTheRulesExistInExactlyOnePlace:
    def test_the_used_r_formula_is_written_once_in_the_repo(self):
        """The whole point of the change. A second inline copy is free to drift
        back out of sync with the breaker, which is the defect this removes."""
        import pathlib
        root = pathlib.Path("backend")
        hits = [p for p in root.rglob("*.py")
                if "max(0.0, -float(" in p.read_text()]
        # daily_reset.used_r_from is the single legitimate home.
        assert [p.name for p in hits] == ["daily_reset.py"], \
            f"used_r is re-implemented in {[str(p) for p in hits]}"

    def test_no_module_resolves_the_trading_day_locally(self):
        """`date.today()` currently exists in exactly two places — both the
        dashboard lines this change fixes — so scanning the whole backend tree
        is safe and needs no exclusions."""
        import pathlib
        offenders = [str(p) for p in pathlib.Path("backend").rglob("*.py")
                     if "date.today()" in p.read_text()]
        assert offenders == [], f"local-day resolution found in {offenders}"
```

- [ ] **Step 2: Run the new test file**

Run: `PYTHONPATH=. pytest tests/backend/test_daily_limit_rules.py -q`

Expected: PASS

- [ ] **Step 3: Run the FULL backend suite**

Run: `PYTHONPATH=. pytest tests/backend/ -q`

Expected: PASS, **more than the 421 that passed before this change** — the new
module adds tests. Any failure in `test_daily_reset.py` is a regression in the
breaker itself: stop and investigate rather than adjusting that file, which is
deliberately unmodified by this plan.

- [ ] **Step 4: Verify the suite does not leak rows**

```bash
CNT() { PYTHONPATH=. python -c "
import asyncio
from sqlalchemy import select, func
from backend.database import AsyncSessionLocal, engine
from backend.models.trade import Trade
from backend.models.position import Position
from backend.models.daily_summary import DailySummary
async def m():
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(func.count()).select_from(Trade))).scalar()
        b=(await db.execute(select(func.count()).select_from(Position))).scalar()
        c=(await db.execute(select(func.count()).select_from(DailySummary))).scalar()
    await engine.dispose(); print(f'trades={a} positions={b} daily_summary={c}')
asyncio.run(m())"; }
echo "before: $(CNT)"
PYTHONPATH=. pytest tests/backend/ -q >/dev/null 2>&1
echo "run 1:  $(CNT)"
PYTHONPATH=. pytest tests/backend/ -q >/dev/null 2>&1
echo "run 2:  $(CNT)"
```

Expected: all three lines identical (baseline `trades=48 positions=39 daily_summary=0`).
The new tests seed `DailySummary` and `Setting` rows — they MUST clean up after
themselves. If `daily_summary` is non-zero afterwards, Task 3's parity test is
leaking; add a teardown.

- [ ] **Step 5: Confirm the backend still imports and the frontend is untouched**

Run: `PYTHONPATH=. python -c "import backend.main; print('imports clean')"`

Expected: `imports clean`

No frontend files change in this plan, so no build is required. Run
`cd frontend && npm run build` only if you touched anything under `frontend/`.

- [ ] **Step 6: Commit**

```bash
git add tests/backend/test_daily_limit_rules.py
git commit -m "test: guard against the limit rules being re-duplicated

Pins that used_r's formula is written exactly once in the repo and that no
module resolves the trading day locally. Both defects were invisible because
nothing tied the copies together."
```

---

## Task 6: Documentation (DOX)

**Files:**
- Modify: `backend/AGENTS.md`
- Modify: `tests/AGENTS.md`
- Modify: `AGENTS.md` (verification line only)

- [ ] **Step 1: Update `backend/AGENTS.md`**

Add to the existing "Trade-derived performance endpoints" bullet's neighbourhood,
under `## Local Contracts`:

```markdown
- **The daily-limit rules live in exactly one place** (2026-10-18): `today_utc()`, `limit_r_from(settings)` and `used_r_from(summary)` in `backend/core/daily_reset.py`. `DailyResetService`, both `dashboard.py` endpoints and `routers/settings.exchange_sync` all route through them. Before this they were computed in **six** places and nothing kept them in agreement, producing three live defects: (1) `dashboard.today_stats` parsed `daily_loss_limit_r` **unclamped**, so a `9999` row rendered "9999R remaining" while `get_limit_r()` clamped to 3.0 and had already halted the bot; (2) the breaker resolved the trading day in **UTC** and both dashboard endpoints in **local** time, so on the UTC+2 VPS they disagreed between 22:00-24:00 UTC and the panel read *yesterday's* summary; (3) `exchange_sync` wrote `daily_loss_limit_hit = (t.total_r <= -3.0)` — a hardcoded literal that ignored the configured limit entirely, and a raw comparison with no `max(0, …)` floor unlike `used_r_from`. **Known residual, deliberately not fixed:** the breaker reads the hot-reloaded in-memory settings dict while the dashboard and sync read the `Setting` row. They agree only because `PUT /settings/trading` mutates the dict in place. Do not add a third reader without reading this.
- **`used_r` is NET, and that is deliberate** (2026-10-18): a winning day must never trip the breaker, so `used_r = max(0, -total_pnl_r)`. **Gross-loss counting** (wins never offset) was proposed and declined — at the live 53% loss rate the third loss arrives after ~5-6 trades against `max_daily_trades=12`, which would halt before the trade cap on most days and roughly halve the sample accrual the per-strategy monitor depends on. Do not "fix" this without that trade-off being re-agreed.
```

- [ ] **Step 2: Update `tests/AGENTS.md`**

Add to the test list, alphabetically before `test_trade_log_totals.py`:

```markdown
- `test_daily_limit_rules.py` — the shared daily-limit helpers. Covers `limit_r_from` clamping (9999/0/-0.0 → 3.0, 20 inclusive, 20.1 → 3.0, non-numeric → 3.0 without raising, missing key → 3.0), `used_r_from` (winning day → 0.0, losing → abs, flat → 0.0, `None` summary → 0.0, null `pnl_r` → 0.0), `today_utc` under a pinned clock with `TZ=Europe/Berlin` (the 23:30 UTC case where local and UTC dates disagree), dashboard/breaker parity under a `9999` setting row, and three **source-contract** tests: `max(0.0, -float(` occurs only in `daily_reset.py`, no module resolves the trading day locally, and `exchange_sync`'s SQL binds `:limit_r` instead of hardcoding `-3.0`. The source contracts are load-bearing — the two live divergences were invisible precisely because nothing tied the copies together. All seeded `DailySummary`/`Setting` rows are removed in `finally`; the suite must stay idempotent.
```

- [ ] **Step 3: Update the root `AGENTS.md` verification count**

Change the test count in `## Verification` and the "Tests:" line in
`## Current Status` to the number the full suite actually reports after Task 5.
Do not guess it — read it from the Step 3 output.

Also append to the top of the `## Current Status` bullet list:

```markdown
- FIXED 2026-10-18 (daily-limit rules de-duplicated, **local only — not deployed**, `docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md`): the breaker's `used_r`, `limit_r` and "which day is today" were computed in **six** independent places across `daily_reset.py`, `dashboard.py` and `exchange_sync`, with nothing keeping them in agreement. Three live defects fixed: (1) `dashboard.today_stats` parsed `daily_loss_limit_r` **unclamped**, so `daily_loss_limit_r=9999` rendered *"9999R remaining"* while `get_limit_r()` clamped to 3.0 and had already stopped the bot — the safety net held but the panel denied it fired; (2) the breaker resolved the trading day in **UTC** and both dashboard endpoints in **local** time, so on the UTC+2 VPS they disagreed between 22:00-24:00 UTC and the panel read *yesterday's* `daily_summary` row — this was live during the change itself; (3) `exchange_sync` wrote `daily_loss_limit_hit = (t.total_r <= -3.0)`, a **hardcoded literal ignoring the configured limit**, with a raw comparison and no `max(0, …)` floor unlike `used_r_from`. Now three pure helpers — `today_utc()`, `limit_r_from(settings)`, `used_r_from(summary)` — in `backend/core/daily_reset.py` are the single source; the sync binds `:limit_r` through the same clamp. `limit_r_from` also stops **raising** on a non-numeric row, which was survivable inside the breaker but would have surfaced as a 500 once a request handler called it. **Gross-loss breaker semantics were proposed and DECLINED** — at the live 53% loss rate it halts after ~3 losses (~5-6 trades) against `max_daily_trades=12`, roughly halving the sample accrual the per-strategy monitor needs; net semantics (`used_r = max(0, -total_pnl_r)`) are preserved and `test_daily_reset.py` is unmodified and green. **Known residual, deliberately unfixed:** the breaker reads the in-memory settings dict while the dashboard and sync read the `Setting` row — kept in sync only by `PUT /settings/trading`'s in-place mutation.
```

- [ ] **Step 4: Commit**

```bash
git add backend/AGENTS.md tests/AGENTS.md AGENTS.md
git commit -m "docs: daily-limit rules are single-sourced as of 2026-10-18"
```

---

## Task 7: Deploy

**Requires explicit user approval — this restarts the live service.**

- [ ] **Step 1: Confirm the working tree is clean and pushed**

```bash
git status -sb
git log --oneline origin/main..HEAD
```

Expected: no unpushed commits. `analysis/panel/DECISION.md` may still show as
modified — that is pre-existing unrelated work, leave it unstaged.

- [ ] **Step 2: Deploy**

```bash
ssh contabo01 'cd /opt/fubot && git pull --ff-only && \
  sudo -u fubot /opt/fubot/.venv/bin/python -c "import backend.main; print(\"imports clean\")" && \
  systemctl restart fubot && sleep 12 && systemctl is-active fubot && \
  curl -s http://127.0.0.1:8000/health'
```

Expected: `active` and `{"status":"ok"}`.

- [ ] **Step 3: Verify the fix is live**

```bash
ssh contabo01 'curl -s -o /dev/null -w "today=%{http_code} " \
  -H "Authorization: Bearer $(cat /tmp/ja.txt)" http://127.0.0.1:8000/api/dashboard/today'
```

Then confirm the deployed `dashboard.py` contains no `date.today()`:

```bash
ssh contabo01 'grep -c "date.today()" /opt/fubot/backend/routers/dashboard.py || echo "0 — clean"'
```

Expected: `0 — clean`.

- [ ] **Step 4: Check for errors and report the bot's state**

```bash
ssh contabo01 'timeout 30 journalctl -u fubot --since "2 min ago" --no-pager -o cat | grep -icE "error|traceback"'
```

Expected: `0`.

**Report to the user:** the bot does **not** auto-start on restart — it must be
started from the Overview page.

---

## Rollback

Every task is an independent commit. To revert the behaviour change without
touching the docs:

```bash
git revert --no-edit <commit-sha-of-task-4>
```

Tasks 1-3 are pure refactors with no behaviour change and can be reverted
independently. Task 4 is the only behavioural change to the sync latch.