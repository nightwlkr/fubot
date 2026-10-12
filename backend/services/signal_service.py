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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from backend.indicators.atr import calculate_atr
from backend.indicators.trend import analyze_trend_bias
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.choch import detect_choch
from backend.indicators.volume_profile import get_volume_confirmed_filter
from backend.models.signal import Signal as SignalModel

logger = logging.getLogger(__name__)


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

        # 3. Detect patterns
        candidates: list[tuple[str, str, float, float, float, float, float]] = []
        for f in detect_fvg(k5m, symbol):
            e = evaluate_fvg_entry(f, atr_1h, rr_min)
            if e["is_valid"]:
                direction = "LONG" if f.direction == "BULLISH" else "SHORT"
                candidates.append(("FVG", direction, f.zone_min, f.zone_max,
                                   e["stop_loss"], e["take_profit"], e["rr_ratio"]))
        for p in detect_three_candles(k5m, symbol):
            e = evaluate_three_candle_entry(p, atr_1h, rr_min)
            if e["is_valid"]:
                direction = "LONG" if p.direction == "BULLISH" else "SHORT"
                candidates.append(("3CANDLE", direction, p.entry_zone_min, p.entry_zone_max,
                                   e["stop_loss"], e["take_profit"], e["rr_ratio"]))
        for ch in detect_choch(k15m, symbol, "15m", atr_1h, rr_min):
            if ch.is_valid:
                direction = "LONG" if ch.direction == "BULLISH" else "SHORT"
                candidates.append(("CHOC", direction,
                                   ch.pullback_entry_zone[0], ch.pullback_entry_zone[1],
                                   ch.stop_loss, ch.take_profit, ch.rr_ratio))

        if not candidates:
            return []

        # 4/5. Apply filters and persist (deduped, one fresh session per scan)
        # Trend-strength gate settings
        trade_neutral = bool(self.settings.get("trade_neutral_markets", False))
        min_strength = str(self.settings.get("trade_min_trend_strength", "MODERATE")).upper()
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
            else:
                # Filter 2 — volume confirmation (10 candles preceding formation)
                volume_confirmed = get_volume_confirmed_filter(direction, k5m[-15:-5]).passes_filter
                if not volume_confirmed:
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

        A duplicate (same strategy+direction already PENDING/SKIPPED) is NOT
        inserted and its in-memory Signal gets db_id=None. It is also marked
        SKIPPED here so the scan loop never evaluates/executes a signal with no
        DB row — that produced orphaned positions with signal_id=None.
        """
        if self.session_factory is None or not to_persist:
            return
        try:
            async with self.session_factory() as session:
                existing = await session.execute(
                    select(SignalModel.strategy_type, SignalModel.direction).where(
                        SignalModel.symbol == symbol,
                        SignalModel.status.in_(("PENDING", "SKIPPED")),
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

    def get_pending(self) -> list[Signal]:
        return [s for s in self.pending if s.status == "PENDING"]


_signal_service: Optional[SignalService] = None


def get_signal_service() -> SignalService:
    """Bare service (no db/binance) — the wired instance comes from main.py."""
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service
