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

class TestEquityCurveRecordsAccountEquity:
    """`equity_log.balance_usdt` is what both the Performance Equity Curve and
    the Overview curve chart. It has been wrong twice, in different ways:

    1. It recorded `availableBalance` — FREE MARGIN — which dips on every entry
       and recovers on exit.
    2. Changed to the `balance` field of the USDT row of /fapi/v2/balance. Still
       wrong: that row is NOT the account. This account also holds BTC and BNB
       as cross-margin collateral, so the USDT row read ~$23.90 while real
       margin balance was ~$223.72 — the curve understated the account by ~90%.

    Summing `balance` across assets does not fix it either: those values are
    denominated in each asset (0.0022 BTC, 0.042 BNB), not USD, so they cannot
    be added. The only correct source is the account-level USD total
    `totalMarginBalance` from /fapi/v2/account — the figure Binance's own UI
    labels "Margin Balance".
    """

    def test_snapshot_uses_the_account_level_total(self):
        from backend.core.bot_engine import BotEngine
        src = inspect.getsource(BotEngine._maybe_snapshot_equity)
        assert "get_account_equity()" in src, \
            "equity must come from the account-level USD total"
        assert 'equity.get("margin_balance"' in src

    def test_snapshot_does_not_use_the_usdt_asset_row(self):
        """The per-asset USDT row excludes BTC/BNB cross-margin collateral."""
        from backend.core.bot_engine import BotEngine
        src = inspect.getsource(BotEngine._maybe_snapshot_equity)
        assert "get_account_balance()" not in src, \
            "the USDT row of /fapi/v2/balance is not the account"
        assert 'asset.get("asset") == "USDT"' not in src

    def test_snapshot_never_records_free_margin(self):
        """Parse the AST and check only real code, so the docstring explaining
        WHY availableBalance is wrong doesn't read as a use of it."""
        import ast
        import textwrap
        from backend.core.bot_engine import BotEngine
        tree = ast.parse(textwrap.dedent(inspect.getsource(BotEngine._maybe_snapshot_equity)))
        # Drop docstrings — they are not executable content.
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "availableBalance" not in code and "available_balance" not in code, \
            "free margin dips on every entry — it is not equity"

    def test_equity_service_reads_the_account_endpoint(self):
        from backend.services.binance_service import BinanceService
        src = inspect.getsource(BinanceService.get_account_equity)
        assert "fapi/v2/account" in src, \
            "the USD totals live on /fapi/v2/account, not /fapi/v2/balance"
        assert "totalMarginBalance" in src

    def test_equity_service_declares_the_right_weight(self):
        """/fapi/v2/account is weight 5; declaring 1 under-counts the limiter."""
        from backend.services.binance_service import BinanceService
        src = inspect.getsource(BinanceService.get_account_equity)
        assert "weight=5" in src

    def test_per_asset_balance_is_still_available_for_sizing(self):
        """Fixing equity must not break position sizing, which legitimately
        wants per-asset free margin."""
        from backend.services.binance_service import BinanceService
        src = inspect.getsource(BinanceService.get_account_balance)
        assert '"availableBalance"' in src
        assert '"balance"' in src

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
        # Derives from Trade rows, never daily_summary (the defect this class
        # was written for).
        assert 't.closed_at.strftime("%Y-%m")' in src
        # A NULL pnl_r means 1R was unestablishable, NOT breakeven. `or 0`
        # silently renders an unmeasurable trade as 0R; the fail-closed charge
        # of a full -1R is required instead.
        assert "float(t.pnl_r or 0)" not in src, \
            "monthly must not coalesce an unknown-R trade to breakeven"
        assert "daily_r_contribution" in src

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
        """The mode filter now lives in the shared `_apply_trade_filters` helper,
        so assert on the helper rather than on a literal in trade_stats."""
        import backend.routers.trades as tr
        helper = inspect.getsource(tr._apply_trade_filters)
        assert "Trade.mode ==" in helper
        assert "mode" in inspect.signature(tr._apply_trade_filters).parameters
        # And trade_stats must actually route through it with the mode.
        assert "_apply_trade_filters(" in inspect.getsource(tr.trade_stats)
        assert "mode=mode" in inspect.getsource(tr.trade_stats)

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

