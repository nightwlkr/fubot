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

THE CONTRACT -- two totals, never one
-------------------------------------
An R that cannot be established is **unknown**, and unknown is not a number.
It used to be turned into a number on the READ path: every call to
``daily_r_contribution(None)`` logged "charging a full 1.0R loss" and returned
``-1.0``. That single decision was wrong twice over:

* it **fabricated a loss**. A trade whose 1R could not be measured was booked
  into ``daily_summary.total_pnl_r`` as -1.0R -- an outcome that was never
  observed. Binance omits a zero-value REALIZED_PNL income row when a position
  closes at exactly its entry price, so the "loss" was frequently a flat close.
* it **re-logged on every read**. The same unmeasurable row re-logged on every
  scan, every dashboard poll, every performance refresh: 35,582 of 37,879
  journal lines in 24h were that one message.

So the accounting is split in two, explicitly:

* **measured total** -- the honest ledger. An unknown R contributes ``0.0``.
  Nothing is invented: no measured win, no measured loss, no measured flat.
* **limit total** -- what the breaker budgets against. An unknown R costs a
  full ``-UNKNOWN_R_COST`` (1R), exactly as before, so the safety is not
  weakened -- only moved out of the ledger.

``UNKNOWN_R_COST`` is one R because one R is the largest loss a single trade
can legitimately book, so this is the worst *plausible* case rather than an
arbitrary penalty. The breaker is still fail closed: ``daily_reset.used_r_from``
returns ``inf`` for a non-finite total, and an unmeasurable trade still
consumes a full 1R of the day's budget.

The per-read log is gone from this module. The unknown is announced ONCE, at
detection time, where the close is booked (``PositionManager``) -- a read path
that logs is a read path that can flood the journal.
"""

from __future__ import annotations

import logging
import math
from typing import Iterable, NamedTuple, Optional

logger = logging.getLogger(__name__)

#: Charged against the daily/weekly limit when a trade's R cannot be
#: established. One R is the largest loss a single trade can legitimately
#: book, so this is the worst *plausible* case rather than an arbitrary
#: penalty. It is a LIMIT charge, never a ledger entry.
UNKNOWN_R_COST = 1.0


class RTotals(NamedTuple):
    """The two totals every R consumer needs, plus the census behind them.

    ``measured_total``
        The honest ledger: what the books actually recorded. An unmeasurable
        trade contributes NOTHING here -- it is neither a loss we measured nor
        a flat we measured.

    ``limit_total``
        What the breaker budgets against: the measured total plus a full
        ``-UNKNOWN_R_COST`` for every unknown trade. Numerically identical to
        charging -1R per unknown inline, but separable, so a display can show
        the measured total while the breaker charges the limit total.

    ``unknown_count``
        How many trades were unmeasurable, so a reader can see "2 of 8 trades
        had unmeasurable R" instead of a silent lie.

    ``wins`` / ``losses``
        Tally of the trades whose R WAS measurable, plus every unknown trade
        counted as a loss -- an unmeasurable trade is never a win.
    """

    measured_total: float
    limit_total: float
    unknown_count: int
    wins: int
    losses: int


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


def is_unknown_r(pnl_r) -> bool:
    """True when this trade's R could not be established.

    Unknown covers every shape that carries no number: ``None``, a
    non-numeric value, and a non-finite one (``nan``/``inf``). A non-finite R
    is unmeasurable, not "infinite profit" -- it must never reach the ledger
    as a number.
    """
    if pnl_r is None:
        return True
    try:
        value = float(pnl_r)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(value)


def daily_r_contribution(pnl_r) -> float:
    """Signed R this trade contributes to the **measured** total.

    Sign matters. The breaker is NET -- ``daily_reset.used_r_from`` computes
    ``max(0, -total)`` -- so wins must offset losses, and this returns the
    signed value. Returning ``abs()`` here would charge a winning +3R trade as
    3R of loss and halt the day on a good one.

    An unknown or non-finite R contributes ``0.0``: the measured ledger stays
    honest and nothing is invented. The fail-closed charge lives in
    ``r_totals``'s ``limit_total`` (and, per trade, in
    ``limit_r_contribution``), so the breaker still pays a full 1R for an
    unmeasurable trade.

    This function is on the READ path and is therefore SILENT: it used to log
    an error on every call, which re-announced the same unmeasurable row on
    every scan and flooded the journal. The unknown is announced once, where
    the close is booked.
    """
    if is_unknown_r(pnl_r):
        return 0.0
    return float(pnl_r)


def limit_r_contribution(pnl_r) -> float:
    """Signed R this trade contributes to the **limit** total.

    Identical to ``daily_r_contribution`` for every measurable R. For an
    unknown R it is ``-UNKNOWN_R_COST``: the conservative budget charge that
    keeps the daily/weekly breakers behaving exactly as they did before the
    ledger was split out.
    """
    if is_unknown_r(pnl_r):
        return -UNKNOWN_R_COST
    return float(pnl_r)


def r_totals(pnl_rs: Iterable) -> RTotals:
    """Aggregate an iterable of per-trade R values into the two totals.

    * ``measured_total`` -- sum of the measurable R values (unknown -> 0.0).
    * ``limit_total``    -- the same sum plus ``-UNKNOWN_R_COST`` per unknown,
      i.e. exactly what the breaker charged before the split.
    * ``unknown_count``  -- how many trades were unmeasurable.
    * ``wins``/``losses``-- measurable wins, and measurable losses PLUS every
      unknown trade (an unmeasurable trade is never counted as a win).
    """
    measured_total = 0.0
    limit_total = 0.0
    unknown_count = 0
    wins = 0
    losses = 0
    for pnl_r in pnl_rs:
        if is_unknown_r(pnl_r):
            unknown_count += 1
            limit_total -= UNKNOWN_R_COST
            losses += 1
            continue
        value = float(pnl_r)
        measured_total += value
        limit_total += value
        if value > 0:
            wins += 1
        elif value < 0:
            losses += 1
    return RTotals(measured_total, limit_total, unknown_count, wins, losses)
