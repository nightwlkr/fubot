"""
Regression tests for the Performance page wiring (`analysis/REVIEW_2026-10-17.md`,
"Performance page is not wired correctly").

The Performance page reported absurd numbers — Best Trade +33,409R, Avg R/Trade
+662R, and a P&L endpoint totalling $393 million — and mixed data sources that
disagreed with each other. These tests pin the four wiring defects:

  1. the equity curve recorded `availableBalance` (FREE MARGIN), not total
     account value, so it dipped whenever a position opened;
  2. Monthly Returns read `daily_summary` while /pnl read `trades`, and nothing
     backfilled one from the other, so the two could not agree;
  3. no endpoint filtered by `Trade.mode`, so paper fills were summed into live
     statistics;
  4. `/performance/strategies` had no `days` param, so the R-distribution chart
     ignored the page's range buttons.
"""
import inspect

import pytest


# ---------------------------------------------------------------------------
# 1. The equity curve must record total account value
# ---------------------------------------------------------------------------

class TestEquityCurveRecordsTotalBalance:
    """`_maybe_snapshot_equity` wrote `availableBalance` into `equity_log.balance_usdt`.

    availableBalance is FREE MARGIN: it drops the moment a position opens and
    recovers when it closes, so the "equity curve" showed a cliff on every entry
    that was really just collateral being locked. Binance's `/fapi/v2/balance`
    returns the total on `balance`; `get_account_balance()` discarded it.
    """

    def test_snapshot_writes_total_balance_not_available_balance(self):
        from backend.core.bot_engine import BotEngine
        src = inspect.getsource(BotEngine._maybe_snapshot_equity)
        assert 'asset.get("balance"' in src, \
            "the equity snapshot must record the TOTAL USDT balance"
        assert 'asset.get("availableBalance"' not in src, \
            "availableBalance is free margin, not equity — it must not be charted"

    def test_balance_service_exposes_the_total(self):
        from backend.services.binance_service import BinanceService
        src = inspect.getsource(BinanceService.get_account_balance)
        assert '"balance"' in src, \
            "get_account_balance must surface the total `balance` field"
        # availableBalance must REMAIN available — position sizing needs free margin.
        assert '"availableBalance"' in src, \
            "availableBalance must stay: position sizing legitimately wants free margin"

    def test_equity_point_is_labelled_as_balance(self):
        from backend.routers.performance import equity_curve
        src = inspect.getsource(equity_curve)
        assert "balance=float(r.balance_usdt)" in src


# ---------------------------------------------------------------------------
# 2. Monthly Returns must derive from trades, not daily_summary
# ---------------------------------------------------------------------------

class TestMonthlyReturnsDeriveFromTrades:
    """Monthly Returns aggregated `daily_summary` while /pnl aggregated `trades`.

    `daily_summary` is only written by the daily-reset service and backfilled by
    the exchange-sync recompute (which covers just the last 48h). It is not a
    projection of `trades`, so the two endpoints could show different totals —
    and with the table empty, Monthly Returns rendered blank next to a full P&L
    chart. `trades` is the source of truth for per-trade P&L.
    """

    def test_monthly_does_not_read_daily_summary(self):
        from backend.routers.performance import monthly_returns
        src = inspect.getsource(monthly_returns)
        assert "DailySummary" not in src, \
            "monthly must aggregate `trades`; daily_summary is not backfilled from it"

    def test_monthly_sums_pnl_r_from_trade_rows(self):
        from backend.routers.performance import monthly_returns
        src = inspect.getsource(monthly_returns)
        assert "float(t.pnl_r or 0)" in src
        assert 't.closed_at.strftime("%Y-%m")' in src

    def test_monthly_counts_wins_for_the_win_rate(self):
        from backend.routers.performance import monthly_returns
        src = inspect.getsource(monthly_returns)
        assert '"wins"' in src and "win_rate" in src

    def test_monthly_guards_a_missing_closed_at(self):
        """A trade with NULL closed_at would blow up strftime and 500 the page."""
        from backend.routers.performance import monthly_returns
        src = inspect.getsource(monthly_returns)
        assert "if t.closed_at is None" in src


# ---------------------------------------------------------------------------
# 3. Mode filtering — paper must never contaminate live statistics
# ---------------------------------------------------------------------------

