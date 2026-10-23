"""OBFVG backtest coverage must follow the live detector contract."""

import asyncio
import importlib.util
from pathlib import Path

import pytest


def _series():
    start = 1_700_000_000_000

    def candles(step, count):
        rows = []
        for i in range(count):
            close = 100.0 + i * 0.01
            rows.append({
                "open_time": start + i * step,
                "close_time": start + (i + 1) * step - 1,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1000.0,
            })
        return rows

    return candles(3_600_000, 80), candles(300_000, 960), candles(900_000, 320)


class TestBacktestObFvg:
    def test_live_toggle_and_freshness_setting_gate_detector(self, monkeypatch):
        import backend.backtest as btmod
        from backend.backtest import Backtester

        k1h, k5m, k15m = _series()
        seen = []

        def detector(k5m_arg, k1h_arg, atr, rr, *, max_age_bars):
            seen.append((len(k5m_arg), len(k1h_arg), max_age_bars))
            return [("BULLISH", 99.0, 101.0, 95.0, 119.0, 3.0)]

        monkeypatch.setattr(btmod, "detect_obfvg_candidates", detector, raising=False)
        enabled = Backtester(settings={
            "strategy_fvg_enabled": False,
            "strategy_3candle_enabled": False,
            "strategy_choch_enabled": False,
            "strategy_obfvg_enabled": True,
            "strategy_obfvg_max_age_bars": 4,
        })
        candidates, _, _ = enabled._detect_candidates_at(
            k1h, k5m, k15m, k5m[840]["close_time"] + 1, 70, 840, 280)

        assert seen == [(92, 7, 4)]
        assert len(candidates) == 1
        strategy, direction, entry, source, interval, zone, signal_idx = candidates[0]
        assert (strategy, direction, interval, zone, signal_idx) == (
            "OBFVG", "LONG", "1h", (99.0, 101.0), 70)
        assert entry["entry_price"] == 100.0
        assert len(source) == 71 and source[-1] == k1h[70]

        disabled = Backtester(settings={
            "strategy_fvg_enabled": False,
            "strategy_3candle_enabled": False,
            "strategy_choch_enabled": False,
            "strategy_obfvg_enabled": False,
        })
        candidates, _, _ = disabled._detect_candidates_at(
            k1h, k5m, k15m, k5m[840]["close_time"] + 1, 70, 840, 280)
        assert candidates == []
        assert len(seen) == 1, "disabled OBFVG must not call its detector"

    def test_obfvg_detector_receives_only_closed_candle_prefixes(self, monkeypatch):
        import backend.backtest as btmod
        from backend.backtest import Backtester

        k1h, k5m, k15m = _series()
        seen = []

        def detector(k5m_arg, k1h_arg, atr, rr, *, max_age_bars):
            seen.append((k5m_arg[-1]["close_time"], k1h_arg[-1]["close_time"]))
            return []

        monkeypatch.setattr(btmod, "detect_obfvg_candidates", detector, raising=False)
        bt = Backtester(settings={
            "strategy_fvg_enabled": False,
            "strategy_3candle_enabled": False,
            "strategy_choch_enabled": False,
            "strategy_obfvg_enabled": True,
        })
        scan_time = k5m[840]["close_time"] + 1
        bt._detect_candidates(k1h, k5m, k15m, scan_time)

        # The current 5m bar closes exactly at the 70h boundary, so hourly
        # candle 69 is the last fully closed hourly candle at that instant.
        assert seen == [(k5m[840]["close_time"], k1h[69]["close_time"])]
        assert k5m[841]["close_time"] > scan_time
        assert k1h[71]["close_time"] > scan_time

    def test_long_replay_bounds_obfvg_detector_to_relevant_history(self, monkeypatch):
        import backend.backtest as btmod
        from backend.backtest import Backtester

        k1h, k5m, k15m = _series()
        seen = []

        def detector(k5m_arg, k1h_arg, atr, rr, *, max_age_bars):
            seen.append((len(k5m_arg), len(k1h_arg), k5m_arg[-1], k1h_arg[-1]))
            return []

        monkeypatch.setattr(btmod, "detect_obfvg_candidates", detector, raising=False)
        bt = Backtester(settings={
            "strategy_fvg_enabled": False,
            "strategy_3candle_enabled": False,
            "strategy_choch_enabled": False,
            "strategy_obfvg_enabled": True,
            "strategy_obfvg_max_age_bars": 2,
        })

        bt._detect_candidates_at(
            k1h, k5m, k15m, k5m[840]["close_time"] + 1, 70, 840, 280)

        # The live detector's 50-bar OB horizon plus its 30-bar start offset
        # and 11-bar maximum confirmation lag need 92 candles. Only the most
        # recent three hourly formation bars are eligible at max_age=2.
        assert seen[0][0] == 92
        assert seen[0][1] == 5
        assert seen[0][2] == k5m[840]
        assert seen[0][3] == k1h[70]

    def test_cli_exposes_obfvg_toggle_and_age(self, monkeypatch):
        """CLI args must become the live-named settings consumed by Backtester."""
        import backend.backtest as btmod

        captured = []

        async def fake_run(symbol, *, days, settings, end_days_ago):
            captured.append(settings)

            class Result:
                def summary(self):
                    return {"total_r": 0.0, "net_r": 0.0, "closed": 0,
                            "symbol": symbol, "signals": 0, "win_rate": 0.0,
                            "expectancy_r": 0.0, "expectancy_net_r": 0.0,
                            "trades_per_day": 0.0, "skipped": {}}

            return Result()

        monkeypatch.setattr(btmod, "run_backtest", fake_run)
        script = Path(__file__).resolve().parents[2] / "scripts" / "backtest.py"
        spec = importlib.util.spec_from_file_location("backtest_cli_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(
            "sys.argv", [str(script), "--obfvg", "--obfvg-max-age-bars", "5"])

        asyncio.run(module.main())

        assert captured[0]["strategy_obfvg_enabled"] is True
        assert captured[0]["strategy_obfvg_max_age_bars"] == 5
