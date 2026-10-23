"""Backtest harness — replay the signal pipeline over historical klines.

Validates the strategy BEFORE trusting a forward paper run: fetch N days of
candles, walk them forward one candle at a time, detect signals exactly as
`scan_symbol` does (trend/volume/R:R/ATR-stop filters), and simulate entries
and SL/TP exits to report expectancy.

Pure over candle lists — no DB writes, no network beyond the initial kline
fetch. The signal logic mirrors backend/services/signal_service.py so a
backtest result reflects what the live scanner would have done.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from backend.indicators.atr import calculate_atr
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.choch import detect_choch
from backend.indicators.trend import analyze_trend_bias
from backend.indicators.volume_profile import get_volume_confirmed_filter
from backend.indicators.regime import analyze_market_regime, should_trade_regime
from backend.core.cost_model import LIVE as COST_MODEL
from backend.backtest_data import last_closed_before
from backend.backtest_data import align_window, bars_expected, fetch_klines, window_for_days

logger = logging.getLogger(__name__)

# Trading costs come from ONE place — backend/core/cost_model.py. Three
# inconsistent constants used to live here, in scripts/strategy_compare.py and
# position_manager; this one charged 0.0007 on the assumption that entries fill
# as maker. Live entries are GTC (not GTX post-only) at the zone midpoint with
# gate 4 admitting +-1%, so they cross the spread as a TAKER — measured: 27 of
# 30 consecutive fills came in BETTER than the planned midpoint, which a resting
# maker order never does. Modeled costs are 0.00110 per round trip: 0.00090
# account-calibrated taker fees plus 0.00020 assumed slippage.

#: Re-exported so `scripts/backtest.py` and existing callers keep working. The
#: value is derived from COST_MODEL, never defined twice.
FEE_ROUND_TRIP = COST_MODEL.round_trip


def fee_in_r(entry: float, risk_dist: float) -> float:
    """Round-trip cost expressed in R.

    Fees are a fraction of NOTIONAL; R divides by the risk distance. So the same
    ~0.11% costs 4x more R at a 0.5% stop than at a 2% stop — the effect panel
    cycle 1 measured as the dominant cost drag. At the observed 0.123% stop, a
    round trip costs about 0.90R under the current account-calibrated model.

    `risk_dist` is a PRICE distance (entry - stop), not a stop fraction.
    """
    if risk_dist <= 0:
        return float("inf")
    return COST_MODEL.round_trip_in_r(entry, risk_dist)


# Live entry semantics, mirrored so the backtest measures what the bot does.
# `StrategyService._enter_with_limit` places a LIMIT at the entry-zone midpoint,
# waits up to `signal_max_age_minutes`, then RE-VALIDATES R:R at the price that
# actually filled and skips the signal if it no longer qualifies.
DEFAULT_SIGNAL_MAX_AGE_MIN = 60


def fill_at_midpoint(series: list[dict], signal_idx: int,
                     zone_lo: float, zone_hi: float,
                     max_age_ms: int, *, direction: Optional[str] = None,
                     penetration_bps: float = 0.0) -> Optional[tuple[int, float]]:
    """First bar at/after `signal_idx` whose range touches the zone midpoint.

    Returns `(bar_index, fill_price)` or None. The fill price is the MIDPOINT,
    not the bar's open — that is what a resting LIMIT at the midpoint gets.
    Scanning starts at `signal_idx + 1`: the signal bar is already closed when
    the signal exists, so filling on it would be lookahead.

    `series` is the signal's OWN interval (1h, 5m or 15m) — not necessarily 5m.
    The expiry deadline is measured from the signal bar's CLOSE, matching live:
    `evaluate_signal` only learns the signal once its bar has closed, and
    `signal_max_age_minutes` counts from there. Measuring from `open_time`
    instead grants up to a full extra bar of life that live never had.

    `penetration_bps=0` preserves the touch-fill baseline. A positive value
    requires a directional trade-through, reducing optimistic fills caused by
    queue position that OHLC candles cannot reveal.
    """
    try:
        penetration_bps = float(penetration_bps)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(penetration_bps) or penetration_bps < 0:
        return None
    direction = str(direction or "").upper()
    if penetration_bps > 0 and direction not in ("LONG", "SHORT"):
        return None

    midpoint = (zone_lo + zone_hi) / 2.0
    if not 0 <= signal_idx < len(series):
        return None
    signal_close = series[signal_idx].get("close_time")
    if signal_close is None:
        return None  # fail closed: an unreadable signal age is not a fresh signal
    deadline = signal_close + max_age_ms
    for i in range(signal_idx + 1, len(series)):
        c = series[i]
        if c["open_time"] > deadline:
            return None
        if penetration_bps == 0:
            reached = c["low"] <= midpoint <= c["high"]
        elif direction == "LONG":
            # A buy limit that is merely touched may still be behind the queue.
            # Require price to trade through the midpoint by the configured
            # sensitivity; keep the fill price at the limit, not the better low.
            reached = c["low"] <= midpoint * (1.0 - penetration_bps / 10_000.0)
        else:
            reached = c["high"] >= midpoint * (1.0 + penetration_bps / 10_000.0)
        if reached:
            return i, midpoint
    return None


def rr_at_fill(sl: float, tp: float, direction: str,
               fill: float, rr_min: float) -> float:
    """R:R actually achieved at `fill`, mirroring the live re-check.

    Direction-AGNOSTIC by design, and the reason is load-bearing. The live
    equivalent (`RiskService.validate_rr_ratio`) is SIDE-AWARE, and its `abs()`
    predecessor scored a LONG whose stop sat ABOVE its entry at 93:1 — the
    historical shape this codebase calls out in `backend/AGENTS.md`. But that
    inversion is caught EARLIER and by a different rule: live gate 4b requires
    the stop to be strictly on the correct side of the price, and the bot
    flattens at market when it is not ("Setup void after fill").

    This function is a pure re-measurement of the ratio at a price that is
    inside the zone, so a stop on the wrong side cannot survive to reach it.
    Adding a signed side check here would duplicate gate 4b, and — because a
    wrong-side stop makes `reward/risk` LARGE — would in practice reject
    nothing that gate 4b had not already rejected. `rr_min` is accepted so the
    caller reads as the live gate it mirrors; it is not needed for the ratio.
    """
    risk = abs(fill - sl)
    if risk <= 0:
        return 0.0
    reward = abs(tp - fill)
    return reward / risk


@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    direction: str
    entry: float
    stop: float
    take_profit: float
    entry_idx: int
    # `entry_idx` indexes the signal's OWN interval, so it is meaningless
    # without knowing which. Both of these are required to interpret it — an
    # index into k1h and an index into k5m are not comparable, and reading
    # `k1h[t.entry_idx]` for a 5m signal silently returns the wrong bar.
    timeframe: str = "1h"
    # open_time of the bar the signal came from; the fill is strictly after it.
    signal_open_time: int = 0
    exit_idx: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # TP_HIT / SL_HIT
    pnl_r: float = 0.0
    # Gross minus round-trip costs. The harness used to report `pnl_r` alone,
    # so expectancy was GROSS and a strategy that only loses money after the
    # 0.07% round trip read as an edge.
    net_r: float = 0.0

    @property
    def open(self) -> bool:
        return self.exit_idx is None

    def finalise(self) -> None:
        """Compute net R once the exit is known."""
        risk = getattr(self, "_risk_dist", abs(self.entry - self.stop))
        self.net_r = self.pnl_r - fee_in_r(self.entry, risk)


@dataclass
class BacktestResult:
    symbol: str
    candles: int
    trades: list[BacktestTrade] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)  # filter -> count

    @property
    def closed(self) -> list[BacktestTrade]:
        return [t for t in self.trades if not t.open]

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed if t.pnl_r > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.closed if t.pnl_r < 0)

    @property
    def total_r(self) -> float:
        return sum(t.pnl_r for t in self.closed)

    @property
    def total_net_r(self) -> float:
        return sum(t.net_r for t in self.closed)

    @property
    def expectancy_net_r(self) -> float:
        """Average NET R per closed trade — the number to trust."""
        n = len(self.closed)
        return (self.total_net_r / n) if n else 0.0

    @property
    def win_rate(self) -> float:
        n = len(self.closed)
        return (self.wins / n * 100.0) if n else 0.0

    @property
    def expectancy_r(self) -> float:
        """Average R per closed trade (the key edge metric)."""
        n = len(self.closed)
        return (self.total_r / n) if n else 0.0

    @property
    def trades_per_day(self) -> float:
        # assume the caller fetched daily candles; approximate day count
        days = max(1.0, self.candles / 24.0)  # for 1h candles
        return len(self.trades) / days

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "candles": self.candles,
            "signals": len(self.trades),
            "closed": len(self.closed),
            "open": len(self.trades) - len(self.closed),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1),
            "total_r": round(self.total_r, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "net_r": round(self.total_net_r, 2),
            "expectancy_net_r": round(self.expectancy_net_r, 3),
            "fee_round_trip": FEE_ROUND_TRIP,
            "trust": "net_r is the number to trust; total_r is gross of costs",
            "trades_per_day": round(self.trades_per_day, 1),
            "skipped": self.skipped,
        }


class Backtester:
    """Replays scan_symbol's logic over historical candles, simulating fills.

    Settings mirror the live defaults; pass overrides to A/B filter changes.
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        # Trailing defaults mirror production (routers/settings.py). Without
        # them the harness models static SL/TP while live runs BE + profit-lock,
        # and the 2026-10-15 audit measured 81% of live exits at ~0R — a
        # distribution static exits cannot produce. Pass None values to disable.
        self.trailing = {
            "be_trail_trigger_r": self.settings.get("be_trail_trigger_r", 0.5),
            "profit_lock_trigger_r": self.settings.get("profit_lock_trigger_r", 1.0),
            "profit_lock_trail_r": self.settings.get("profit_lock_trail_r", 0.5),
        }
        self.rr_min = float(self.settings.get("rr_min", 3.0))
        self.risk_percent = float(self.settings.get("risk_percent", 1.0))
        self.balance = float(self.settings.get("backtest_balance", 10000.0))
        self.trade_neutral = bool(self.settings.get("trade_neutral_markets", True))
        self.min_strength = str(self.settings.get("trade_min_trend_strength", "MODERATE")).upper()
        self.volume_filter_enabled = bool(self.settings.get("volume_filter_enabled", False))
        self.regime_filter_enabled = bool(self.settings.get("regime_filter_enabled", True))
        self.min_risk_pct = float(self.settings.get("min_risk_pct", 0.0005))
        self.signal_max_age_ms = int(
            self.settings.get("signal_max_age_minutes", DEFAULT_SIGNAL_MAX_AGE_MIN)) * 60_000
        self.entry_fill_penetration_bps = float(
            self.settings.get("backtest_entry_penetration_bps", 0.0))
        # Strategy toggles, matching the live setting names so the same settings
        # dict drives both (see `_detect_candidates` for why these matter).
        self.fvg_enabled = bool(self.settings.get("strategy_fvg_enabled", True))
        self.three_candle_enabled = bool(self.settings.get("strategy_3candle_enabled", True))
        self.choch_enabled = bool(self.settings.get("strategy_choch_enabled", True))
        self._strength_rank = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}
        self._context_cache: dict[int, tuple[float, object, object]] = {}
        self._last_fvg_idx: Optional[int] = None
        self._last_choch_idx: Optional[int] = None
        self._last_choch_signature: Optional[tuple[str, int]] = None

    def _source_index(self, candles: list[dict], at_ms: int) -> int:
        """Index in `candles` of the last bar fully closed at `at_ms`.

        This is the whole point of the fix: it maps a wall-clock instant into
        whichever interval the signal came from, instead of the old proportional
        index arithmetic that stitched together series spanning different periods.
        """
        return last_closed_before(candles, at_ms)

    @staticmethod
    def _advance_closed_index(candles: list[dict], index: int, at_ms: int) -> int:
        """Advance a sorted candle cursor to the last bar closed by ``at_ms``."""
        while index + 1 < len(candles):
            close_time = candles[index + 1].get("close_time")
            if close_time is None or close_time >= at_ms:
                break
            index += 1
        return index

    def _detect_candidates(self, k1h: list[dict], k5m: list[dict], k15m: list[dict],
                           at_ms: int) -> tuple[list[tuple], object, object]:
        """Detect new signals from bars closed by `at_ms`.

        The scan clock is the close of each 5m bar. A 3CANDLE can therefore be
        new on every scan, while FVG and CHoCH are evaluated only when their
        own 1h or 15m interval advances. Candidate tuples include the index of
        their signal bar in its source interval.
        """
        k1h_idx = self._source_index(k1h, at_ms)
        k5m_idx = self._source_index(k5m, at_ms)
        k15m_idx = self._source_index(k15m, at_ms)
        return self._detect_candidates_at(
            k1h, k5m, k15m, at_ms, k1h_idx, k5m_idx, k15m_idx)

    def _detect_candidates_at(
        self, k1h: list[dict], k5m: list[dict], k15m: list[dict], at_ms: int,
        k1h_idx: int, k5m_idx: int, k15m_idx: int,
    ) -> tuple[list[tuple], object, object]:
        """Detect only signals confirmed on the latest closed interval bars."""
        if k1h_idx < 0 or k5m_idx < 0 or k15m_idx < 0:
            return [], None, None

        # need enough history for indicators
        k1h_u = k1h[:k1h_idx + 1]
        if len(k1h_u) < 60:
            return [], None, None
        k5m_u = k5m[:k5m_idx + 1]
        k15m_u = k15m[:k15m_idx + 1]
        if k1h_idx not in self._context_cache:
            self._context_cache[k1h_idx] = (
                calculate_atr(k1h_u, 14) or 0.0,
                analyze_trend_bias([c["close"] for c in k1h_u]),
                analyze_market_regime(
                    [c["high"] for c in k1h_u],
                    [c["low"] for c in k1h_u],
                    [c["close"] for c in k1h_u],
                ),
            )
        atr, bias, regime = self._context_cache[k1h_idx]

        # Strategy toggles, mirroring signal_service.scan_symbol. Without them a
        # backtest cannot isolate one strategy's fill path: `run` takes a single
        # position per detection bar and candidates are appended FVG, 3CANDLE,
        # CHOC — the same order live uses — so FVG starves the others on any
        # data where it fires often. Live has had these gates since DECISION.md
        # D1; the harness silently did not, which is why FVG-only results could
        # never be checked against a 3CANDLE-only or CHOC-only run.
        cands: list[tuple] = []
        if self.fvg_enabled and self._last_fvg_idx != k1h_idx:
            self._last_fvg_idx = k1h_idx
            for f in detect_fvg(k1h_u[-3:], ""):
                e = evaluate_fvg_entry(f, atr, self.rr_min)
                if e["is_valid"]:
                    cands.append(("FVG", "LONG" if f.direction == "BULLISH" else "SHORT", e,
                                  k1h_u, "1h", (f.zone_min, f.zone_max), k1h_idx))
        if self.three_candle_enabled:
            for p in detect_three_candles(k5m_u[-3:], "", "5m"):
                e = evaluate_three_candle_entry(p, atr, self.rr_min)
                if e["is_valid"]:
                    cands.append(("3CANDLE", "LONG" if p.direction == "BULLISH" else "SHORT", e,
                                  k5m_u, "5m", (p.entry_zone_min, p.entry_zone_max), k5m_idx))
        if self.choch_enabled and self._last_choch_idx != k15m_idx:
            self._last_choch_idx = k15m_idx
            found = [ch for ch in detect_choch(k15m_u, "", "15m", atr, self.rr_min)
                     if ch.is_valid]
            if found:
                ch = found[-1]
                signature = (ch.direction, ch.index)
                if signature != self._last_choch_signature:
                    e = {"entry_price": (ch.pullback_entry_zone[0] + ch.pullback_entry_zone[1]) / 2,
                         "stop_loss": ch.stop_loss, "take_profit": ch.take_profit,
                         "rr_ratio": ch.rr_ratio or 0.0, "is_valid": True}
                    cands.append(("CHOC", "LONG" if ch.direction == "BULLISH" else "SHORT", e,
                                  k15m_u, "15m", (ch.pullback_entry_zone[0],
                                                  ch.pullback_entry_zone[1]), k15m_idx))
                self._last_choch_signature = signature
            else:
                self._last_choch_signature = None
        return cands, bias, regime

    def _passes_filters(self, direction: str, rr: float, entry: float, sl: float,
                        bias, vol_candles: list[dict], skip: dict, regime=None) -> bool:
        """Apply the same filter chain as scan_symbol. Returns True if tradable."""
        min_rank = self._strength_rank.get(self.min_strength, 2)
        aligned = ((bias.direction == "BULLISH" and direction == "LONG") or
                   (bias.direction == "BEARISH" and direction == "SHORT"))

        if bias.direction == "NEUTRAL" and not self.trade_neutral:
            skip["neutral_market"] = skip.get("neutral_market", 0) + 1
            return False
        if aligned and self._strength_rank.get(bias.strength, 2) < min_rank:
            skip["weak_trend"] = skip.get("weak_trend", 0) + 1
            return False
        if bias.direction == "BEARISH" and direction == "LONG":
            skip["counter_trend"] = skip.get("counter_trend", 0) + 1
            return False
        if bias.direction == "BULLISH" and direction == "SHORT":
            skip["counter_trend"] = skip.get("counter_trend", 0) + 1
            return False
        if bias.rsi_zone == "OVERBOUGHT" and direction == "LONG":
            skip["rsi_overbought"] = skip.get("rsi_overbought", 0) + 1
            return False
        if bias.rsi_zone == "OVERSOLD" and direction == "SHORT":
            skip["rsi_oversold"] = skip.get("rsi_oversold", 0) + 1
            return False
        regime_bypasses_volume = False
        if self.regime_filter_enabled:
            if regime is None:
                skip["unknown_regime"] = skip.get("unknown_regime", 0) + 1
                return False
            if regime.regime == "RANGING":
                skip["ranging_regime"] = skip.get("ranging_regime", 0) + 1
                return False
            if regime.regime in ("STRONG_BULL", "STRONG_BEAR"):
                regime_ok, _ = should_trade_regime(regime, direction)
                if not regime_ok:
                    skip["regime_counter_trend"] = skip.get("regime_counter_trend", 0) + 1
                    return False
                # The live signal pipeline's strong-regime branch bypasses its
                # volume check; preserve that behavior for the replay.
                regime_bypasses_volume = True

        if (self.volume_filter_enabled and not regime_bypasses_volume
                and not get_volume_confirmed_filter(direction, vol_candles).passes_filter):
            skip["volume"] = skip.get("volume", 0) + 1
            return False
        if rr < self.rr_min:
            skip["rr"] = skip.get("rr", 0) + 1
            return False
        # min-risk guard (mirrors strategy_service)
        risk_dist = abs(entry - sl)
        if risk_dist <= 0 or risk_dist < entry * self.min_risk_pct:
            skip["min_risk"] = skip.get("min_risk", 0) + 1
            return False
        return True

    def _resolve_exit(self, trade: BacktestTrade, series: list[dict]) -> None:
        """Walk the trade's OWN interval from the fill bar onward, closing on
        whichever of SL/TP the range reaches first — with the live BE and
        profit-lock trailing rules applied as the trade moves in profit.

        The exit MUST run on the same interval as the entry. Evaluating a 5m
        entry against 1h bars had two defects: an SL hit ten minutes after a
        mid-hour fill is invisible inside an hourly range, and the first bar
        checked can OPEN before the fill — lookahead on the exit path, which the
        previous docstring denied while doing it.

        TRAILING (added 2026-10-19). The harness previously modelled static
        SL/TP only, while production runs `be_trail_trigger_r` and
        `profit_lock_*` (position_manager._check_be_trailing:391 and
        _check_profit_trailing:430). Live converts a large share of -1R
        stop-outs into ~0R scratches — the 2026-10-15 audit found 81% of exits
        at approximately 0R — so the static model's exit distribution is not the
        live one, and every expectancy number it produced described a strategy
        nobody runs.

        Stops fill at the worse of the stop price and the bar open when a candle
        gaps through them. The fill candle is checked for a stop, but its target
        and trailing extrema are ignored because OHLC data cannot establish
        whether those prices occurred before or after the entry fill.

        Both rules are mirrored exactly, including their one-way guard (a stop
        only ever moves toward profit, never back):

            profit >= be_trail_trigger_r * risk  -> stop moves to entry
            profit >= profit_lock_trigger_r * risk -> stop trails to
                                                     extreme -/+ trail_r

        WITHIN-BAR ORDERING, and it is deliberately pessimistic: the existing
        stop is tested against the bar's range FIRST, and only then is the stop
        tightened using that same bar's extreme. A bar is a range with no
        internal path, so crediting a tighter stop before testing would let a
        trailing stop "catch" a move it could not have been placed in time for.
        The live engine re-checks every ~30s and would often do better than this.

        `run` resolves each trade to completion before resuming its 5m scan and
        holds the simulated slot through the exit candle close. This is a
        conservative single-position replay, not a portfolio event loop;
        `max_open > 1` is not supported.
        """
        t = trade
        # Live defaults (routers/settings.py). `None` disables a rule.
        be_r = self.trailing.get("be_trail_trigger_r")
        pl_trigger_r = self.trailing.get("profit_lock_trigger_r")
        pl_trail_r = self.trailing.get("profit_lock_trail_r", 0.5)

        entry = t.entry
        risk = abs(entry - t.stop)
        if risk <= 0:
            risk = abs(t.take_profit - entry) or 1.0

        stop = t.stop

        for j in range(t.entry_idx, len(series)):
            c = series[j]
            hi, lo = c["high"], c["low"]
            try:
                bar_open = float(c.get("open", stop))
                if not math.isfinite(bar_open):
                    bar_open = stop
            except (TypeError, ValueError):
                bar_open = stop

            # 1. Test the CURRENT stop against this bar before tightening it.
            if t.direction == "LONG":
                if lo <= stop:
                    fill = min(stop, bar_open)
                    self._close(t, j, fill, "BE_STOP" if stop != t.stop else "SL_HIT")
                    return
                if j == t.entry_idx:
                    continue
                if hi >= t.take_profit:
                    self._close(t, j, t.take_profit, "TP_HIT")
                    return
            else:
                if hi >= stop:
                    fill = max(stop, bar_open)
                    self._close(t, j, fill, "BE_STOP" if stop != t.stop else "SL_HIT")
                    return
                if j == t.entry_idx:
                    continue
                if lo <= t.take_profit:
                    self._close(t, j, t.take_profit, "TP_HIT")
                    return

            # 2. Tighten the stop for SUBSEQUENT bars, mirroring the live
            #    one-way guards. Uses this bar's favourable extreme.
            if t.direction == "LONG":
                profit = hi - entry
                if pl_trigger_r and profit >= pl_trigger_r * risk:
                    new_stop = hi - pl_trail_r * risk
                    if new_stop > stop:
                        stop = new_stop
                elif be_r and profit >= be_r * risk and stop < entry:
                    stop = entry
            else:
                profit = entry - lo
                if pl_trigger_r and profit >= pl_trigger_r * risk:
                    new_stop = lo + pl_trail_r * risk
                    if new_stop < stop:
                        stop = new_stop
                elif be_r and profit >= be_r * risk and stop > entry:
                    stop = entry
        # Ran out of data with the trade still open: leave `exit_idx` None so
        # `BacktestResult.open` counts it and it stays out of expectancy.

    def run(self, symbol: str, k1h: list[dict], k5m: list[dict], k15m: list[dict],
            max_open: int = 1) -> BacktestResult:
        """Scan each closed 5m bar and resolve one position at a time.

        No-lookahead guarantees, precisely:
          - detection uses only candles CLOSED at the scan time,
            so the still-forming 1h bar is excluded from the detectors AND from
            the bias the filters read (they share one truncated view);
          - a fill lands strictly AFTER the signal bar, on the signal's own
            interval, at the zone midpoint;
          - the exit scan starts strictly after the fill bar, on that same
            interval.

        See `_resolve_exit` for what this deliberately does NOT model.
        """
        result = BacktestResult(symbol=symbol, candles=len(k1h))
        if max_open != 1:
            logger.warning("Backtester supports one open position; max_open=%s ignored", max_open)
        busy_until_ms = -1
        indices = {"1h": -1, "5m": -1, "15m": -1}
        series = {"1h": k1h, "5m": k5m, "15m": k15m}

        for scan_candle in k5m:
            close_time = scan_candle.get("close_time")
            if close_time is None:
                continue
            at_ms = int(close_time) + 1
            for tf in indices:
                indices[tf] = self._advance_closed_index(series[tf], indices[tf], at_ms)
            if indices["1h"] < 59:
                continue
            cands, bias, regime = self._detect_candidates_at(
                k1h, k5m, k15m, at_ms, indices["1h"], indices["5m"], indices["15m"])
            if bias is None:
                continue
            if at_ms < busy_until_ms:
                continue
            for candidate in cands:
                strategy, direction, e, vol_src, tf, zone = candidate[:6]
                signal_idx = (candidate[6] if len(candidate) > 6 else
                              self._source_index(vol_src, at_ms))
                sl = e["stop_loss"]
                tp = e["take_profit"]
                zone_lo, zone_hi = zone
                # Live gates the PRE-fill chain on the zone MIDPOINT, because
                # that is the price the LIMIT will actually rest at. The
                # detector's point estimate (`e["entry_price"]`) is a different
                # number — 3CANDLE's is c1.high while the zone is
                # c1.low..c1.high — so filtering on it gated on a price the
                # order would never be at.
                entry = (zone_lo + zone_hi) / 2.0
                vol_window = vol_src[-15:-5] if len(vol_src) > 15 else vol_src
                if not self._passes_filters(direction, e["rr_ratio"], entry, sl,
                                            bias, vol_window, result.skipped, regime=regime):
                    continue
                # Live fill semantics: a LIMIT resting at the zone midpoint on
                # the signal's OWN interval, not the next 1h open. Filling on the
                # wrong interval was a 12x time-scale mismatch.
                tf_candles = {"1h": k1h, "5m": k5m, "15m": k15m}[tf]
                sig_idx = signal_idx
                filled = fill_at_midpoint(
                    tf_candles, sig_idx, zone_lo, zone_hi, self.signal_max_age_ms,
                    direction=direction,
                    penetration_bps=self.entry_fill_penetration_bps,
                )
                if filled is None:
                    result.skipped["no_fill"] = result.skipped.get("no_fill", 0) + 1
                    continue
                fill_idx, fill = filled
                if rr_at_fill(sl, tp, direction, fill, self.rr_min) < self.rr_min:
                    result.skipped["rr_at_fill"] = result.skipped.get("rr_at_fill", 0) + 1
                    continue
                trade = BacktestTrade(
                    symbol=symbol, strategy=strategy, direction=direction,
                    entry=fill, stop=sl, take_profit=tp, entry_idx=fill_idx,
                    timeframe=tf,
                    signal_open_time=tf_candles[sig_idx]["open_time"])
                # pnl_r uses the frozen entry-to-stop distance (mirrors
                # positions.initial_risk_distance)
                risk_dist = abs(fill - sl)
                if risk_dist <= 0:
                    continue
                trade._risk_dist = risk_dist  # type: ignore[attr-defined]
                result.trades.append(trade)
                self._resolve_exit(trade, tf_candles)
                if trade.exit_idx is None:
                    busy_until_ms = float("inf")
                else:
                    exit_close = tf_candles[trade.exit_idx].get("close_time")
                    busy_until_ms = (float("inf") if exit_close is None
                                     else int(exit_close) + 1)
                break

        return result

    def _close(self, t: BacktestTrade, idx: int, price: float, reason: str):
        t.exit_idx = idx
        t.exit_price = price
        t.exit_reason = reason
        risk_dist = getattr(t, "_risk_dist", abs(t.entry - t.stop))
        t.pnl_r = ((price - t.entry) if t.direction == "LONG"
                   else (t.entry - price)) / risk_dist
        t.finalise()


