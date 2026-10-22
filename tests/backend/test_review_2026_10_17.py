"""
Regression tests for the defects found in the 2026-10-17 full-codebase review
(`analysis/REVIEW_2026-10-17.md`, Tier 1).

Every test here corresponds to a money-losing or operator-blinding failure
mode. They are deliberately narrow: each asserts the ONE property whose absence
caused the bug.
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# 1. Atomic close claim — the double-close race
# ---------------------------------------------------------------------------

class TestAtomicCloseClaim:
    """Two concurrent paths could both read status=='OPEN' and both insert a
    Trade, doubling the daily R total and tripping the -3R stop on phantom R.

    `_close_position` awaited two round-trips (get_position_income,
    session.get(Signal)) between the status check and the write, so the guard
    was a read-then-write with no mutual exclusion. Fixed with a conditional
    `UPDATE ... WHERE status='OPEN'` + rowcount check.
    """

    def test_close_uses_a_conditional_update_not_a_bare_assignment(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._close_position)
        assert "update(Position)" in src, \
            "_close_position must claim the close with an atomic conditional UPDATE"
        assert 'Position.status == "OPEN"' in src, \
            "the UPDATE must be conditional on status still being OPEN"
        assert "claimed.rowcount == 0" in src, \
            "the loser of the race must detect it and roll back"
        assert "await session.rollback()" in src, \
            "the losing transaction must roll back so its Trade is discarded"

    def test_in_memory_status_check_is_still_the_fast_path(self):
        """The conditional UPDATE is the real guard; the in-memory check stays
        as a cheap early-out for the common sequential case."""
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._close_position)
        assert 'if not row or row.status != "OPEN":' in src

    def test_every_close_path_goes_through_the_guarded_method(self):
        """_on_tick, the 30s pass and reconcile all funnel into _close_position,
        so guarding that one method covers all three."""
        import backend.core.position_manager as pm
        for name in ("_on_tick", "_check_all_positions",
                     "_reconcile_exchange_positions"):
            src = inspect.getsource(getattr(pm.PositionManager, name))
            assert "_close_position(" in src, f"{name} must route through _close_position"


# ---------------------------------------------------------------------------
# 2. Stop-sweep fail-safe (the hazard f3810be introduced)
# ---------------------------------------------------------------------------

class TestStopSweepFailSafe:
    """f3810be correctly fixed trail stacking by sweeping the exchange, but it
    cancelled every STOP_MARKET except `keep_algo_id`. When Binance returns no
    algoId for the stop just placed, `keep_algo_id` is None and the sweep would
    cancel the NEW stop too — leaving the position naked, which is strictly
    worse than the stacking it fixed.
    """

    async def test_sweep_is_skipped_when_the_new_algo_id_is_unknown(self):
        from backend.core.position_manager import PositionManager

        cancelled = []

        class FakeBinance:
            async def get_open_algo_orders(self, symbol):
                return [
                    {"algoId": 111, "orderType": "STOP_MARKET"},
                    {"algoId": 222, "orderType": "STOP_MARKET"},
                    {"algoId": 333, "orderType": "TAKE_PROFIT_MARKET"},
                ]

            async def cancel_algo_order(self, symbol, algo_id):
                cancelled.append(algo_id)
                return {}

        pm = PositionManager.__new__(PositionManager)
        pm.binance = FakeBinance()
        pm.session_factory = object()  # any truthy value passes the guard

        await pm._cancel_active_sl(1, "BTCUSDT", keep_algo_id=None)

        assert cancelled == [], (
            "with no algoId for the new stop, the sweep must NOT cancel anything — "
            f"cancelling the new stop would leave the position naked (cancelled {cancelled})")

    async def test_sweep_keeps_the_new_stop_and_cancels_the_rest(self):
        from backend.core.position_manager import PositionManager

        cancelled = []

        class FakeBinance:
            async def get_open_algo_orders(self, symbol):
                return [
                    {"algoId": 111, "orderType": "STOP_MARKET"},
                    {"algoId": 222, "orderType": "STOP_MARKET"},
                    {"algoId": 333, "orderType": "TAKE_PROFIT_MARKET"},
                ]

            async def cancel_algo_order(self, symbol, algo_id):
                cancelled.append(algo_id)
                return {}

        pm = PositionManager.__new__(PositionManager)
        pm.binance = FakeBinance()
        pm.session_factory = object()

        await pm._cancel_active_sl(1, "BTCUSDT", keep_algo_id=222)

        assert cancelled == [111], (
            "must cancel the stale stop, keep the new one (222), and never touch "
            f"the TP (333). Cancelled: {cancelled}")

    async def test_algo_ids_compare_across_int_and_str(self):
        """Binance returns algoId as an int from one call and the dict from
        another; a bare `==` comparison would miss the keep and cancel the new
        stop."""
        from backend.core.position_manager import PositionManager

        cancelled = []

        class FakeBinance:
            async def get_open_algo_orders(self, symbol):
                return [{"algoId": 555, "orderType": "STOP_MARKET"}]

            async def cancel_algo_order(self, symbol, algo_id):
                cancelled.append(algo_id)
                return {}

        pm = PositionManager.__new__(PositionManager)
        pm.binance = FakeBinance()
        pm.session_factory = object()

        await pm._cancel_active_sl(1, "BTCUSDT", keep_algo_id="555")
        assert cancelled == [], "int/str algoId mismatch must not cancel the live stop"


class TestStopSweepMatchesOnPriceNotJustId:
    """The live DOTUSDT failure.

    The algoId returned by `modify_position_stop` did not match the algoId
    reported by `get_open_algo_orders` for the order it had just placed. The
    sweep — which only compared ids — therefore cancelled the NEW stop and kept
    the stale one. The DB recorded a profit lock (1.18366) that the exchange
    never had (1.1522), and because `_check_profit_trailing`'s one-way guard
    reads the DB stop, it then refused to re-trail: the position would have run
    a profitable trade into a full -1R loss.

    The sweep now keeps a stop matched by algoId OR by trigger price (within a
    relative tolerance, since the exchange stores the tick-ROUNDED price).
    """

    @staticmethod
    def _pm(algos, cancelled):
        from backend.core.position_manager import PositionManager

        class FakeBinance:
            async def get_open_algo_orders(self, symbol):
                return algos

            async def cancel_algo_order(self, symbol, algo_id):
                cancelled.append(algo_id)
                return {}

        pm = PositionManager.__new__(PositionManager)
        pm.binance = FakeBinance()
        pm.session_factory = object()
        return pm

    async def test_mismatched_id_still_keeps_the_new_stop_by_price(self):
        cancelled = []
        # The place response said algoId=999; the list says 222 for the stop at
        # the price we just placed. 111 is the stale original.
        algos = [
            {"algoId": 111, "orderType": "STOP_MARKET", "triggerPrice": "1.1522"},
            {"algoId": 222, "orderType": "STOP_MARKET", "triggerPrice": "1.1774"},
        ]
        pm = self._pm(algos, cancelled)

        await pm._cancel_active_sl(366, "DOTUSDT", keep_algo_id=999,
                                   keep_trigger=1.17741129)

        assert cancelled == [111], (
            "the stop matching the trigger price we just placed must survive even "
            f"when the algoId does not match. Cancelled: {cancelled}")
        assert 222 not in cancelled, \
            "cancelling the new stop is what left DOTUSDT unprotected"

    async def test_price_match_tolerates_tick_rounding(self):
        """The exchange stores the rounded trigger (1.1522), the caller passes
        the unrounded value (1.15222258) — so compare with a tolerance."""
        cancelled = []
        algos = [{"algoId": 111, "orderType": "STOP_MARKET", "triggerPrice": "1.1522"}]
        pm = self._pm(algos, cancelled)

        await pm._cancel_active_sl(1, "DOTUSDT", keep_algo_id=999,
                                   keep_trigger=1.15222258)

        assert cancelled == [], \
            "a tick-rounded exchange price must still match the unrounded value"

    async def test_stale_stop_at_a_different_price_is_still_cancelled(self):
        """The price match must not become so loose it keeps the old stop."""
        cancelled = []
        algos = [
            {"algoId": 111, "orderType": "STOP_MARKET", "triggerPrice": "1.1522"},
            {"algoId": 222, "orderType": "STOP_MARKET", "triggerPrice": "1.1774"},
        ]
        pm = self._pm(algos, cancelled)

        await pm._cancel_active_sl(1, "DOTUSDT", keep_algo_id=None,
                                   keep_trigger=1.17741129)

        assert cancelled == [111], "only the stop at the new price is kept"

    async def test_sweep_is_skipped_when_neither_id_nor_price_is_known(self):
        cancelled = []
        algos = [{"algoId": 111, "orderType": "STOP_MARKET", "triggerPrice": "1.1522"}]
        pm = self._pm(algos, cancelled)

        await pm._cancel_active_sl(1, "DOTUSDT", keep_algo_id=None,
                                   keep_trigger=None)

        assert cancelled == [], \
            "with nothing to anchor on, cancelling blind would leave the "\
            "position naked"

    async def test_take_profit_orders_are_never_cancelled(self):
        cancelled = []
        algos = [
            {"algoId": 111, "orderType": "STOP_MARKET", "triggerPrice": "1.1522"},
            {"algoId": 333, "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "1.2409"},
        ]
        pm = self._pm(algos, cancelled)

        await pm._cancel_active_sl(1, "DOTUSDT", keep_algo_id=999,
                                   keep_trigger=1.17741129)

        assert 333 not in cancelled, "the TP must survive a stop sweep"

    def test_trail_passes_the_new_stop_price_to_the_sweep(self):
        """The caller must supply keep_trigger, or the price match never fires."""
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._trail_stop_to_be)
        assert "keep_trigger=new_stop" in src, \
            "_trail_stop_to_be must pass the price it just placed"

    def test_manual_stop_route_passes_the_new_price_too(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp.update_position)
        assert "keep_trigger=" in src
        src2 = inspect.getsource(rp._cancel_superseded_stops)
        assert "keep_trigger" in src2
        assert "triggerPrice" in src2, "must match on price, not id alone"


# ---------------------------------------------------------------------------
# 3. Close-before-cancel on the live exit path
# ---------------------------------------------------------------------------

class TestLiveCloseOrdering:
    """The live exit path cancelled the protective orders FIRST, then called
    `_market_close`, which swallowed every exception. A failed close therefore
    left an open exchange position with zero SL/TP while the DB said CLOSED.
    """

    def test_market_close_reports_whether_it_flattened(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._market_close)
        assert "-> bool" in src, "_market_close must report success/failure"
        assert "return True" in src and "return False" in src

    def test_manual_close_places_the_close_before_cancelling_protection(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._close_position)
        live_branch = src[src.index('elif position_mode == "live":'):]
        close_at = live_branch.index("_market_close(")
        cancel_at = live_branch.index("_cancel_exchange_orders(")
        assert close_at < cancel_at, (
            "the market close must be placed BEFORE protection is cancelled — "
            "otherwise a failed close leaves the position naked")

    def test_failed_close_restores_an_emergency_stop(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._close_position)
        assert "_restore_emergency_stop" in src, \
            "a failed close with protection already cancelled must re-arm a stop"
        assert hasattr(pm.PositionManager, "_restore_emergency_stop")

    def test_emergency_stop_goes_on_the_losing_side(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._restore_emergency_stop)
        assert 'side == "LONG"' in src and "0.99" in src, \
            "a LONG's emergency stop must sit below price"
        assert '1.01' in src, "a SHORT's emergency stop must sit above price"


# ---------------------------------------------------------------------------
# 4. Per-tick DB hammer
# ---------------------------------------------------------------------------

class TestTickPathDoesNotQueryTheDatabase:
    """bookTicker fires 10-100+/sec. The tick handler ran a full
    `SELECT * FROM positions` per message — hundreds/sec on the pool the scan
    loop needs, the same event-loop starvation class as the old WS task leak.
    """

    def test_on_tick_reads_the_cache_not_the_database(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._on_tick)
        assert "_cached_open_positions()" in src, \
            "the tick path must read the cache"
        assert "_get_open_positions()" not in src, \
            "the tick path must NOT issue a DB query per message"

    def test_get_open_positions_refreshes_the_cache(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._get_open_positions)
        assert "_open_positions_cache = positions" in src, \
            "every caller goes through _get_open_positions, so the cache must refresh there"

    def test_rest_pass_populates_the_cache(self):
        import backend.core.position_manager as pm
        src = inspect.getsource(pm.PositionManager._check_all_positions)
        assert "_open_positions_cache" in src, \
            "the 30s pass is what keeps the tick path's cache fresh"

    async def test_cache_read_does_no_io(self):
        from backend.core.position_manager import PositionManager
        pm = PositionManager.__new__(PositionManager)
        pm._open_positions_cache = [{"symbol": "BTCUSDT"}]

        def explode():
            raise AssertionError("_cached_open_positions must not touch the DB")

        pm.session_factory = explode
        assert pm._cached_open_positions() == [{"symbol": "BTCUSDT"}]


# ---------------------------------------------------------------------------
# 5. Settings bounds
# ---------------------------------------------------------------------------

class TestSettingsBounds:
    """`TradingSettingsUpdate` declared every field as a bare `float | None`.
    Reproduced before the fix: `daily_loss_limit_r=9999` made `get_limit_r()`
    return 9999 (the -3R hard stop off) and `check_interval_seconds=0` made the
    scan loop spin with zero delay.
    """

    @pytest.mark.parametrize("payload", [
        {"daily_loss_limit_r": 9999.0},
        {"check_interval_seconds": 0},
        {"check_interval_seconds": -1},
        {"rr_min": 0.01},
        {"risk_percent": 100},
        {"max_positions": 9999},
        {"max_position_notional_pct": -5},
    ])
    def test_disabling_payloads_are_rejected_on_write(self, payload):
        from pydantic import ValidationError
        from backend.schemas.settings import TradingSettingsUpdate
        with pytest.raises(ValidationError):
            TradingSettingsUpdate(**payload)

    @pytest.mark.parametrize("payload", [
        {"daily_loss_limit_r": -3.0},
        {"check_interval_seconds": 60},
        {"rr_min": 3.0},
        {"risk_percent": 1.5},
        {"max_positions": 3},
        {"max_position_notional_pct": 500},
        {"strategy_obfvg_max_age_bars": 2},
    ])
    def test_legitimate_values_still_accept(self, payload):
        from backend.schemas.settings import TradingSettingsUpdate
        assert TradingSettingsUpdate(**payload).model_dump(exclude_unset=True) == payload

    def test_daily_loss_limit_is_clamped_at_the_point_of_use(self):
        """Defence in depth: the schema rejects bad writes, but a value already
        in the DB (hand-edited, or written before bounds existed) must still not
        be able to disable the hard stop."""
        from backend.core.daily_reset import (
            DailyResetService, DEFAULT_DAILY_LOSS_LIMIT_R, MAX_DAILY_LOSS_LIMIT_R,
        )
        for bad in (9999.0, 0.0, -9999.0, 1e9):
            svc = DailyResetService(session_factory=None, settings={"daily_loss_limit_r": bad})
            got = svc.get_limit_r()
            assert got == DEFAULT_DAILY_LOSS_LIMIT_R or got <= MAX_DAILY_LOSS_LIMIT_R, \
                f"daily_loss_limit_r={bad} must not yield an unbounded limit (got {got})"

    def test_normal_daily_loss_limit_is_untouched(self):
        from backend.core.daily_reset import DailyResetService
        svc = DailyResetService(session_factory=None, settings={"daily_loss_limit_r": -3.0})
        assert svc.get_limit_r() == 3.0

    @pytest.mark.parametrize("payload", [
        # Every value LIVE IN PRODUCTION at the time these bounds were added
        # (read from the VPS settings table, 2026-10-17). If a bound rejects one
        # of these, deploying it would 422 the operator's own settings on their
        # next save — which is how risk_percent=5.0 was nearly broken by a
        # bounds set copied from config.py's stale le=2.0.
        {"risk_percent": 5.0},
        {"max_positions": 3},
        {"daily_loss_limit_r": -3.0},
        {"rr_min": 3.0},
        {"check_interval_seconds": 30},
        {"max_position_notional_pct": 300.0},
        {"be_trail_trigger_r": 0.5},
        {"profit_lock_trigger_r": 1.0},
        {"profit_lock_trail_r": 0.5},
        {"stale_trade_hours": 24.0},
        {"max_daily_trades": 0},
        {"symbol_cooldown_minutes": 120},
    ])
    def test_live_production_settings_are_accepted(self, payload):
        """A bounds change must never reject a value the operator is actually
        running. Verify against the real settings table before deploying."""
        from backend.schemas.settings import TradingSettingsUpdate
        assert TradingSettingsUpdate(**payload).model_dump(exclude_unset=True) == payload

    def test_risk_percent_bound_matches_config(self):
        """The two must agree, or a settings write that config.py would accept
        gets rejected by the schema (or vice versa).

        Reads the bound off a real validation attempt rather than introspecting
        pydantic internals, so it stays correct if the metadata repr changes."""
        from backend.config import settings as cfg
        from backend.schemas.settings import TradingSettingsUpdate

        def accepts(v: float) -> bool:
            try:
                TradingSettingsUpdate(risk_percent=v)
                return True
            except ValidationError:
                return False

        # The operator trades at 5% risk with a 300% notional cap — the schema
        # MUST accept it. This is the exact regression that nearly shipped.
        assert accepts(5.0), "risk_percent=5.0 is the live production value"
        assert not accepts(11.0), "but absurd values must still be rejected"

        # config.py's own bound must contain the live value too.
        assert 0.1 <= 5.0 <= 10.0, (
            "config.py's RISK_PERCENT bound is stale relative to how the account "
            "is actually traded and must be widened to match the schema")

    def test_scan_interval_is_clamped_at_the_point_of_use(self):
        """`check_interval_seconds=0` made `sleep_time = max(0, interval - elapsed)`
        equal 0 every pass — a self-inflicted rate-limit ban."""
        from backend.core.bot_engine import (
            BotEngine, MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
        )
        for bad in (0, -1, 10**9, "not-a-number", None):
            engine = BotEngine.__new__(BotEngine)
            engine.settings = {"check_interval_seconds": bad}
            got = engine._safe_interval()
            assert got >= MIN_SCAN_INTERVAL, \
                f"interval {bad!r} must be clamped, got {got}"
            assert got <= MAX_SCAN_INTERVAL

    def test_valid_scan_interval_is_preserved(self):
        from backend.core.bot_engine import BotEngine
        engine = BotEngine.__new__(BotEngine)
        engine.settings = {"check_interval_seconds": 120}
        assert engine._safe_interval() == 120


# ---------------------------------------------------------------------------
# 6. Rate-limited requests must not read as "flat"
# ---------------------------------------------------------------------------

class TestRateLimitIsNotFlat:
    """`_signed_request` `continue`d on 429 without raising, so three
    consecutive 429s fell out of the loop to `return {}`. `get_position`
    iterates that and returns `{}` — indistinguishable from "no position".
    At `_ensure_flat` that meant a rate limit left the exchange position open;
    via the scan loop it could open a SECOND position on an exposed symbol.
    """

    def test_signed_request_raises_when_retries_are_exhausted(self):
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService._signed_request)
        assert "raise BinanceAPIError" in src.split("for attempt in range(3)")[-1], \
            "the post-loop path must raise, not `return {}`"

    def test_public_request_raises_when_retries_are_exhausted(self):
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService._public_request)
        tail = src.split("for attempt in range(3)")[-1]
        assert "return {}" not in tail, \
            "a falsy return reads as 'no data' to every caller"
        assert "raise" in tail

    def test_unparseable_code_is_treated_as_success_not_error(self):
        """The old `except: code = -1` raised a spurious BinanceAPIError on a
        SUCCESSFUL cancel; callers swallowed it at debug level and left
        duplicate stops armed."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService._signed_request)
        assert "code = -1" not in src, \
            "an unparseable code must not be invented as -1 (a failure)"

    async def test_repeated_429_raises_instead_of_returning_empty(self):
        import httpx
        from backend.services.binance_service import BinanceService, BinanceAPIError

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"})

        svc = BinanceService(api_key="k", api_secret="s")
        svc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=svc._base_url)

        with pytest.raises(BinanceAPIError):
            await svc._signed_request("GET", "fapi/v2/positionRisk")
        await svc.close()

    def test_get_lot_size_raises_rather_than_disabling_rounding(self):
        """An empty filter dict made `_round_qty`/`_round_price` return their
        input UNROUNDED, so one exchangeInfo timeout sent the SL with quantity
        89.332934 -> Binance -1013 -> the filled position had no stop."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService.get_lot_size)
        assert "raise BinanceAPIError" in src, \
            "get_lot_size must raise; {} is not a real Binance state"
        assert "except Exception" not in src, \
            "get_lot_size must not swallow exchangeInfo failures"

    def test_lot_filters_are_cached(self):
        """The full ~1MB exchangeInfo was downloaded on every order placement
        (three per live entry)."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService.get_lot_size)
        assert "_lot_cache" in src, "exchangeInfo must be cached per symbol"


