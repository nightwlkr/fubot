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
