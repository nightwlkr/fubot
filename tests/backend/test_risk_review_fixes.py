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
    start = text.index('                    if decision.decision == "EXECUTE":')
    end = text.index('                        await ws_manager.broadcast', start)
    execute_block = text[start:end]

    assert "daily_guard_positions[signal.symbol]" in execute_block
    assert '"initialRiskDistance"' in execute_block
    assert '"unrealizedPnl"' in execute_block
