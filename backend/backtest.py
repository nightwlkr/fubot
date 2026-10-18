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
from dataclasses import dataclass, field
from typing import Optional

from backend.indicators.atr import calculate_atr
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.choch import detect_choch
from backend.indicators.trend import analyze_trend_bias
from backend.indicators.volume_profile import get_volume_confirmed_filter
from backend.backtest_data import last_closed_before

logger = logging.getLogger(__name__)


# Live entry semantics, mirrored so the backtest measures what the bot does.
# `StrategyService._enter_with_limit` places a LIMIT at the entry-zone midpoint,
# waits up to `signal_max_age_minutes`, then RE-VALIDATES R:R at the price that
# actually filled and skips the signal if it no longer qualifies.
DEFAULT_SIGNAL_MAX_AGE_MIN = 60


def fill_at_midpoint(k5m: list[dict], signal_idx: int,
                     zone_lo: float, zone_hi: float,
                     max_age_ms: int) -> Optional[tuple[int, float]]:
    """First bar at/after `signal_idx` whose range touches the zone midpoint.

    Returns `(bar_index, fill_price)` or None. The fill price is the MIDPOINT,
    not the bar's open — that is what a resting LIMIT at the midpoint gets.
    Scanning starts at `signal_idx + 1`: the signal bar is already closed when
    the signal exists, so filling on it would be lookahead.
    """
    midpoint = (zone_lo + zone_hi) / 2.0
    if not 0 <= signal_idx < len(k5m):
        return None
    deadline = k5m[signal_idx]["open_time"] + max_age_ms
    for i in range(signal_idx + 1, len(k5m)):
        c = k5m[i]
        if c["open_time"] > deadline:
            return None
        if c["low"] <= midpoint <= c["high"]:
            return i, midpoint
    return None


def rr_at_fill(sl: float, tp: float, direction: str,
               fill: float, rr_min: float) -> float:
    """R:R actually achieved at `fill`, mirroring the live re-check."""
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
    exit_idx: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # TP_HIT / SL_HIT
    pnl_r: float = 0.0

    @property
    def open(self) -> bool:
        return self.exit_idx is None


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
            "trades_per_day": round(self.trades_per_day, 1),
            "skipped": self.skipped,
        }


