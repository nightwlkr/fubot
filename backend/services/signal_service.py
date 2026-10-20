"""
Signal Service — orchestrates indicator modules and emits structured trading signals.

Pipeline (FUBOT_STEPS PHASE2_STEP7):
  1. Fetch 1h / 5m / 15m candles in parallel (200 each)
  2. ATR(14) on 1h for stop sizing; trend bias on 1h closes
  3. Detect FVG + 3-Candle on 5m, CHoCH on 15m
  4. Filters: trend bias → volume confirmation → minimum R:R
  5. Persist PENDING signals and SKIPPED candidates (with skip_reason)

Wired by: BotEngine (scan loop) and the /signals API route.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from backend.indicators.atr import calculate_atr
from backend.indicators.trend import analyze_trend_bias
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.indicators.order_block import detect_order_blocks, fvg_within_order_block
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.choch import detect_choch
from backend.indicators.volume_profile import get_volume_confirmed_filter
from backend.models.signal import Signal as SignalModel

logger = logging.getLogger(__name__)

# `evaluate_fvg_entry` builds the target from `risk * rr_min`, so rr_ratio is
# mathematically exactly rr_min — but float division can land a hair under it and
# fail `is_valid`. This tolerance matches the backtest harness (RR_EPSILON).
RR_EPSILON = 1e-9

# OB detection needs this much 5m history before the first candle is considered
# (see detect_order_blocks' start_idx).
OB_START_IDX = 30


def detect_obfvg_candidates(
    k5m: list[dict],
    k1h: list[dict],
    atr_1h: float,
    rr_min: float,
    max_age_bars: int = 2,
    ob_lookback: int = 50,
) -> list[tuple[str, float, float, float, float, float]]:
    """Order-Block-confirmed Fair Value Gaps on the 1h timeframe.

    Returns (fvg_direction, zone_min, zone_max, stop, take_profit, rr) tuples.

    A bullish 1h FVG only qualifies when a confirmed BULLISH order block on the
    5m series overlaps its zone (and vice versa). Plain FVG alone is disabled in
    production at -0.397R/trade; the OB confluence is what separates this
    population — measured +0.432R ungated / +0.315R with the live trend gate
    over 20 symbols at R:R 3.0 (DECISION.md cycle 2).

    FRESHNESS: only FVGs on the last `max_age_bars` CLOSED 1h bars are eligible.
    The backtest entered each gap at the first 5m bar after its forming hour
    closed; a gap that formed days ago would instead be entered at today's
    price, which is a different trade and was never measured. Two bars of slack
    covers the 60s scan cadence so a fresh gap is never missed.

    OB visibility is bounded by `confirm_at_idx` (via fvg_within_order_block), so
    an OB that only confirms after the FVG cannot create confluence.
    """
    if not k5m or not k1h or atr_1h <= 0:
        return []

    # "Now" on the 5m series — a fresh FVG formed on the last closed hour, so
    # this is the correct confluence-visibility point.
    current_5m = len(k5m) - 1
    obs = detect_order_blocks(k5m, left_bars=2, right_bars=2, start_idx=OB_START_IDX)
    if not obs:
        return []

    cutoff = len(k1h) - 1 - max(0, max_age_bars)
    out: list[tuple[str, float, float, float, float, float]] = []
    for f in detect_fvg(k1h):
        if f.bar_index < cutoff:
            continue
        e = evaluate_fvg_entry(f, atr_1h, rr_min)
        if not e["is_valid"] and e["rr_ratio"] < rr_min - RR_EPSILON:
            continue
        if e["rr_ratio"] <= 0:
            continue
        if not fvg_within_order_block(
            f.zone_min, f.zone_max, f.direction,
            obs, lookback=ob_lookback, current_idx=current_5m,
        ):
            continue
        out.append((f.direction, f.zone_min, f.zone_max,
                    e["stop_loss"], e["take_profit"], e["rr_ratio"]))
    return out


@dataclass
class Signal:
    symbol: str
    strategy_type: str
    direction: str
    entry_zone_min: Decimal
    entry_zone_max: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    rr_ratio: Decimal
    volume_confirmed: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    skip_reason: Optional[str] = None
    status: str = "PENDING"
    db_id: Optional[int] = None  # signals.id when persisted


class SignalService:
    def __init__(self, session_factory=None, binance=None,
                 settings: Optional[dict] = None) -> None:
        self.session_factory = session_factory
        self.binance = binance
        self.settings = settings or {}
        self.pending: list[Signal] = []

    async def scan_symbol(self, symbol: str) -> list[Signal]:
        """
        Run the full indicator pipeline for `symbol`.
        Returns every generated candidate: PENDING signals (all filters passed)
        and SKIPPED candidates (with skip_reason populated).
        """
        if self.binance is None:
            return []

        rr_min = float(self.settings.get("rr_min", 3.0))

        # Expire stale PENDING signals for this symbol FIRST. Without this, a
        # PENDING signal whose price never returned to its zone is never
        # re-evaluated (dedup blocks fresh signals for the same
        # symbol+strategy+direction) and never expires (only evaluate_signal
        # expires, and it's not called again) — the stale row blocks trading
        # for that symbol/strategy forever.
        await self._expire_stale_pending(symbol)

        # 1. Fetch all timeframes in parallel
        try:
            k1h, k5m, k15m = await asyncio.gather(
                self.binance.get_klines(symbol, "1h", 200),
                self.binance.get_klines(symbol, "5m", 200),
                self.binance.get_klines(symbol, "15m", 200),
            )
        except Exception as exc:
            logger.warning("[%s] Candle fetch failed: %s", symbol, exc)
            return []
        if not k1h or not k5m or not k15m:
            logger.debug("[%s] Insufficient candle data", symbol)
            return []

        # 2. Context: ATR(14) on 1h, trend bias on 1h closes
        atr_1h = calculate_atr(k1h, period=14) or 0.0
        bias = analyze_trend_bias([c["close"] for c in k1h])

        # 2b. Market regime analysis (ADX + regime classification)
        from backend.indicators.regime import analyze_market_regime, should_trade_regime
        highs_1h = [c["high"] for c in k1h]
        lows_1h = [c["low"] for c in k1h]
        closes_1h = [c["close"] for c in k1h]
        regime = analyze_market_regime(highs_1h, lows_1h, closes_1h)

        # 2b. Funding rate filter — skip entries when funding is extremely negative
        # (indicates heavy short bias, potential for short squeeze or reversal).
        # Positive funding = longs pay shorts (bullish sentiment).
        # Extreme negative = shorts pay longs (bearish sentiment, possible bounce).
        funding_rate = 0.0
        try:
            funding_rate = await self.binance.get_funding_rate(symbol)
        except Exception as exc:
            logger.debug("[%s] Funding rate fetch failed: %s", symbol, exc)
        funding_filter_enabled = bool(self.settings.get("funding_rate_filter_enabled", True))
        funding_skip_threshold = float(self.settings.get("funding_skip_threshold", -0.0001))
        if funding_filter_enabled and funding_rate < funding_skip_threshold:
            logger.info("[%s] Skipping: extreme negative funding rate %.6f", symbol, funding_rate)
            return []

        # 3. Detect patterns
        candidates: list[tuple[str, str, float, float, float, float, float]] = []
        # Strategy toggles (Settings UI). DECISION.md D1 (2026-10-16): FVG is
        # disabled in production — its stop geometry (zone_min - atr_1h with a
        # 3R target) produced 15.6% targets and -0.397R/trade live (0/5). The
        # flags existed in the settings API but were never consumed; without
        # this gate a "disabled" strategy kept firing. Default True preserves
        # prior behaviour when the keys are absent.
        if bool(self.settings.get("strategy_fvg_enabled", False)):
            # FVG runs on 1h: a true 3-candle gap (c2.low > c1.high) essentially
            # never forms on liquid 5m crypto (ranges always overlap) — 0
            # detections there. On 1h, gaps do occur.
            for f in detect_fvg(k1h, symbol):
                e = evaluate_fvg_entry(f, atr_1h, rr_min)
                if e["is_valid"]:
                    direction = "LONG" if f.direction == "BULLISH" else "SHORT"
                    candidates.append(("FVG", direction, f.zone_min, f.zone_max,
                                       e["stop_loss"], e["take_profit"], e["rr_ratio"]))
        if bool(self.settings.get("strategy_3candle_enabled", True)):
            for p in detect_three_candles(k5m, symbol):
                e = evaluate_three_candle_entry(p, atr_1h, rr_min)
                if e["is_valid"]:
                    direction = "LONG" if p.direction == "BULLISH" else "SHORT"
                    candidates.append(("3CANDLE", direction, p.entry_zone_min, p.entry_zone_max,
                                       e["stop_loss"], e["take_profit"], e["rr_ratio"]))
        if bool(self.settings.get("strategy_choch_enabled", True)):
            for ch in detect_choch(k15m, symbol, "15m", atr_1h, rr_min):
                if ch.is_valid:
                    direction = "LONG" if ch.direction == "BULLISH" else "SHORT"
                    candidates.append(("CHOC", direction,
                                       ch.pullback_entry_zone[0], ch.pullback_entry_zone[1],
                                       ch.stop_loss, ch.take_profit, ch.rr_ratio))
        if bool(self.settings.get("strategy_obfvg_enabled", False)):
            max_age = int(self.settings.get("strategy_obfvg_max_age_bars", 2))
            for fdir, zmin, zmax, sl, tp, rr in detect_obfvg_candidates(
                k5m, k1h, atr_1h, rr_min, max_age_bars=max_age
            ):
                candidates.append(("OBFVG", "LONG" if fdir == "BULLISH" else "SHORT",
                                   zmin, zmax, sl, tp, rr))

        if not candidates:
            return []

        # 4/5. Apply filters and persist (deduped, one fresh session per scan)
        # Trend-strength gate settings
        trade_neutral = bool(self.settings.get("trade_neutral_markets", True))
        min_strength = str(self.settings.get("trade_min_trend_strength", "MODERATE")).upper()
        volume_filter_enabled = bool(self.settings.get("volume_filter_enabled", False))
        _strength_rank = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}
        min_rank = _strength_rank.get(min_strength, 2)

        signals: list[Signal] = []
        to_persist: list[tuple[Signal, bool]] = []
        for strategy_type, direction, zone_min, zone_max, sl, tp, rr in candidates:
            status, skip_reason, volume_confirmed = "PENDING", None, False

            # Filter 0 — trend-strength gate. Applied only to ALIGNED entries
            # (BULLISH+LONG / BEARISH+SHORT): skip weak aligned trends and
            # (by default) neutral markets, where sweeps fail in chop. The
            # counter-trend + RSI checks below remain the authority for
            # mis-aligned directions.
            aligned = ((bias.direction == "BULLISH" and direction == "LONG") or
                       (bias.direction == "BEARISH" and direction == "SHORT"))
            if bias.direction == "NEUTRAL" and not trade_neutral:
                status, skip_reason = "SKIPPED", "Trend filter: neutral market"
            elif aligned and _strength_rank.get(bias.strength, 2) < min_rank:
                status, skip_reason = (
                    "SKIPPED",
                    f"Trend filter: {bias.strength} trend below {min_strength}")
            # Filter 1 — trend bias
            elif bias.direction == "BEARISH" and direction == "LONG":
                status, skip_reason = "SKIPPED", "Trend filter: BEARISH"
            elif bias.direction == "BULLISH" and direction == "SHORT":
                status, skip_reason = "SKIPPED", "Trend filter: BULLISH"
            elif bias.rsi_zone == "OVERBOUGHT" and direction == "LONG":
                status, skip_reason = "SKIPPED", "Trend filter: RSI overbought"
            elif bias.rsi_zone == "OVERSOLD" and direction == "SHORT":
                status, skip_reason = "SKIPPED", "Trend filter: RSI oversold"
        # Filter 1b — market regime (ADX-based). Skip RANGING markets entirely
            # because SMC strategies (FVG/CHoCH) fail in chop. Also skip counter-trend
            # entries in strong regimes.
            regime_filter_enabled = bool(self.settings.get("regime_filter_enabled", True))
            if regime_filter_enabled and regime.regime == "RANGING":
                status, skip_reason = "SKIPPED", f"Regime: ranging (ADX {regime.adx:.1f})"
            elif regime_filter_enabled and regime.regime in ("STRONG_BULL", "STRONG_BEAR"):
                regime_ok, regime_reason = should_trade_regime(regime, direction)
                if not regime_ok:
                    status, skip_reason = "SKIPPED", f"Regime filter: {regime_reason}"
            else:
                # Filter 2 — volume confirmation (10 candles preceding formation).
                # Use the timeframe the pattern was detected on: 1h volume for
                # the 1h FVG and the 1h FVG leg of OB+FVG, 5m volume for 5m
                # 3-candle / 15m CHoCH.
                vol_candles = k1h[-15:-5] if strategy_type in ("FVG", "OBFVG") else k5m[-15:-5]
                volume_confirmed = get_volume_confirmed_filter(direction, vol_candles).passes_filter
                # Volume filter DISABLED by default (2026-10-13): the backtest
                # showed it blocked profitable trades without improving quality
                # (428→664 trades, expectancy +0.072→+0.082 when off). Set
                # volume_filter_enabled=true to restore it.
                if volume_filter_enabled and not volume_confirmed:
                    status, skip_reason = "SKIPPED", "Volume filter: no dominant side confirmation"
                # Filter 3 — minimum R:R
                elif rr < rr_min:
                    status, skip_reason = "SKIPPED", f"R:R {rr:.2f} below minimum {rr_min:.1f}"

            signal = Signal(
                symbol=symbol,
                strategy_type=strategy_type,
                direction=direction,
                entry_zone_min=Decimal(str(zone_min)),
                entry_zone_max=Decimal(str(zone_max)),
                stop_loss=Decimal(str(sl)),
                take_profit=Decimal(str(tp)),
                rr_ratio=Decimal(str(round(rr, 4))),
                volume_confirmed=volume_confirmed,
                status=status,
                skip_reason=skip_reason,
            )
            signals.append(signal)
            to_persist.append((signal, volume_confirmed))

        await self._persist_signals(symbol, to_persist)

        logger.info("[%s] scan: %d candidates (%d pending, %d skipped)",
                    symbol, len(signals),
                    sum(1 for s in signals if s.status == "PENDING"),
                    sum(1 for s in signals if s.status == "SKIPPED"))
        return signals

    async def _persist_signals(self, symbol: str, to_persist: list[tuple[Signal, bool]]) -> None:
        """Insert new PENDING/SKIPPED signal rows, deduped per (symbol, strategy, direction).

        A duplicate (same strategy+direction already PENDING) is NOT inserted
        and its in-memory Signal gets db_id=None. It is also marked SKIPPED here
        so the scan loop never evaluates/executes a signal with no DB row — that
        produced orphaned positions with signal_id=None.

        Only PENDING reserves the slot: a SKIPPED signal was REJECTED (counter-
        trend, weak, etc.) and must NOT block future attempts — the trend flips
        and a previously-skipped setup becomes valid. Deduping against SKIPPED
        rows made 43 stale rejections permanently block all new signals for
        those symbol+strategy+direction combos (observed live: 43 SKIPPED rows,
        zero new signals in 43 min of scanning).
        """
        if self.session_factory is None or not to_persist:
            return
        try:
            async with self.session_factory() as session:
                existing = await session.execute(
                    select(SignalModel.strategy_type, SignalModel.direction).where(
                        SignalModel.symbol == symbol,
                        SignalModel.status == "PENDING",
                    )
                )
                seen = set(existing.fetchall())
                for signal, volume_confirmed in to_persist:
                    key = (signal.strategy_type, signal.direction)
                    if key in seen:
                        # Duplicate of an existing pending/skipped signal — do
                        # not execute it (no db row), just record as deduped.
                        signal.status = "SKIPPED"
                        signal.skip_reason = "Duplicate of existing signal"
                        continue
                    model = SignalModel(
                        symbol=symbol,
                        strategy_type=signal.strategy_type,
                        direction=signal.direction,
                        entry_zone_min=signal.entry_zone_min,
                        entry_zone_max=signal.entry_zone_max,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        rr_ratio=signal.rr_ratio,
                        volume_confirmed=volume_confirmed,
                        status=signal.status,
                        skip_reason=signal.skip_reason,
                    )
                    session.add(model)
                    await session.flush()
                    signal.db_id = model.id
                    seen.add(key)
                await session.commit()
        except Exception:
            logger.exception("[%s] Failed to persist signals", symbol)

    async def _expire_stale_pending(self, symbol: str) -> None:
        """Mark stale PENDING and SKIPPED signals as EXPIRED.

        Prevents the stale-PENDING deadlock: a pending signal whose price never
        returned to its zone is never re-evaluated (dedup blocks fresh signals
        for the same symbol+strategy+direction) and never expires (only
        evaluate_signal expires, and it isn't called again). Expiring here lets
        the next scan generate a fresh signal for that symbol/strategy.

        SKIPPED rows are also expired so the rejections table doesn't grow
        unboundedly (they no longer block dedup — see _persist_signals — but
        old rows are just noise).
        """
        if self.session_factory is None:
            return
        max_age_min = float(self.settings.get("signal_max_age_minutes", 60))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_min)
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(SignalModel).where(
                        SignalModel.symbol == symbol,
                        SignalModel.status.in_(("PENDING", "SKIPPED")),
                        SignalModel.created_at < cutoff,
                    )
                )
                stale = result.scalars().all()
                if stale:
                    for s in stale:
                        s.status = "EXPIRED"
                    await session.commit()
                    logger.info("[%s] expired %d stale signal(s) "
                                "(older than %.0f min)", symbol, len(stale), max_age_min)
        except Exception:
            logger.exception("[%s] Failed to expire stale signals", symbol)

    def get_pending(self) -> list[Signal]:
        return [s for s in self.pending if s.status == "PENDING"]


_signal_service: Optional[SignalService] = None


def get_signal_service() -> SignalService:
    """Bare service (no db/binance) — the wired instance comes from main.py."""
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service