class TestTradeLogFiltersReachBothEndpoints:
    """The Trade Log's summary cards fetched `/trades/stats` with NO parameters,
    so they described all-time history while the table beside them was filtered.
    Filtering to one symbol left "45 trades / 40% win rate" above a 5-row table -
    which reads as "the filters don't work" even though every backend filter was
    individually correct (verified against production: symbol/side/outcome/
    from_date/to_date all returned distinct, correct counts).
    """

    def test_stats_accepts_the_same_filters_as_the_listing(self):
        import backend.routers.trades as tr
        list_params = set(inspect.signature(tr.list_trades).parameters)
        stats_params = set(inspect.signature(tr.trade_stats).parameters)
        for f in ("symbol", "side", "outcome", "from_date", "to_date"):
            assert f in list_params, f"/trades must accept {f}"
            assert f in stats_params, (
                f"/trades/stats must accept {f} or the cards ignore that filter")

    def test_both_endpoints_share_one_filter_implementation(self):
        """The six filters were written out twice inside list_trades (once for
        the page, once for the count) and a third time in trade_stats - three
        copies that can drift. They must all go through one helper."""
        import backend.routers.trades as tr
        helper = inspect.getsource(tr._apply_trade_filters)
        for token in ("symbol_filter(Trade.symbol", "Trade.side ==",
                      "Trade.pnl_r > 0", "Trade.pnl_r < 0",
                      "Trade.pnl_r == 0", "Trade.closed_at >=",
                      "Trade.closed_at <"):
            assert token in helper, f"the shared helper must handle {token}"
        list_src = inspect.getsource(tr.list_trades)
        assert list_src.count("_apply_trade_filters(") == 2, (
            "both the page query and the total-count query use the helper")
        assert "Trade.symbol ==" not in list_src, (
            "filters must not be re-implemented inline in list_trades")

    def test_frontend_sends_filters_to_the_stats_endpoint(self):
        from pathlib import Path
        tsx = (Path(__file__).resolve().parents[2]
               / "frontend/src/pages/TradeLog.tsx").read_text()
        idx = tsx.index("ENDPOINTS.tradeStats")
        # Params must be passed DIRECTLY. Wrapping them as { params } makes
        # axios serialise `params[side]=SHORT`, which the backend ignores —
        # that was the actual root cause of "the filters don't work".
        assert "ENDPOINTS.tradeStats, params)" in tsx[idx:idx + 80], (
            "the summary cards must request /trades/stats with the same filter "
            "params as /trades, passed directly (not wrapped in { params })")

    def test_to_date_includes_the_whole_end_day(self):
        """`datetime.fromisoformat('2026-10-17')` is midnight at the START of
        that day, so `closed_at < that` excluded every trade from the 17th."""
        import backend.routers.trades as tr
        helper = inspect.getsource(tr._apply_trade_filters)
        assert "timedelta(days=1)" in helper
        assert "len(to_date) == 10" in helper

    def test_unparseable_date_does_not_break_the_query(self):
        """A bad date string must not raise out of the endpoint and 500 the page."""
        import backend.routers.trades as tr
        helper = inspect.getsource(tr._apply_trade_filters)
        assert helper.count("except ValueError") == 2, (
            "both from_date and to_date must swallow an unparseable value")


class TestRangeButtonsAndStatCardsShareARange:
    """The 5 headline cards were all-time while every chart beside them honoured
    the 7d/30d/90d/365d buttons - so "Win Rate 38%" sat directly above a 7-day
    chart describing a completely different sample.
    """

    def test_stat_cards_also_respect_the_range(self):
        """The 5 headline cards were all-time while every chart beside them
        honoured the range — so "Win Rate 38%" sat directly above a 7-day
        chart describing a completely different sample."""
        import backend.routers.trades as tr
        src = inspect.getsource(tr.trade_stats)
        assert "days: Optional[int]" in src, \
            "/trades/stats must accept the range the cards display"
        assert "Trade.closed_at >= cutoff" in src

    def test_stat_cards_send_the_range_from_the_frontend(self):
        from pathlib import Path
        tsx = (Path(__file__).resolve().parents[2]
               / "frontend/src/pages/Performance.tsx").read_text()
        idx = tsx.index("ENDPOINTS.tradeStats")
        assert "days: RANGE_DAYS[range]" in tsx[idx:idx + 200], \
            "the stat-card request must pass the selected range"
        assert "ENDPOINTS.tradeStats, { days:" in tsx[idx:idx + 200], \
            "range must be passed directly, not wrapped in { params: ... }"
        assert "[range]" in tsx, \
            "the stats effect must re-run when the range changes"

    def test_overview_equity_curve_matches_its_7d_label(self):
        """It took the newest 30 ROWS of equity_log. Snapshots land every ~2.5
        min, so 30 rows is about 75 MINUTES under a card labelled '7D'."""
        import backend.routers.dashboard as rd
        src = inspect.getsource(rd.equity_and_totals) if hasattr(rd, "equity_and_totals") \
            else inspect.getsource(rd)
        assert "timedelta(days=7)" in src, \
            "the Overview curve must filter to the 7 days its title claims"
        assert ".limit(30)" not in src, \
            "a row limit ignores the time filter and silently shows ~75 minutes"

    def test_overview_equity_curve_is_ordered_ascending(self):
        """lightweight-charts throws 'Value is null' on out-of-order time, so the
        backend should not ship DESCENDING and rely on the frontend to re-sort."""
        import backend.routers.dashboard as rd
        src = inspect.getsource(rd)
        assert "EquityLog.recorded_at.desc()" not in src, \
            "the equity curve must be ordered ascending"

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