"""Signal service — orchestration of all indicators, scan symbol for trading signals."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.signal import Signal
from backend.models.setting import Setting
from backend.indicators.sma import calculate_sma
from backend.indicators.rsi import calculate_rsi, get_rsi_zone
from backend.indicators.trend import analyze_trend_bias, should_trade_with_bias
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.choch import detect_choch
from backend.indicators.volume_profile import analyze_zone_volume
from backend.indicators.atr import calculate_atr
from backend.services.binance_service import BinanceService

logger = logging.getLogger(__name__)


class SignalService:
    """Orchestrates all indicator modules to detect and filter trading signals."""

    def __init__(self, db: AsyncSession, binance: BinanceService, settings: dict):
        self.db = db
        self.binance = binance
        self.settings = settings
        self._cached_trend: dict[str, dict] = {}

    async def scan_symbol(self, symbol: str) -> list[Signal]:
        """
        Full scan pipeline for one symbol:
        1. Fetch all needed candle data
        2. Calculate trend bias (1H)
        3. Detect FVG, 3-Candle, CHoCH on 5m/15m
        4. Filter by trend bias alignment
        5. Filter by volume confirmation
        6. Filter by R:R >= minimum
        7. Create Signal records in DB for passing signals
        """
        try:
            # Fetch candles in parallel
            candles_1h = await self.binance.get_klines(symbol, "1h", 200)
            candles_5m = await self.binance.get_klines(symbol, "5m", 200)
            candles_15m = await self.binance.get_klines(symbol, "15m", 200)

            if not candles_1h or not candles_5m:
                return []

            # Extract close prices
            closes_1h = [c["close"] for c in candles_1h]
            closes_5m = [c["close"] for c in candles_5m]
            closes_15m = [c["close"] for c in candles_15m]

            # Calculate ATR (14-period on 1H)
            atr = calculate_atr(candles_1h, 14) or 1.0

            # Step 1: Trend bias
            trend = analyze_trend_bias(closes_1h)

            # Step 2: Pattern detection
            candidates = []

            # FVG on 5m
            fvgs = detect_fvg(candles_5m, symbol)
            for fvg in fvgs[:3]:  # Top 3 FVGs
                entry = evaluate_fvg_entry(fvg, atr, self.settings.get("rr_min", 3.0))
                if entry["is_valid"]:
                    candidates.append({
                        "type": "FVG",
                        "direction": fvg.direction,
                        "entry": entry["entry_price"],
                        "stop": entry["stop_loss"],
                        "tp": entry["take_profit"],
                        "rr": entry["rr_ratio"],
                        "zone_min": fvg.zone_min,
                        "zone_max": fvg.zone_max,
                        "candles": candles_5m[-15:-5],
                        "signal": fvg,
                    })

            # 3-Candle on 5m
            tc_patterns = detect_three_candles(candles_5m, symbol)
            for tc in tc_patterns[:3]:
                entry = evaluate_three_candle_entry(tc, atr, self.settings.get("rr_min", 3.0))
                if entry["is_valid"]:
                    candidates.append({
                        "type": "3CANDLE",
                        "direction": tc.direction,
                        "entry": entry["entry_price"],
                        "stop": entry["stop_loss"],
                        "tp": entry["take_profit"],
                        "rr": entry["rr_ratio"],
                        "zone_min": tc.entry_zone_min,
                        "zone_max": tc.entry_zone_max,
                        "candles": candles_5m[-15:-5],
                        "signal": tc,
                    })

            # CHoCH on 15m
            choch_patterns = detect_choch(candles_15m, symbol, "15m", atr, self.settings.get("rr_min", 3.0))
            for ch in choch_patterns[:2]:
                if ch.is_valid:
                    candidates.append({
                        "type": "CHOC",
                        "direction": ch.direction,
                        "entry": (ch.pullback_entry_zone[0] + ch.pullback_entry_zone[1]) / 2,
                        "stop": ch.stop_loss,
                        "tp": ch.take_profit,
                        "rr": ch.rr_ratio or 0.0,
                        "zone_min": ch.pullback_entry_zone[0],
                        "zone_max": ch.pullback_entry_zone[1],
                        "candles": candles_15m[-15:-5],
                        "signal": ch,
                    })

            # Step 3-4: Apply filters
            valid_signals = []
            for c in candidates:
                passes = True
                skip_reason = ""

                # Filter by trend
                trend_ok, trend_reason = should_trade_with_bias(trend, c["direction"])
                if not trend_ok:
                    passes = False
                    skip_reason = f"Trend filter: {trend_reason}"

                # Filter by volume
                if passes and c.get("candles"):
                    vol = analyze_zone_volume(c["candles"])
                    vol_ok = (
                        (c["direction"] == "LONG" and vol.dominant_side == "BUY" and vol.is_confirmed) or
                        (c["direction"] == "SHORT" and vol.dominant_side == "SELL" and vol.is_confirmed)
                    )
                    if not vol_ok:
                        passes = False
                        skip_reason = f"Volume filter: {vol.dominant_side} dominance insufficient"

                # Filter by R:R
                if passes and c["rr"] < self.settings.get("rr_min", 3.0):
                    passes = False
                    skip_reason = f"R:R {c['rr']:.2f} below minimum {self.settings.get('rr_min', 3.0)}"

                if passes:
                    # Create Signal record
                    signal = Signal(
                        symbol=symbol,
                        strategy_type=c["type"],
                        direction=c["direction"],
                        entry_zone_min=c["zone_min"],
                        entry_zone_max=c["zone_max"],
                        stop_loss=c["stop"],
                        take_profit=c["tp"],
                        rr_ratio=c["rr"],
                        volume_confirmed=True,
                        status="PENDING",
                        created_at=datetime.now(timezone.utc),
                    )
                    self.db.add(signal)
                    valid_signals.append(signal)

            await self.db.commit()

            # Alert on new signals
            if valid_signals and self.settings.get("alerts_enabled", True):
                for s in valid_signals:
                    logger.info(f"Signal: {s.direction} {s.symbol} ({s.strategy_type}), R:R: {s.rr_ratio:.1f}")

            return valid_signals

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}", exc_info=True)
            return []

    async def get_active_signals(self, symbol: Optional[str] = None, limit: int = 100) -> list[Signal]:
        """Get currently pending signals."""
        query = select(Signal).where(Signal.status == "PENDING")

        if symbol:
            query = query.where(Signal.symbol == symbol)

        query = query.order_by(Signal.created_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_missed_signals(self, days: int = 7, symbol: Optional[str] = None) -> list[Signal]:
        """Get skipped signals from last N days."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        query = select(Signal).where(
            Signal.status == "SKIPPED",
            Signal.created_at >= cutoff,
        )

        if symbol:
            query = query.where(Signal.symbol == symbol)

        query = query.order_by(Signal.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())