class Backtester:
    """Replays scan_symbol's logic over historical candles, simulating fills.

    Settings mirror the live defaults; pass overrides to A/B filter changes.
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self.rr_min = float(self.settings.get("rr_min", 3.0))
        self.risk_percent = float(self.settings.get("risk_percent", 1.0))
        self.balance = float(self.settings.get("backtest_balance", 10000.0))
        self.trade_neutral = bool(self.settings.get("trade_neutral_markets", False))
        self.min_strength = str(self.settings.get("trade_min_trend_strength", "MODERATE")).upper()
        self.volume_filter_enabled = bool(self.settings.get("volume_filter_enabled", True))
        self.min_risk_pct = float(self.settings.get("min_risk_pct", 0.0005))
        self.signal_max_age_ms = int(
            self.settings.get("signal_max_age_minutes", DEFAULT_SIGNAL_MAX_AGE_MIN)) * 60_000
        self._strength_rank = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}

    def _source_index(self, candles: list[dict], at_ms: int) -> int:
        """Index in `candles` of the last bar fully closed at `at_ms`.

        This is the whole point of the fix: it maps a wall-clock instant into
        whichever interval the signal came from, instead of the old proportional
        index arithmetic that stitched together series spanning different periods.
        """
        return last_closed_before(candles, at_ms)

    def _detect_candidates(self, k1h: list[dict], k5m: list[dict], k15m: list[dict],
                           at_idx: int) -> list[tuple]:
        """Detect + evaluate signals using only candles knowable at `at_idx`.

        Mirrors signal_service.scan_symbol: 1h context (ATR/trend), FVG on 1h,
        3-candle on 5m, CHoCH on 15m.

        Each interval's "up to now" view is truncated by WALL-CLOCK TIME via
        `_source_index`, not by proportional index arithmetic. The old code
        scaled each lower series' length by the 1h index, which mapped 1h bar 500
        (hour 500) onto 5m bar 500 (hour 41.7) whenever the two series spanned
        different periods — so the trend filter read a different month than the
        entries it filtered.

        Returns `(strategy, direction, evaluation, vol_source, timeframe, zone)`
        where `zone` is the entry ZONE `(lo, hi)` the live bot rests its LIMIT
        midpoint inside — not the point estimate. All three detectors expose a
        real zone, so no synthetic fallback is needed here.
        """
        at_ms = k1h[at_idx]["open_time"]
        k1h_idx = self._source_index(k1h, at_ms)
        k5m_idx = self._source_index(k5m, at_ms)
        k15m_idx = self._source_index(k15m, at_ms)
        if k1h_idx < 0 or k5m_idx < 0 or k15m_idx < 0:
            return []

        # need enough history for indicators
        k1h_u = k1h[:k1h_idx + 1]
        if len(k1h_u) < 60:
            return []
        k5m_u = k5m[:k5m_idx + 1]
        k15m_u = k15m[:k15m_idx + 1]
        atr = calculate_atr(k1h_u, 14) or 0.0
        bias = analyze_trend_bias([c["close"] for c in k1h_u])

        cands: list[tuple] = []
        for f in detect_fvg(k1h_u, ""):
            e = evaluate_fvg_entry(f, atr, self.rr_min)
            if e["is_valid"]:
                cands.append(("FVG", "LONG" if f.direction == "BULLISH" else "SHORT", e,
                              k1h_u, "1h", (f.zone_min, f.zone_max)))
        for p in detect_three_candles(k5m_u, "", "5m"):
            e = evaluate_three_candle_entry(p, atr, self.rr_min)
            if e["is_valid"]:
                cands.append(("3CANDLE", "LONG" if p.direction == "BULLISH" else "SHORT", e,
                              k5m_u, "5m", (p.entry_zone_min, p.entry_zone_max)))
        for ch in detect_choch(k15m_u, "", "15m", atr, self.rr_min):
            if ch.is_valid:
                e = {"entry_price": (ch.pullback_entry_zone[0] + ch.pullback_entry_zone[1]) / 2,
                     "stop_loss": ch.stop_loss, "take_profit": ch.take_profit,
                     "rr_ratio": ch.rr_ratio or 0.0, "is_valid": True}
                cands.append(("CHOC", "LONG" if ch.direction == "BULLISH" else "SHORT", e,
                              k15m_u, "15m", (ch.pullback_entry_zone[0],
                                              ch.pullback_entry_zone[1])))
        return cands

    def _passes_filters(self, direction: str, rr: float, entry: float, sl: float,
                        bias, vol_candles: list[dict], skip: dict) -> bool:
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
        if (self.volume_filter_enabled
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

    def run(self, symbol: str, k1h: list[dict], k5m: list[dict], k15m: list[dict],
            max_open: int = 1) -> BacktestResult:
        """Walk candles forward; on each 1h close detect signals, simulate
        SL/TP exits against subsequent 1h candles. No lookahead: a signal is
        only ever evaluated against candles that had CLOSED, it fills on a LATER
        bar of its OWN interval, and it is only checked against candles after
        the entry."""
        result = BacktestResult(symbol=symbol, candles=len(k1h))
        open_trade: Optional[BacktestTrade] = None
        vol_stride = 10  # re-evaluate volume every N candles (matches scan cadence)

        for i in range(60, len(k1h)):
            # --- 1. manage an open trade against this candle's range ---
            if open_trade is not None:
                hi, lo = k1h[i]["high"], k1h[i]["low"]
                t = open_trade
                if t.direction == "LONG":
                    # check SL first (conservative: stop before target if both hit)
                    if lo <= t.stop:
                        self._close(t, i, t.stop, "SL_HIT")
                    elif hi >= t.take_profit:
                        self._close(t, i, t.take_profit, "TP_HIT")
                else:
                    if hi >= t.stop:
                        self._close(t, i, t.stop, "SL_HIT")
                    elif lo <= t.take_profit:
                        self._close(t, i, t.take_profit, "TP_HIT")
                if not open_trade.open:
                    open_trade = None

            # --- 2. detect new signals (skip if a trade is open) ---
            if open_trade is not None:
                continue
            if i % vol_stride != 0:
                continue

            bias = analyze_trend_bias([c["close"] for c in k1h[:i + 1]])
            cands = self._detect_candidates(k1h, k5m, k15m, i)
            for strategy, direction, e, vol_src, tf, zone in cands:
                entry = e["entry_price"]
                sl = e["stop_loss"]
                tp = e["take_profit"]
                zone_lo, zone_hi = zone
                vol_candles = (k1h if tf == "1h" else k5m)
                vol_window = vol_src[-15:-5] if len(vol_src) > 15 else vol_src
                if not self._passes_filters(direction, e["rr_ratio"], entry, sl,
                                            bias, vol_window, result.skipped):
                    continue
                # Live fill semantics: a LIMIT resting at the zone midpoint on
                # the signal's OWN interval, not the next 1h open. Filling on the
                # wrong interval was a 12x time-scale mismatch.
                tf_candles = {"1h": k1h, "5m": k5m, "15m": k15m}[tf]
                sig_idx = self._source_index(tf_candles, k1h[i]["open_time"])
                filled = fill_at_midpoint(
                    tf_candles, sig_idx, zone_lo, zone_hi, self.signal_max_age_ms)
                if filled is None:
                    result.skipped["no_fill"] = result.skipped.get("no_fill", 0) + 1
                    continue
                fill_idx, fill = filled
                if rr_at_fill(sl, tp, direction, fill, self.rr_min) < self.rr_min:
                    result.skipped["rr_at_fill"] = result.skipped.get("rr_at_fill", 0) + 1
                    continue
                trade = BacktestTrade(
                    symbol=symbol, strategy=strategy, direction=direction,
                    entry=fill, stop=sl, take_profit=tp, entry_idx=fill_idx)
                # pnl_r uses the frozen entry-to-stop distance (mirrors
                # positions.initial_risk_distance)
                risk_dist = abs(fill - sl)
                if risk_dist <= 0:
                    continue
                trade._risk_dist = risk_dist  # type: ignore[attr-defined]
                result.trades.append(trade)
                open_trade = trade
                break  # one open position at a time (max_open=1)

        return result

    def _close(self, t: BacktestTrade, idx: int, price: float, reason: str):
        t.exit_idx = idx
        t.exit_price = price
        t.exit_reason = reason
        risk_dist = getattr(t, "_risk_dist", abs(t.entry - t.stop))
        t.pnl_r = ((price - t.entry) if t.direction == "LONG"
                   else (t.entry - price)) / risk_dist


async def run_backtest(symbol: str, days: int = 30, settings: Optional[dict] = None,
                       binance=None) -> BacktestResult:
    """Fetch historical klines and run a backtest. `binance` is a BinanceService
    (injectable for tests); creates one if not given."""
    created = False
    if binance is None:
        from backend.services.binance_service import BinanceService
        binance = BinanceService()
        created = True
    try:
        limit = min(days * 24, 1000)
        k1h = await binance.get_klines(symbol, "1h", limit)
        k5m = await binance.get_klines(symbol, "5m", limit)
        k15m = await binance.get_klines(symbol, "15m", limit)
        bt = Backtester(settings=settings)
        return bt.run(symbol, k1h, k5m, k15m)
    finally:
        if created:
            await binance.close()
