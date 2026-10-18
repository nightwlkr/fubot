"""Single source of truth for R-multiples.

1R is the distance from a position's entry to its **original** stop. Two things
make that unrecoverable after the fact:

* BE-trailing and profit-lock trailing MUTATE ``positions.stop_loss`` toward --
  and past -- entry. Reading the live stop at close time is how a breakeven
  stop-out gets scored as a catastrophic loss.
* The reconcile/adopt path (``PositionManager._reconcile_exchange_positions``)
  creates rows with ``initial_risk_distance = NULL`` because the original stop
  is genuinely unknown for a position that predates the bot.

So the distance is frozen at open and every consumer reads it through here
rather than recomputing it. Live 2026-10-17/18 showed the cost of doing it
per-site: an identical -$1.06 BTCUSDT stop-out was recorded as -1.46R, -1.91R
and -1.97R on three rows, and one AVAXUSDT row read -18.26R. Because the daily
breaker sums ``pnl_r``, an unusable R reading does not merely mislabel one
trade -- it moves the -3R limit that is the last line of defence.

The contract is deliberately **fail closed**: an R that cannot be computed is
returned as ``None``, and ``used_r_from_trade`` prices that at a FULL -1R
against the day's limit. Unreadable must never mean free -- the same principle
as ``daily_reset.used_r_from`` returning ``inf`` for a non-finite total.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

#: Charged against the daily limit when a trade's R cannot be established.
#: One R is the largest loss a single trade can legitimately book, so this is
#: the worst *plausible* case rather than an arbitrary penalty.
UNKNOWN_R_COST = 1.0


def risk_distance(entry_price=None, stop_loss=None, frozen=None) -> Optional[float]:
    """The entry-to-original-stop distance that defines 1R.

    ``frozen`` (``positions.initial_risk_distance``, written once at open) is
    authoritative when it is a finite positive number. It is preferred over
    ``entry_price``/``stop_loss`` because the stop is mutable and the frozen
    copy is not.

    Returns ``None`` when no trustworthy distance exists. Callers MUST NOT
    substitute ``abs(entry - stop)`` themselves at close time: that is exactly
    the trailed-stop error this function exists to prevent, and the fallback
    still lives in ``PositionManager._close_position_row`` for the open-time
    case only.
    """
    if frozen is not None:
        try:
            value = float(frozen)
        except (TypeError, ValueError):
            logger.error("initial_risk_distance=%r is not numeric — ignoring", frozen)
        else:
            if math.isfinite(value) and value > 0:
                return value
            logger.error("initial_risk_distance=%r is not finite-positive — ignoring", frozen)

    # No frozen value. A live stop may already have been trailed, so this is only
    # usable while the position is still open and untrailed; callers closing a
    # position must pass `frozen` and treat None as unknown.
    if entry_price is None or stop_loss is None:
        return None
    try:
        distance = abs(float(entry_price) - float(stop_loss))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(distance) or distance <= 0:
        return None
    return distance


def pnl_r_from(pnl_usdt, quantity, distance) -> Optional[float]:
    """R-multiple of a realised P&L, or ``None`` if it cannot be established.

    ``None`` means "unknown", never "zero": a zero R would let an unmeasurable
    trade pass the daily limit untouched, which is the open direction.
    """
    if distance is None:
        return None
    try:
        usd = float(pnl_usdt)
        qty = float(quantity)
        risk = float(distance)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(usd):
        logger.error("pnl_usdt=%r is not finite — R is unknown", pnl_usdt)
        return None
    if not math.isfinite(qty) or qty <= 0 or risk <= 0:
        return None
    risk_amount = qty * risk
    if risk_amount <= 0:
        return None
    value = usd / risk_amount
    return value if math.isfinite(value) else None


def daily_r_contribution(pnl_r) -> float:
    """Signed R this trade contributes to ``daily_summary.total_pnl_r``.

    Sign matters. The breaker is NET -- ``daily_reset.used_r_from`` computes
    ``max(0, -total)`` -- so wins must offset losses, and this returns the
    signed value. Returning ``abs()`` here would charge a winning +3R trade as
    3R of loss and halt the day on a good one.

    An unknown or non-finite R contributes ``-UNKNOWN_R_COST`` (a full loss) so
    the breaker tightens rather than loosens when the books stop making sense.
    """
    if pnl_r is None:
        logger.error("trade R is unknown — charging a full %.1fR loss",
                     UNKNOWN_R_COST)
        return -UNKNOWN_R_COST
    try:
        value = float(pnl_r)
    except (TypeError, ValueError):
        logger.error("trade R=%r is not numeric — charging a full %.1fR loss",
                     pnl_r, UNKNOWN_R_COST)
        return -UNKNOWN_R_COST
    if not math.isfinite(value):
        logger.error("trade R=%r is not finite — charging a full %.1fR loss",
                     pnl_r, UNKNOWN_R_COST)
        return -UNKNOWN_R_COST
    return value
