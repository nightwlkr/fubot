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

    def test_nan_and_inf_fall_back_rather_than_disabling_the_breaker(self):
        """The negated range check is what makes this safe: NaN fails every
        comparison, so it takes the fallback. Rewriting it as
        `if limit <= 0 or limit > MAX` lets NaN through, and a NaN limit makes
        every `used_r >= limit` False -- the breaker silently off."""
        from backend.core.daily_reset import limit_r_from
        for junk in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
            assert limit_r_from({"daily_loss_limit_r": junk}) == pytest.approx(3.0), junk


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
    def test_it_resolves_the_day_in_utc_and_returns_that_date(self, monkeypatch):
        """23:30 UTC on a UTC+2 host is 01:30 LOCAL the next day, so a
        local-time resolution returns 2026-03-16 while the breaker returns
        2026-03-15. Asserting the tz ARGUMENT is what actually pins the
        contract -- a fake that ignored tz would let a naive `datetime.now()`
        pass, and the `date.today()` path is only caught by calendar luck."""
        from datetime import datetime, timezone
        import backend.core.daily_reset as dr

        base = datetime(2026, 3, 15, 23, 30, tzinfo=timezone.utc)
        seen = {}

        class _FixedUTC(datetime):
            @classmethod
            def now(cls, tz=None):
                seen["tz"] = tz
                return base.astimezone(tz) if tz is not None else base.astimezone()

        monkeypatch.setattr(dr, "datetime", _FixedUTC)
        assert dr.today_utc() == base.date()
        assert seen["tz"] is timezone.utc, "the trading day must be resolved in UTC"

    def test_the_module_does_not_resolve_the_day_locally(self):
        """Guards the `date.today()` path directly, which the fake-clock test
        above cannot catch on its own.

        Checked as an AST CALL rather than as a substring: the prose in
        `today_utc`'s docstring must be able to NAME `date.today()` in order to
        explain why it is not used, and a text search would forbid exactly that.
        """
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2]
               / "backend/core/daily_reset.py").read_text()
        called = set()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "today"):
                called.add(ast.unparse(node.func))
        assert "date.today" not in called, f"local day resolution: {sorted(called)}"
