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

logger = logging.getLogger(__name__)


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
        self._strength_rank = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}

    def _detect_candidates(self, k1h: list[dict], k5m: list[dict], k15m: list[dict],
                           at_idx: int) -> list[tuple[str, str, dict]]:
        """Detect + evaluate signals using only candles up to at_idx (no lookahead).

        Mirrors signal_service.scan_symbol: 1h context (ATR/trend), FVG on 1h,
        3-candle on 5m, CHoCH on 15m.
        """
        k1h_u = k1h[:at_idx + 1]
        # need enough history for indicators
        if len(k1h_u) < 60:
            return []
        atr = calculate_atr(k1h_u, 14) or 0.0
        bias = analyze_trend_bias([c["close"] for c in k1h_u])

        # 5m/15m aligned to the same wall-clock index (approximate: use the
        # tail of each series ending at the same relative point)
        k5m_u = k5m[: len(k5m) * (at_idx + 1) // len(k1h)] or k5m
        k15m_u = k15m[: len(k15m) * (at_idx + 1) // len(k1h)] or k15m

        cands = []
        for f in detect_fvg(k1h_u, ""):
            e = evaluate_fvg_entry(f, atr, self.rr_min)
            if e["is_valid"]:
                cands.append(("FVG", "LONG" if f.direction == "BULLISH" else "SHORT", e,
                              k1h_u, "1h"))
        for p in detect_three_candles(k5m_u, "", "5m"):
            e = evaluate_three_candle_entry(p, atr, self.rr_min)
            if e["is_valid"]:
                cands.append(("3CANDLE", "LONG" if p.direction == "BULLISH" else "SHORT", e,
                              k5m_u, "5m"))
        for ch in detect_choch(k15m_u, "", "15m", atr, self.rr_min):
            if ch.is_valid:
                e = {"entry_price": (ch.pullback_entry_zone[0] + ch.pullback_entry_zone[1]) / 2,
                     "stop_loss": ch.stop_loss, "take_profit": ch.take_profit,
                     "rr_ratio": ch.rr_ratio or 0.0, "is_valid": True}
                cands.append(("CHOC", "LONG" if ch.direction == "BULLISH" else "SHORT", e,
                              k15m_u, "15m"))
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
        SL/TP exits against subsequent 1h candles. No lookahead: a signal
        detected at index i fills at i+1's open and is only checked against
        candles after the entry."""
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
            for strategy, direction, e, vol_src, tf in cands:
                entry = e["entry_price"]
                sl = e["stop_loss"]
                tp = e["take_profit"]
                vol_candles = (k1h if tf == "1h" else k5m)
                vol_window = vol_src[-15:-5] if len(vol_src) > 15 else vol_src
                if not self._passes_filters(direction, e["rr_ratio"], entry, sl,
                                            bias, vol_window, result.skipped):
                    continue
                # fill at the NEXT candle's open (no lookahead)
                fill = k1h[i + 1]["open"] if i + 1 < len(k1h) else entry
                trade = BacktestTrade(
                    symbol=symbol, strategy=strategy, direction=direction,
                    entry=fill, stop=sl, take_profit=tp, entry_idx=i + 1)
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