class TestPerformanceFiltersByMode:
    """`Trade.mode` was added with migration 009 precisely so paper and live
    could be separated, but `grep -c "Trade.mode"` was 0 in both routers: every
    statistic summed both. Paper fills are not real money and must not appear in
    a live win-rate or R-per-trade figure.
    """

    @pytest.mark.parametrize("endpoint", [
        "pnl_curve", "monthly_returns", "strategy_performance",
    ])
    def test_performance_endpoints_filter_by_mode(self, endpoint):
        import backend.routers.performance as perf
        src = inspect.getsource(getattr(perf, endpoint))
        assert "_mode_filter(" in src, f"{endpoint} must filter by mode"

    def test_equity_curve_is_exempt_because_equity_log_has_no_mode(self):
        """`equity_curve` reads `equity_log`, which records account balance and
        has no `mode` column (a balance snapshot is not attributable to a
        trading mode). It is genuinely exempt — and asserting the exemption
        documents why, so nobody 'fixes' it later by adding a broken filter."""
        import backend.routers.performance as perf
        from backend.models.equity_log import EquityLog
        src = inspect.getsource(perf.equity_curve)
        assert "EquityLog" in src
        assert "mode" not in EquityLog.__table__.columns, \
            "equity_log has no mode column — so the equity curve cannot be "\
            "mode-filtered, and any such filter would be a no-op"

    def test_trade_stats_filters_by_mode(self):
        import backend.routers.trades as tr
        src = inspect.getsource(tr.trade_stats)
        assert "Trade.mode" in src

    def test_default_is_live_only(self):
        """Live-only by default: showing paper by default would corrupt real
        money numbers without any indication on screen."""
        import backend.routers.performance as perf
        import backend.routers.trades as tr
        for mod, fn in ((perf, "pnl_curve"), (perf, "monthly_returns"),
                        (perf, "strategy_performance"), (tr, "trade_stats")):
            src = inspect.getsource(getattr(mod, fn))
            assert 'Query("live"' in src, f"{mod.__name__}.{fn} must default to live"

    def test_mode_filter_supports_paper_and_all(self):
        from backend.routers.performance import _mode_filter
        import inspect as _i
        src = _i.getsource(_mode_filter)
        assert 'mode == "all"' in src, "an explicit 'all' must be possible"
        assert "Trade.mode ==" in src

    def test_mode_filter_is_a_no_op_for_all(self):
        """`_mode_filter(query, 'all')` must not add a WHERE clause."""
        from backend.routers.performance import _mode_filter

        class FakeQuery:
            def __init__(self):
                self.where_called = False

            def where(self, *a, **k):
                self.where_called = True
                return self

        q = FakeQuery()
        _mode_filter(q, "all")
        assert q.where_called is False, "'all' must not filter"

        q2 = FakeQuery()
        _mode_filter(q2, "live")
        assert q2.where_called is True, "an explicit mode must filter"


# ---------------------------------------------------------------------------
# 4. Range buttons must reach every chart
# ---------------------------------------------------------------------------

class TestRangeReachesEveryChart:
    """`/performance/strategies` had no `days` param, so the R-distribution
    ignored the 7d/30d/90d/365d buttons and always showed all-time while the
    charts beside it respected the selection.
    """

    def test_strategies_endpoint_accepts_days(self):
        from backend.routers.performance import strategy_performance
        src = inspect.getsource(strategy_performance)
        assert "days: Optional[int]" in src, \
            "/performance/strategies must accept a days range"
        assert "Trade.closed_at >= cutoff" in src

    def test_strategies_days_defaults_to_all_time(self):
        """Omitting days keeps the historical all-time behaviour, so the
        histogram doesn't silently shrink for API consumers that don't pass it."""
        from backend.routers.performance import strategy_performance
        src = inspect.getsource(strategy_performance)
        assert "days: Optional[int] = Query(None" in src

    def test_frontend_sends_the_range_to_the_distribution(self):
        from pathlib import Path
        tsx = (Path(__file__).resolve().parents[2]
               / "frontend/src/pages/Performance.tsx").read_text()
        idx = tsx.index("performanceStrategies")
        window = tsx[idx:idx + 300]
        assert "days:" in window, \
            "the R-distribution request must pass the selected range"

    def test_frontend_shares_one_range_across_charts(self):
        """Each chart used to own its own range state, so the buttons on one
        chart never affected the others."""
        from pathlib import Path
        tsx = (Path(__file__).resolve().parents[2]
               / "frontend/src/pages/Performance.tsx").read_text()
        assert "const [range, setRange] = useState<Range>('30d')" in tsx, \
            "the page must own a single range and pass it down"
        assert tsx.count("useState<Range>") == 1, \
            "only one component may own range state"


# ---------------------------------------------------------------------------
# 5. Numeric coercion
# ---------------------------------------------------------------------------

class TestStatsNumericCoercion:
    """`pnl_r` is a Numeric column and comes back as Decimal. The old
    `sum(t.pnl_r for t in trades)` raised TypeError on a Decimal/float mix and
    on NULL.
    """

    def test_stats_coerces_to_float(self):
        from backend.routers.trades import trade_stats
        src = inspect.getsource(trade_stats)
        assert "float(t.pnl_r or 0)" in src, \
            "pnl_r must be coerced to float; Decimal/float mixes raise"