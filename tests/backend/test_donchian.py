"""DONCHIAN paper-only trend strategy — detector, scan gating, decision gate.

The strategy is PAPER-MODE ONLY by construction: detection requires
`bot_mode == "paper"` AND `strategy_donchian_enabled`, and
`StrategyService._evaluate_donchian` refuses live mode before any other
check. These tests pin the detector math (no lookahead, frozen stops), the
triple gate, the paper execution path, and the live-mode hard stop.
"""
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from backend.indicators.donchian import (
    DEFAULT_SYMBOLS,
    closed_bars,
    detect_donchian_breakout,
    donchian_trailing_stop,
)
from backend.services.order_service import OrderService
from backend.services.risk_service import RiskService
from backend.services.signal_service import Signal as ScanSignal, SignalService
from backend.services.strategy_service import StrategyService

DAY_MS = 86_400_000
# Fixed past base — every generated bar's close_time is already closed.
BASE_MS = 1_700_000_000_000


def _d(i, o, h, l, c):
    return {
        "open_time": BASE_MS + i * DAY_MS,
        "open": float(o), "high": float(h), "low": float(l),
        "close": float(c), "volume": 1.0,
        "close_time": BASE_MS + i * DAY_MS + DAY_MS - 1,
    }


def flat_days(n, price=100.0, high=101.0, low=99.0):
    return [_d(i, price, high, low, price) for i in range(n)]


def breakout_days(n=30, close=102.0):
    """`n` flat daily bars then a final close above the prior-20 max high."""
    candles = flat_days(n - 1)
    candles.append(_d(n - 1, 100.0, 103.0, 99.0, close))
    return candles


class TestDetectDonchianBreakout:
    def test_long_breakout_detected(self):
        hit = detect_donchian_breakout(breakout_days())
        assert hit is not None
        assert hit["direction"] == "LONG"
        assert hit["entry_price"] == 102.0
        assert hit["channel_top"] == 101.0
        assert hit["channel_bottom"] == 99.0
        # Initial stop = min low of the 20 bars strictly before the signal bar.
        assert hit["stop_loss"] == 99.0
        assert hit["trail_lookback"] == 10

    def test_no_breakout_inside_channel(self):
        candles = flat_days(30)
        candles[-1] = _d(29, 100.0, 101.5, 99.0, 100.5)  # close inside channel
        assert detect_donchian_breakout(candles) is None

    def test_short_mirror_detected(self):
        candles = flat_days(30)
        candles[-1] = _d(29, 100.0, 101.0, 97.0, 98.0)  # close < min low 99
        hit = detect_donchian_breakout(candles)
        assert hit is not None and hit["direction"] == "SHORT"
        # Short stop = max high of the prior window.
        assert hit["stop_loss"] == 101.0

    def test_signal_bar_excluded_from_its_own_channel(self):
        """Lookahead guard: the signal bar's own high must NOT form the
        channel — a 500 high on the signal bar cannot block its own breakout."""
        candles = breakout_days()
        candles[-1]["high"] = 500.0
        hit = detect_donchian_breakout(candles)
        assert hit is not None and hit["direction"] == "LONG"
        assert hit["channel_top"] == 101.0

    def test_prior_bar_spike_blocks_breakout(self):
        """Conversely a spike on a PRIOR bar legitimately raises the channel."""
        candles = breakout_days()
        candles[-2]["high"] = 150.0
        assert detect_donchian_breakout(candles) is None

    def test_insufficient_history_returns_none(self):
        assert detect_donchian_breakout(flat_days(20)) is None
        # 21 bars = lookback+1 is the minimum for a channel.
        candles = flat_days(21)
        candles[-1] = _d(20, 100.0, 103.0, 99.0, 102.0)
        assert detect_donchian_breakout(candles) is not None

    def test_lookback_parameter_respected(self):
        # With lookback=5 the channel uses only the prior 5 bars.
        candles = flat_days(30)
        # Spikes at indices 10..23: inside the prior-20 window (9..28) but
        # outside the prior-5 window (24..28) of the signal bar at index 29.
        for i in range(10, 24):
            candles[i]["high"] = 200.0
        candles[-1] = _d(29, 100.0, 103.0, 99.0, 102.0)
        assert detect_donchian_breakout(candles, lookback=5) is not None
        assert detect_donchian_breakout(candles, lookback=20) is None


class TestDonchianTrailingStop:
    def test_long_trail_uses_prior_10_bar_low(self):
        candles = flat_days(30)
        # Bars 19..28 (the 10 strictly before the last) dip to 98.
        for i in range(19, 29):
            candles[i]["low"] = 98.0
        candles[-1]["low"] = 90.0  # last bar itself is excluded
        assert donchian_trailing_stop(candles, "LONG") == 98.0

    def test_short_trail_mirrors_with_high(self):
        candles = flat_days(30)
        for i in range(19, 29):
            candles[i]["high"] = 105.0
        assert donchian_trailing_stop(candles, "SHORT") == 105.0

    def test_trail_none_when_history_short(self):
        assert donchian_trailing_stop(flat_days(10), "LONG") is None

    def test_trail_lookback_parameter(self):
        candles = flat_days(30)
        for i in range(25, 29):
            candles[i]["low"] = 97.0
        assert donchian_trailing_stop(candles, "LONG", trail_lookback=5) == 97.0


