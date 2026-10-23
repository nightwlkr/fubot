"""Regressions for live-position mode recovery and same-scan loss checks."""

import asyncio
from types import SimpleNamespace


def test_position_manager_starts_in_the_persisted_bot_mode():
    from backend.core.position_manager import PositionManager

    manager = PositionManager(
        binance=object(), order_service=None, daily_reset=None,
        alert_service=None, settings={"bot_mode": "live"},
    )

    assert manager.mode == "live"


def test_position_manager_connects_live_stream_before_startup_reconciliation():
    from backend.core.position_manager import PositionManager

    events = []

    class Binance:
        async def get_all_positions(self):
            events.append("reconcile")
            return []

    class UserDataStream:
        async def connect(self, callback):
            events.append("stream")

    manager = PositionManager(
        binance=Binance(), order_service=None, daily_reset=None,
        alert_service=None, settings={"bot_mode": "live"},
        user_data_ws=UserDataStream(),
    )
    manager.session_factory = object()

    async def no_db_positions():
        return []

    manager._get_open_positions = no_db_positions

    async def run():
        await manager.start()
        await manager.stop()

    asyncio.run(run())

    assert events[:2] == ["stream", "reconcile"]


def test_position_snapshot_carries_recorded_mode_for_trade_accounting():
    """The close path must tag a Trade from its Position row, not a manager default."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "backend/core/position_manager.py"
    text = source.read_text()
    start = text.index("    async def _get_open_positions(")
    end = text.index("    def _cached_open_positions(", start)
    assert '"mode": r["mode"]' in text[start:end]

    close_start = text.index("    async def _close_position(")
    close_end = text.index("    async def _cancel_exchange_orders(", close_start)
    close_source = text[close_start:close_end]
    assert "position_mode = row.mode" in close_source
    assert "mode=position_mode" in close_source


def test_successful_fill_is_added_to_the_same_scan_daily_guard_snapshot():
    """A later candidate in the same scan must see the new position's R basis."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "backend/core/bot_engine.py"
    text = source.read_text()
    assert "self._record_scan_exposure(" in text
    helper_start = text.index("    def _record_scan_exposure(")
    helper_end = text.index("    async def _minutes_since_last_trade(", helper_start)
    helper = text[helper_start:helper_end]
    assert 'open_positions[signal.symbol]' in helper
    assert 'daily_guard_positions[signal.symbol]' in helper
    assert '"initialRiskDistance"' in helper
    assert '"unrealizedPnl"' in helper


def test_protected_survivor_after_failed_entry_updates_same_scan_risk_snapshot():
    from backend.core.bot_engine import BotEngine
    from backend.services.strategy_service import TradeDecision

    signal = type("Signal", (), {"symbol": "BTCUSDT", "direction": "LONG"})()
    survivor = {
        "symbol": "BTCUSDT", "side": "LONG", "quantity": 0.4,
        "entryPrice": 100.5, "markPrice": 100.5,
        "unrealizedPnl": -0.25, "initialRiskDistance": 10.5,
    }
    decision = TradeDecision(
        "SKIP", skip_reason="take profit placement failed",
        surviving_position=survivor,
    )
    occupancy = {}
    daily_guard = {}

    assert BotEngine._record_scan_exposure(
        signal, decision, occupancy, daily_guard, mode="live")
    assert occupancy["BTCUSDT"] == {
        "side": "LONG", "quantity": 0.4, "initialRiskDistance": 10.5,
    }
    assert daily_guard["BTCUSDT"] == {
        "side": "LONG", "quantity": 0.4,
        "initialRiskDistance": 10.5, "unrealizedPnl": -0.25,
    }


def test_unknown_surviving_exposure_aborts_the_remaining_scan_pass():
    from backend.core.bot_engine import BotEngine
    from backend.services.strategy_service import TradeDecision

    signal = type("Signal", (), {"symbol": "BTCUSDT", "direction": "LONG"})()
    decision = TradeDecision("SKIP", exposure_unknown=True)
    occupancy = {}
    daily_guard = {}

    assert not BotEngine._record_scan_exposure(
        signal, decision, occupancy, daily_guard, mode="live")
    assert occupancy == {}
    assert daily_guard == {}