class LookaheadProbeBacktester(Backtester):
    """Deliberately broken variant used ONLY by tests.

    Detects against the END of the series instead of the bar at hand, which is
    the exact shape of the original OB+FVG lookahead bug (an FVG at 1h bar `i`
    is only knowable at that hour's CLOSE). It exists so the test suite can
    prove it is capable of detecting lookahead at all — a suite that cannot
    fail this way cannot be trusted when it says "no lookahead".
    """

    def _detect_candidates(self, k1h, k5m, k15m, at_ms):
        candidates, _, regime = super()._detect_candidates(k1h, k5m, k15m, at_ms)
        # Deliberately leak the final hourly context into every historical scan.
        bias = analyze_trend_bias([c["close"] for c in k1h])
        return candidates, bias, regime


async def run_backtest(symbol: str, days: int = 30, settings: Optional[dict] = None,
                       binance=None, end_days_ago: int = 0) -> BacktestResult:
    """Fetch a full `days` window (paginated) and run the backtest.

    All three intervals are pinned to ONE `[start, end)` window and fetched
    through `fetch_klines`, so the harness cannot again silently test 3.5 days
    of 5m against a month of 1h.
    """
    created = False
    if binance is None:
        from backend.services.binance_service import BinanceService
        binance = BinanceService()
        created = True
    try:
        start_ms, end_ms = window_for_days(days)
        if end_days_ago:
            # Shift the window BACKWARD so an earlier slice can be tested on its
            # own. This is what makes an out-of-sample split possible: the two
            # halves must be disjoint windows, not one window sampled twice.
            end_ms -= end_days_ago * 86_400_000
            start_ms -= end_days_ago * 86_400_000
        series = {}
        for tf in ("1h", "5m", "15m"):
            series[tf] = await fetch_klines(
                lambda s, i, a, b: binance.get_klines_window(s, i, a, b),
                symbol, tf, start_ms, end_ms)
        series = align_window(series, start_ms, end_ms)
        for tf, candles in series.items():
            if len(candles) < bars_expected(tf, days) * 0.9:
                raise RuntimeError(
                    f"{symbol} {tf}: got {len(candles)} bars for a {days}d window, "
                    f"expected ~{bars_expected(tf, days)} — pagination failed")
        bt = Backtester(settings=settings)
        return bt.run(symbol, series["1h"], series["5m"], series["15m"])
    finally:
        if created:
            await binance.close()
