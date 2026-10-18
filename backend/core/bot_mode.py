"""Single source of truth for the persisted bot mode.

`settings.bot_mode` is written by `POST /api/bot/start` and read by three
independent call sites (routers/bot.py, main.py, BotEngine.__init__). Each one
did its own `value in ("paper", "live")` membership test, and the stored value
on the live box is `"live"` — WITH the quote characters still attached, because
a generic settings write JSON-encoded it. So every one of those tests failed,
and the engine silently fell back to paper: the Start button ignored the saved
mode entirely and the operator could not tell.

Two failure modes matter here and they pull in opposite directions:

* Unknown/garbage must NOT be honoured. If a stored value is unrecognisable the
  bot must fall back to PAPER, because the alternative is trading real money on
  a misread configuration.
* A value that is merely *quoted* must NOT be treated as unknown. That is a
  serialisation artefact, not a different mode, and treating it as unknown is
  what silently downgraded a live bot to paper.

So this normalises the representation and then validates, rather than validating
the raw string.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The only modes the engine understands.
VALID_MODES = ("paper", "live")

#: Fails SAFE. An unreadable mode must never resolve to real money.
DEFAULT_MODE = "paper"


def normalise_bot_mode(value=None, default: str = DEFAULT_MODE) -> str:
    """Return a validated mode string.

    Tolerates the historical JSON-quoted form (`'"live"'`) and stray
    whitespace/casing, then validates against VALID_MODES. Anything still
    unrecognised falls back to `default` (paper) with a warning.
    """
    if value is None:
        return default

    if not isinstance(value, str):
        logger.error("bot_mode %r is not a string — falling back to %s", value, default)
        return default

    candidate = value.strip()
    # A generic settings write JSON-encodes values, which leaves the quotes in
    # place: '"live"' is the same intent as 'live'.
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
        candidate = candidate[1:-1].strip()
    candidate = candidate.lower()

    if candidate in VALID_MODES:
        return candidate

    logger.error(
        "bot_mode %r is not one of %s — falling back to %s",
        value, VALID_MODES, default)
    return default