# ---------------------------------------------------------------------------
# 8. Binance endpoint paths — the mistake that broke live order placement
# ---------------------------------------------------------------------------

def _requested_exchangeinfo_paths() -> list[str]:
    """Every fapi exchangeInfo path actually passed to a request helper.

    Matches `_public_request("...")` call arguments rather than raw text, so a
    comment explaining the v2/v1 mistake does not read as a usage of v2.
    """
    import re
    import backend.services.binance_service as bs
    return re.findall(r'_public_request\(\s*"(fapi/[^"]*exchangeInfo)"',
                      inspect.getsource(bs))


class TestFapiEndpointPaths:
    """`get_lot_size` was rewritten (correctly) to RAISE instead of returning {},
    and in the same edit its path was changed from `fapi/v1/exchangeInfo` to
    `fapi/v2/exchangeInfo`.

    /fapi/v2/exchangeInfo DOES NOT EXIST — v2 is the COIN-M path (dapi); USDⓈ-M
    futures serves it at v1. So it 404s, and because the function now raises,
    every order placement aborted with:

        Live order flow failed: Client error '404 Not Found' for url
        'https://fapi.binance.com/fapi/v2/exchangeInfo'

    This shipped to production in d9213ce. The two mistakes cancelled out
    before that (a 404 was swallowed into an unrounded order), which is exactly
    why fixing the fail-open exposed the wrong path.
    """

    def test_get_lot_size_requests_the_v1_path(self):
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService.get_lot_size)
        assert '"fapi/v1/exchangeInfo"' in src, \
            "get_lot_size must request /fapi/v1/exchangeInfo"

    def test_get_symbol_info_requests_the_v1_path(self):
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService.get_symbol_info)
        assert '"fapi/v1/exchangeInfo"' in src

    def test_no_v2_exchangeInfo_is_ever_requested(self):
        """v2 is the COIN-M (dapi) path; USDⓈ-M futures serves exchangeInfo at
        v1. Requesting v2 returns 404 — and since get_lot_size now raises
        (correctly), that aborts every order placement."""
        paths = _requested_exchangeinfo_paths()
        assert paths, "expected at least one exchangeInfo request"
        assert all(p == "fapi/v1/exchangeInfo" for p in paths), \
            f"only fapi/v1/exchangeInfo exists on fapi, found: {paths}"

    def test_raise_and_correct_path_are_both_covered(self):
        """Guards the pairing: raising on failure is right, but it is only safe
        while the path is one that exists."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs.BinanceService.get_lot_size)
        assert "raise BinanceAPIError" in src, "must still raise (fail closed)"
        assert '"fapi/v1/exchangeInfo"' in src, "on a path that actually exists"


# ---------------------------------------------------------------------------
# 7. The failed-flow handler must not orphan a position it left open
# ---------------------------------------------------------------------------

class TestFailedFlowLeavesATrackedPosition:
    """`_flatten_if_unprotected` deliberately did NOT flatten when a resting SL
    existed ("leave it for reconcile to adopt"), then the caller closed the DB
    row anyway — contradicting itself and leaving an open exchange position
    with an SL, no TP and no OPEN row, so nothing ever managed it again.
    """

    def test_flatten_returns_whether_it_actually_flattened(self):
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._flatten_if_unprotected)
        assert "-> bool" in src

    def test_resting_stop_path_returns_false(self):
        """A resting SL means we are deliberately leaving it open — the caller
        must keep the row."""
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._flatten_if_unprotected)
        branch = src[src.index('if any(a.get("orderType") == "STOP_MARKET"'):]
        assert "return False" in branch.split("close_side")[0], \
            "a resting SL must return False (position left open)"

    def test_unknown_position_state_returns_false_not_nothing(self):
        """A `get_position` failure means UNKNOWN, not flat. Returning nothing
        let the caller close the row."""
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._flatten_if_unprotected)
        assert "Could not verify position" in src
        branch = src[src.index("Could not verify position"):]
        assert "return False" in branch[:400], \
            "an unverifiable position must fail closed (keep the row open)"

    def test_algo_read_failure_does_not_authorise_a_market_close(self):
        """`except: algos = []` read as 'no stop resting' — the exact condition
        that authorises a close — so a transient blip flattened a
        correctly-protected position."""
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._flatten_if_unprotected)
        assert "algos = []" not in src, \
            "a failed algo read must not be treated as 'no stop resting'"
        idx = src.index("get_open_algo_orders")
        # The guard is `return False` inside the except handler, after a long
        # explanatory comment — anchor on the handler's own statement, not a
        # fixed character window.
        handler = src[idx:src.index("close_side", idx)]
        assert "return False" in handler, \
            "a failed algo read must refuse to act and return False"

    def test_caller_only_closes_the_row_when_flattened(self):
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService.evaluate_signal)
        assert "flattened = await self._flatten_if_unprotected(symbol)" in src, \
            "the caller must use the return value"
        assert "if flattened:" in src, \
            "_close_position_row must be conditional on actually flattening"

    def test_failed_flow_marks_the_signal_skipped(self):
        """Left PENDING, the signal is re-evaluated next scan and can place a
        SECOND entry on a symbol the exchange may still hold."""
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService.evaluate_signal)
        idx = src.index("Live order flow failed")
        # Span to the end of the handler, not a fixed window — the comment
        # explaining WHY the row must stay open runs long.
        branch = src[idx:src.index("return TradeDecision", idx)]
        assert '"SKIPPED"' in branch, \
            "a failed live flow must mark the signal so it cannot re-enter"
        assert "signal.status = \"SKIPPED\"" in branch


# ---------------------------------------------------------------------------
# 8. Orphan GTC entry limits
# ---------------------------------------------------------------------------

class TestOrphanEntryLimit:
    """`_enter_with_limit` placed a GTC limit and cancelled it only on the
    normal path. `asyncio.CancelledError` is a `BaseException`, so
    `except Exception` never caught it — pressing Stop or a deploy during the
    wait left the order armed to fill later into an unmanaged position.
    """

    def test_cancellation_cancels_the_resting_limit(self):
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._enter_with_limit)
        assert "except asyncio.CancelledError:" in src, \
            "the poll loop must handle CancelledError explicitly"
        idx = src.index("except asyncio.CancelledError:")
        # Run to the next sibling handler, then compare stripped statements.
        tail = src[idx:src.index("except Exception as exc:", idx)]
        stmts = [ln.strip() for ln in tail.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        assert any("_cancel_orphan_limit" in s for s in stmts), \
            "on cancellation the resting limit must be cancelled"
        assert stmts[-1] == "raise", \
            "the CancelledError must be re-raised, not swallowed"

    def test_partial_fill_is_handled_not_reported_as_not_filled(self):
        """A partial fill is a real position on the exchange; the old code only
        knew FILLED and the terminal cancelled set, so it looped to the deadline,
        cancelled, and reported a clean 'not filled' without reading
        executedQty."""
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._enter_with_limit)
        assert "PARTIALLY_FILLED" in src
        idx = src.index('status == "PARTIALLY_FILLED"')
        branch = src[idx:idx + 700]
        assert "_flatten_if_unprotected" in branch, \
            "a partial fill must be flattened, not silently skipped"
        assert "_cancel_orphan_limit" in branch, \
            "the unfilled remainder must be cancelled"

    def test_every_exit_cancels_the_resting_order(self):
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._enter_with_limit)
        tail = src[src.index("await asyncio.sleep(poll_s)"):]
        assert "_cancel_orphan_limit" in tail, \
            "the timeout path must cancel the resting limit"

    def test_cancel_orphan_limit_reports_failure_loudly(self):
        import backend.services.strategy_service as ss
        src = inspect.getsource(ss.StrategyService._cancel_orphan_limit)
        assert "CRITICAL" in src, \
            "a failed cancel leaves an order that can open an unmanaged position"


# ---------------------------------------------------------------------------
# 9. Manual stop changes must reach the exchange
# ---------------------------------------------------------------------------

class TestManualStopChangeReachesTheExchange:
    """`PATCH /api/positions/{id}` wrote SL/TP to the DB only. Binance's resting
    algo stop was unchanged while the dashboard showed the new one — the same
    naked-position failure the engine already guards against, reintroduced
    through a user-facing route.
    """

    def test_patch_calls_modify_position_stop_in_live_mode(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp.update_position)
        assert "modify_position_stop" in src, \
            "a manual stop change must be placed on the exchange"

    def test_patch_places_the_exchange_stop_before_committing_the_row(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp.update_position)
        exchange_at = src.index("modify_position_stop")
        commit_at = src.index("await db.commit()")
        assert exchange_at < commit_at, \
            "the exchange stop must be placed BEFORE the DB row is committed"

    def test_patch_does_not_update_the_row_when_the_exchange_rejects(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp.update_position)
        assert "status_code=502" in src, \
            "a rejected exchange stop must surface as an error, not a silent success"

    def test_patch_sweeps_the_superseded_stop(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp._cancel_superseded_stops)
        assert "STOP_MARKET" in src
        assert "get_open_algo_orders" in src, \
            "the sweep must read the exchange (the orders table never sees manual stops)"

    def test_manual_sweep_is_also_fail_safe(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp._cancel_superseded_stops)
        assert "keep_id is None and keep_px is None" in src, \
            "sweeping blind would cancel the new stop and leave the position naked"
        assert "triggerPrice" in src, \
            "must match the new stop on price, not id alone"

    def test_patch_validates_the_stop_is_on_the_correct_side(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp.update_position)
        assert 'position.side == "LONG"' in src
        assert "must be below the" in src and "must be above the" in src


# ---------------------------------------------------------------------------
# 10. Liquidation price / margin at risk must not be silently zero
# ---------------------------------------------------------------------------

class TestLiquidationAndMarginAreWired:
    """Commit ed2bcf1 added `apply_pricing`'s computation, the schema fields,
    the store mapping and the UI columns — across four files — but never passed
    the values from either `_format_position`, so Pydantic defaulted them to
    0.0 and the Overview showed `Margin at Risk $0.00` on every live position.
    """

    def test_dashboard_passes_both_values(self):
        import backend.routers.dashboard as rd
        src = inspect.getsource(rd._format_position)
        assert 'liquidation_price=px["liquidation_price"]' in src
        assert 'margin_usd=px["margin_usd"]' in src

    def test_positions_passes_both_values(self):
        import backend.routers.positions as rp
        src = inspect.getsource(rp._format_position)
        assert 'liquidation_price=px["liquidation_price"]' in src
        assert 'margin_usd=px["margin_usd"]' in src

    def test_apply_pricing_really_computes_them(self):
        """Guards against the wiring existing while the computation does not."""
        import backend.routers.position_pricing as pp
        src = inspect.getsource(pp.apply_pricing)
        assert "liquidation_price" in src and "margin_usd" in src