class TestClosedBars:
    def test_forming_bar_dropped(self):
        candles = flat_days(30)
        forming = _d(30, 100.0, 101.0, 99.0, 100.0)
        # close_time in the future relative to real now — still forming.
        forming["close_time"] = int(time.time() * 1000) + DAY_MS
        out = closed_bars(candles + [forming])
        assert len(out) == 30
        assert out[-1]["open_time"] == candles[-1]["open_time"]

    def test_explicit_now_ms(self):
        candles = flat_days(30)
        out = closed_bars(candles, now_ms=BASE_MS + 30 * DAY_MS)
        assert len(out) == 30


# ---------------------------------------------------------------- services

class _FakeBinance:
    """Minimal BinanceService stand-in for scan/evaluate paths."""

    def __init__(self, price=100.0, daily=None):
        self.price = price
        self.daily = daily if daily is not None else breakout_days()
        self.calls = []

    async def get_ticker(self, symbol):
        return {"symbol": symbol, "price": self.price}

    async def get_mark_price(self, symbol):
        return {"symbol": symbol, "markPrice": self.price}

    async def get_account_balance(self):
        return [{"asset": "USDT", "availableBalance": "10000"}]

    async def get_funding_rate(self, symbol):
        return 0.0

    async def get_klines(self, symbol, interval="1h", limit=100):
        self.calls.append((symbol, interval, limit))
        if interval == "1d":
            return list(self.daily)
        # Flat intraday candles — enough bars for every detector/context calc.
        n = 200
        return [{
            "open_time": BASE_MS - (n - i) * 3_600_000,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 10.0,
            "close_time": BASE_MS - (n - i) * 3_600_000 + 3_599_999,
        } for i in range(n)]


def _scan_settings(**over):
    settings = {
        "bot_mode": "paper",
        "strategy_donchian_enabled": True,
        "strategy_fvg_enabled": False,
        "strategy_3candle_enabled": False,
        "strategy_choch_enabled": False,
        "strategy_obfvg_enabled": False,
        "strategy_orb_enabled": False,
        "funding_rate_filter_enabled": False,
        "rr_min": 3.0,
        "signal_max_age_minutes": 60,
    }
    settings.update(over)
    return settings


def _donchian_signal(**over):
    args = dict(
        symbol="BTCUSDT", strategy_type="DONCHIAN", direction="LONG",
        entry_zone_min=Decimal("100"), entry_zone_max=Decimal("100"),
        stop_loss=Decimal("95"), take_profit=None, rr_ratio=Decimal("0"),
        volume_confirmed=False,
        created_at=datetime.now(timezone.utc),
        status="PENDING", db_id=None,
    )
    args.update(over)
    return ScanSignal(**args)


class TestScanSymbolDonchianGate:
    """scan_symbol emits DONCHIAN candidates only in paper mode with the
    toggle on and the symbol inside the universe."""

    def _service(self, **over):
        return SignalService(
            session_factory=None, binance=_FakeBinance(),
            settings=_scan_settings(**over))

    async def test_paper_mode_emits_donchian_signal(self):
        signals = await self._service().scan_symbol("BTCUSDT")
        donchian = [s for s in signals if s.strategy_type == "DONCHIAN"]
        assert len(donchian) == 1
        s = donchian[0]
        assert s.status == "PENDING" and s.direction == "LONG"
        assert s.take_profit is None and float(s.rr_ratio) == 0.0
        # entry = last closed daily close (102); stop = min low prior 20 (99)
        assert float(s.entry_zone_min) == 102.0
        assert float(s.stop_loss) == 99.0

    async def test_live_mode_never_detects(self):
        signals = await self._service(bot_mode="live").scan_symbol("BTCUSDT")
        assert not [s for s in signals if s.strategy_type == "DONCHIAN"]

    async def test_toggle_off_disables(self):
        signals = await self._service(strategy_donchian_enabled=False).scan_symbol("BTCUSDT")
        assert signals == []

    async def test_symbol_outside_universe_skipped(self):
        signals = await self._service(donchian_symbols="ETHUSDT").scan_symbol("BTCUSDT")
        assert not [s for s in signals if s.strategy_type == "DONCHIAN"]
        # ...but the same symbol IS picked up when listed.
        signals = await self._service(donchian_symbols="BTCUSDT").scan_symbol("BTCUSDT")
        assert any(s.strategy_type == "DONCHIAN" for s in signals)

    async def test_default_universe_is_backtest_symbols(self):
        svc = self._service()
        assert svc._donchian_universe() == set(DEFAULT_SYMBOLS)
        assert "BTCUSDT" in svc._donchian_universe()

    async def test_min_risk_floor_widens_stop(self):
        """A stop tighter than min_risk_pct of entry is widened to the floor."""
        candles = breakout_days()
        for c in candles[:-1]:
            c["low"] = 101.9  # prior-20 min low ≈ entry - 0.1% << 0.5% floor
        candles[-1]["low"] = 101.9
        svc = SignalService(
            session_factory=None,
            binance=_FakeBinance(daily=candles, price=102.0),
            settings=_scan_settings(min_risk_pct=0.005))
        signals = await svc.scan_symbol("BTCUSDT")
        s = next(s for s in signals if s.strategy_type == "DONCHIAN")
        # floor: 102 * (1 - 0.005) = 101.49 — wider (lower) than the 101.9 channel.
        assert float(s.stop_loss) == pytest.approx(101.49, abs=1e-6)


