"""Regression tests for backend/core/bot_mode.py.

The live defect: `settings.bot_mode` held `"live"` — with literal quote
characters — so `value in ("paper", "live")` was False at all three read sites
and the engine silently downgraded to paper. The Start button ignored the saved
mode and the operator had no indication it had.
"""
import pytest

from backend.core.bot_mode import DEFAULT_MODE, normalise_bot_mode


class TestQuotedValuesAreHonoured:
    """A JSON-quoted value is a serialisation artefact, not a different mode."""

    def test_double_quoted_live_resolves_to_live(self):
        """The exact value on the live box.

        Mutation: drop the unquoting branch -> this returns 'paper'.
        """
        assert normalise_bot_mode('"live"') == "live"

    def test_single_quoted_paper_resolves_to_paper(self):
        assert normalise_bot_mode("'paper'") == "paper"

    def test_unquoted_values_still_work(self):
        assert normalise_bot_mode("live") == "live"
        assert normalise_bot_mode("paper") == "paper"


class TestTolerance:
    def test_whitespace_and_casing(self):
        assert normalise_bot_mode("  LIVE  ") == "live"
        assert normalise_bot_mode("\tPaper\n") == "paper"


class TestFailsSafe:
    """The other direction: a genuinely unknown mode must never become live."""

    @pytest.mark.parametrize("bad", [
        None, "", "   ", "LIVE_MAYBE", "true", "1", "0", "off", [],
        {"mode": "live"}, 123,
    ])
    def test_unknown_values_fall_back_to_paper(self, bad):
        """Mutation: return the raw value, or default to 'live' -> these leak.

        This is the safety property that matters most: an unreadable setting
        must not be able to turn real trading on.
        """
        assert normalise_bot_mode(bad) == DEFAULT_MODE == "paper"

    def test_explicit_default_is_respected(self):
        assert normalise_bot_mode("nonsense", default="paper") == "paper"


class TestReadSitesAgree:
    """Three call sites parse this value. They must not be able to disagree —
    the original bug was three copies of the same broken membership test."""

    def test_every_reader_normalises(self):
        import inspect

        from backend.core import bot_mode as mod
        from backend.routers import bot as router
        import backend.main as main

        for src in (inspect.getsource(router), inspect.getsource(main)):
            assert 'value in ("paper", "live")' not in src, \
                "a raw membership test bypasses normalise_bot_mode"
            assert "normalise_bot_mode" in src

        assert "normalise_bot_mode" in inspect.getsource(mod)