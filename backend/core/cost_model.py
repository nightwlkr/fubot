"""Single source of truth for trading costs.

Fees were the binding economic constraint on this bot — panel cycle 1 measured
that every configuration went net-negative once costs were charged honestly —
so the constant that charges them deserves to be exact rather than inherited
from a guess.

The repo previously carried three inconsistent constants:

    0.0007   backend/backtest.py        (assumes MAKER entry)
    0.0014   scripts/strategy_compare.py (0.05%/side + 0.02% slippage)
    0.00045  backend/core/position_manager.py (paper mode only, per side)

The backtest's 0.0007 is the optimistic end, and optimistically wrong in a way
that was measured rather than assumed. Live entries are placed as a LIMIT at the
zone midpoint with `timeInForce: GTC` — NOT `GTX` (post-only) — and gate 4
admits any market price within 1% of the zone, so the order is frequently
marketable at submission and fills immediately across the spread as a TAKER.

The signature is unambiguous in the trade log: over 30 consecutive entries,
**27 filled strictly better than the planned midpoint**, 3 worse, 1 exactly on.
A resting maker limit fills AT its price and never better; filling better on 90%
of orders is a taker fill. Exits are STOP_MARKET / TAKE_PROFIT_MARKET algo
orders, also taker. So the live round trip is taker + taker.
"""

from __future__ import annotations

from dataclasses import dataclass

# Binance USDS-M VIP 0, after the 10% BNB discount (which position_manager's
# paper model already assumed at 0.00045/side).
MAKER_FEE = 0.00018
TAKER_FEE = 0.00045

#: Charged per side against the fill price. Deliberately conservative: a
#: STOP_MARKET in a thin book fills through the bid/ask.
DEFAULT_SLIPPAGE = 0.0001


@dataclass(frozen=True)
class CostModel:
    """Round-trip cost in R for a given entry, stop distance and fill mode.

    `fill_mode` is one of:

      ``"taker"``  entry crosses the spread immediately — what live actually
                   does (see module docstring). Default, because defaulting to
                   the optimistic mode is how a losing strategy looks like a
                   winning one.
      ``"maker"``  entry rests and is filled by someone else at the limit price.
      ``"hybrid"`` maker entry, taker exit.
    """

    entry_fee: float = TAKER_FEE
    exit_fee: float = TAKER_FEE
    entry_slippage: float = DEFAULT_SLIPPAGE
    exit_slippage: float = DEFAULT_SLIPPAGE

    @classmethod
    def for_fill_mode(cls, fill_mode: str) -> "CostModel":
        if fill_mode == "taker":
            return cls(TAKER_FEE, TAKER_FEE)
        if fill_mode == "maker":
            return cls(MAKER_FEE, MAKER_FEE)
        if fill_mode == "hybrid":
            return cls(MAKER_FEE, TAKER_FEE)
        raise ValueError(
            f"unknown fill_mode {fill_mode!r}; expected one of "
            "'taker', 'maker', 'hybrid'")

    @property
    def round_trip(self) -> float:
        """Total cost as a fraction of NOTIONAL, one full entry+exit."""
        return (self.entry_fee + self.entry_slippage
                + self.exit_fee + self.exit_slippage)

    def round_trip_in_r(self, entry: float, risk_dist: float) -> float:
        """Round-trip cost expressed in R.

        Fees are a fraction of NOTIONAL while R divides by the risk distance,
        so the same 0.09% costs ~4x more R at a 0.25% stop than at a 1% one.
        That inverse relationship IS the dominant cost drag on this strategy:
        panel cycle 1 measured a tight-stop variant at gross +0.039R and net
        -0.320R for exactly this reason.
        """
        if risk_dist <= 0:
            return float("inf")
        return (entry * self.round_trip) / risk_dist


#: What live actually pays. Not the optimistic 0.0007 the harness previously
#: assumed.
LIVE = CostModel.for_fill_mode("taker")

#: Kept for callers that still read the flat constant. Now the measured live
#: round trip (0.0009 + 0.0002 slippage), NOT the old assumed 0.0007.
FEE_ROUND_TRIP = LIVE.round_trip