class TestEvaluateDonchian:
    def _strategy(self, settings=None):
        merged = {
            "strategy_donchian_enabled": True,
            "max_positions": 3, "risk_percent": 1.0,
            "paper_balance_usdt": 10000.0, "paper_use_real_balance": False,
            "max_position_notional_pct": 50.0,
            "signal_max_age_minutes": 60, "min_risk_pct": 0.005,
        }
        merged.update(settings or {})
        return StrategyService(
            RiskService(), OrderService(binance=None, session_factory=None),
            binance=_FakeBinance(price=100.0), session_factory=None,
            settings=merged)

    async def test_live_mode_never_executes_donchian(self):
        """THE hard gate: a PENDING DONCHIAN signal in live mode is refused
        before any sizing, order or exchange call — even with the toggle on
        and a full snapshot supplied."""
        order_svc = OrderService(binance=None, session_factory=None)
        svc = StrategyService(
            RiskService(), order_svc, binance=_FakeBinance(price=100.0),
            session_factory=None,
            settings={"strategy_donchian_enabled": True})
        decision = await svc.evaluate_signal(
            _donchian_signal(), paper_mode=False, open_positions={})
        assert decision.decision == "SKIP"
        assert "paper" in decision.skip_reason.lower()
        assert order_svc.pending == {}  # nothing was ever placed

    async def test_paper_mode_executes_simulated_fill(self):
        svc = self._strategy()
        decision = await svc.evaluate_signal(
            _donchian_signal(), paper_mode=True, open_positions={})
        assert decision.decision == "EXECUTE" and decision.paper is True
        plan = decision.plan
        assert plan["entry"] == 100.0
        assert plan["stop_loss"] == 95.0
        assert plan["take_profit"] is None
        assert plan["initial_risk_distance"] == pytest.approx(5.0)
        # risk$ = 1% of 10000 = 100; qty = 100 / 5 = 20
        assert plan["size"] == pytest.approx(20.0)

    async def test_paper_toggle_off_skips(self):
        svc = self._strategy(settings={"strategy_donchian_enabled": False})
        decision = await svc.evaluate_signal(
            _donchian_signal(), paper_mode=True, open_positions={})
        assert decision.decision == "SKIP"
        assert "disabled" in decision.skip_reason.lower()

    async def test_paper_existing_position_blocks(self):
        svc = self._strategy()
        decision = await svc.evaluate_signal(
            _donchian_signal(), paper_mode=True,
            open_positions={"BTCUSDT": {"quantity": 1.0}})
        assert decision.decision == "SKIP"
        assert "already open" in decision.skip_reason.lower()


class TestDonchianSignalDedup:
    """Real-DB dedup: a second identical PENDING DONCHIAN setup is suppressed
    by the shared _persist_signals path (same as every other strategy)."""

    SYMBOL = "DONCHTST"

    async def test_duplicate_pending_not_reinserted(self):
        # Dedicated engine, disposed after — the shared AsyncSessionLocal pool
        # is loop-bound and leaking a pooled connection breaks later tests
        # (same convention as test_signal_service._scan_and_fetch).
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.models.signal import Signal as SignalModel

        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        svc = SignalService(session_factory=Session,
                            binance=None, settings={})
        sig = _donchian_signal(symbol=self.SYMBOL)
        try:
            async with Session() as session:
                session.add(SignalModel(
                    symbol=self.SYMBOL, strategy_type="DONCHIAN",
                    direction="LONG",
                    entry_zone_min=Decimal("100"), entry_zone_max=Decimal("100"),
                    stop_loss=Decimal("95"), take_profit=None,
                    rr_ratio=Decimal("0"), volume_confirmed=False,
                    status="PENDING",
                ))
                await session.commit()

            await svc._persist_signals(self.SYMBOL, [(sig, False)])

            # The in-memory signal was deduped, not persisted.
            assert sig.status == "SKIPPED"
            assert "Duplicate" in sig.skip_reason
            async with Session() as session:
                count = (await session.execute(
                    select(func.count()).select_from(SignalModel).where(
                        SignalModel.symbol == self.SYMBOL,
                        SignalModel.status == "PENDING",
                    ))).scalar_one()
            assert count == 1
        finally:
            async with Session() as session:
                await session.execute(
                    delete(SignalModel).where(SignalModel.symbol == self.SYMBOL))
                await session.commit()
            await engine.dispose()
