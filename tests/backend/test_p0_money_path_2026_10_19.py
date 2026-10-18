"""Regression tests for four P0 money-path defects found 2026-10-19.

Each class maps to one defect. Behavioural where the defect is observable
(fees, reduceOnly, double-submit); source-contract where a re-broken
implementation could still satisfy a naive behavioural test.

  P0-1  `get_position_income` filtered server-side on `incomeType`, so the
        COMMISSION / FUNDING_FEE branches were unreachable and every live
        `pnl_usdt`, `pnl_r` and the -3R breaker were gross of costs.
  P0-4  `place_market_order` sent no `reduceOnly`, so a close racing an
        exchange-side TP/SL fill opens a new opposite position.
  P0-6  `_signed_request` retried transport errors on every method, so a
        timed-out `POST /fapi/v1/order` was sent twice.
  P2-1  `auth_service` logged `FUBOT_SECRET_KEY[:4]` on every failed login.

No network access (httpx.MockTransport).
"""
import httpx
import pytest

from backend.services.binance_service import BinanceAPIError, BinanceService


def _signed_service(handler) -> BinanceService:
    svc = BinanceService(api_key="SUPERSECRETKEY", api_secret="s", testnet=True)
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=svc._base_url
    )
    return svc


# ---------------------------------------------------------------------------
# P0-1 — fees and funding were structurally always zero
# ---------------------------------------------------------------------------

class TestIncomeIncludesFeesAndFunding:
    """`get_position_income` passed `incomeType=REALIZED_PNL` to Binance, so the
    server returned ONLY realized-PnL rows. The function then summed
    `COMMISSION` and `FUNDING_FEE` branches that could never match, and
    `totalPnl` equalled gross PnL — while its docstring promised fees were
    included and the caller logged "fees included".
    """

    async def test_total_pnl_is_net_of_commission_and_funding(self):
        """The whole point: -5 realized, -0.30 commission, -0.10 funding = -5.40."""
        def handler(request):
            return httpx.Response(200, json=[
                {"incomeType": "REALIZED_PNL", "income": "-5.0"},
                {"incomeType": "COMMISSION", "income": "-0.3"},
                {"incomeType": "FUNDING_FEE", "income": "-0.1"},
            ])

        svc = _signed_service(handler)
        try:
            out = await svc.get_position_income("BTCUSDT")
        finally:
            await svc.close()

        assert out["realizedPnl"] == pytest.approx(-5.0)
        assert out["fees"] == pytest.approx(-0.3)
        assert out["funding"] == pytest.approx(-0.1)
        assert out["totalPnl"] == pytest.approx(-5.4), \
            "totalPnl must be NET of costs, not gross"

    async def test_request_does_not_ask_binance_to_filter_the_types(self):
        """A behavioural test above can be satisfied by summing a payload the
        server pre-filtered. Guard the actual query: incomeType must be absent.
        """
        seen = {}

        def handler(request):
            seen["query"] = str(request.url)
            return httpx.Response(200, json=[])

        svc = _signed_service(handler)
        try:
            await svc.get_position_income("BTCUSDT")
        finally:
            await svc.close()

        assert "incomeType" not in seen["query"], (
            "asking Binance to filter by incomeType makes the COMMISSION and "
            "FUNDING_FEE branches unreachable — filter client-side instead"
        )

    async def test_end_time_bounds_the_window(self):
        """A close must not absorb a LATER position's fees on the same symbol.
        Without an end bound, `[opened_at, now]` on a re-entered symbol charges
        the next trade's costs to this one."""
        seen = {}

        def handler(request):
            seen["query"] = str(request.url)
            return httpx.Response(200, json=[])

        svc = _signed_service(handler)
        try:
            await svc.get_position_income("BTCUSDT", start_time=1000, end_time=2000)
        finally:
            await svc.close()

        assert "endTime=2000" in seen["query"]
        assert "startTime=1000" in seen["query"]

    async def test_unrelated_income_types_are_ignored(self):
        """TRANSFER, WELCOME_BONUS etc. are not PnL and must not be summed."""
        def handler(request):
            return httpx.Response(200, json=[
                {"incomeType": "REALIZED_PNL", "income": "1.0"},
                {"incomeType": "TRANSFER", "income": "500.0"},
                {"incomeType": "WELCOME_BONUS", "income": "100.0"},
            ])

        svc = _signed_service(handler)
        try:
            out = await svc.get_position_income("BTCUSDT")
        finally:
            await svc.close()

        assert out["totalPnl"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# P0-4 — closing market orders must be reduceOnly
# ---------------------------------------------------------------------------

class TestMarketCloseIsReduceOnly:
    """If Binance's TP or SL fills between our DB read and our close order, an
    unreduced MARKET SELL on a now-flat symbol opens a fresh SHORT that nothing
    manages. `reduceOnly=true` makes the order a no-op instead.
    """

    async def test_a_closing_order_sends_reduce_only(self):
        seen = {}

        def handler(request):
            seen["query"] = str(request.url)
            return httpx.Response(200, json={
                "orderId": 1, "executedQty": "1", "avgPrice": "100",
                "cumQuote": "100", "status": "FILLED",
            })

        svc = _signed_service(handler)
        svc.get_lot_size = lambda symbol: _async_value(
            {"stepSize": "0.001", "minQty": "0.001"})
        try:
            await svc.place_market_order("BTCUSDT", "SELL", 1.0, reduce_only=True)
        finally:
            await svc.close()

        assert "reduceOnly=true" in seen["query"], \
            "a closing MARKET order must be reduceOnly"

    async def test_an_entry_order_does_not_send_reduce_only(self):
        """reduceOnly on an entry would be rejected — it only reduces."""
        seen = {}

        def handler(request):
            seen["query"] = str(request.url)
            return httpx.Response(200, json={
                "orderId": 1, "executedQty": "1", "avgPrice": "100",
                "cumQuote": "100", "status": "FILLED",
            })

        svc = _signed_service(handler)
        svc.get_lot_size = lambda symbol: _async_value(
            {"stepSize": "0.001", "minQty": "0.001"})
        try:
            await svc.place_market_order("BTCUSDT", "BUY", 1.0)
        finally:
            await svc.close()

        assert "reduceOnly" not in seen["query"]


# ---------------------------------------------------------------------------
# P0-6 — an order POST must never be blindly retried
# ---------------------------------------------------------------------------

class TestOrderPostsAreNotBlindlyRetried:
    """`_signed_request` caught bare `Exception` — which includes httpx transport
    errors — and retried. On `POST /fapi/v1/order` a timeout means the order may
    already have reached Binance; the retry submits a DUPLICATE, and the bot then
    tracks one position while holding two.
    """

    async def test_a_timed_out_order_post_is_not_resent(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            raise httpx.ReadTimeout("timed out")

        svc = _signed_service(handler)
        try:
            with pytest.raises(Exception):
                await svc._signed_request("POST", "fapi/v1/order", {"symbol": "BTCUSDT"})
        finally:
            await svc.close()

        assert len(calls) == 1, (
            f"the order POST was sent {len(calls)} times; a timeout leaves the "
            "exchange state UNKNOWN and must not be retried blindly"
        )

    async def test_a_get_is_still_retried(self):
        """Idempotent reads must keep their resilience — only POST changes."""
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if len(calls) < 2:
                raise httpx.ReadTimeout("timed out")
            return httpx.Response(200, json={"ok": True})

        svc = _signed_service(handler)
        try:
            out = await svc._signed_request("GET", "fapi/v2/balance")
        finally:
            await svc.close()

        assert len(calls) == 2
        assert out == {"ok": True}

    async def test_every_order_post_carries_a_unique_client_order_id(self):
        """Without newClientOrderId there is no way to ask Binance whether a
        timed-out order actually landed, and no way to reconcile a duplicate."""
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={
                "orderId": 1, "executedQty": "1", "avgPrice": "100",
                "cumQuote": "100", "status": "FILLED",
            })

        svc = _signed_service(handler)
        svc.get_lot_size = lambda symbol: _async_value(
            {"stepSize": "0.001", "minQty": "0.001"})
        try:
            await svc.place_market_order("BTCUSDT", "BUY", 1.0)
            await svc.place_market_order("BTCUSDT", "BUY", 1.0)
        finally:
            await svc.close()

        ids = [q.split("newClientOrderId=")[1].split("&")[0]
               for q in seen if "newClientOrderId=" in q]
        assert len(ids) == 2, "each order POST must carry a client order id"
        assert ids[0] != ids[1], "client order ids must be unique per order"

    @pytest.mark.parametrize("call,args", [
        ("place_limit_order", ("BTCUSDT", "BUY", 100.0, 1.0)),
        ("modify_position_stop", ("BTCUSDT", "LONG", 95.0, 1.0)),
        ("place_take_profit_order", ("BTCUSDT", "LONG", 115.0, 1.0)),
    ])
    async def test_limit_and_protective_orders_also_carry_client_ids(self, call, args):
        """A duplicated resting LIMIT entry, or a duplicated protective stop, is
        just as unmanaged as a duplicated market order — the id is what lets a
        reconcile find and cancel the orphan."""
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={
                "orderId": 1, "algoId": 1, "executedQty": "1",
                "avgPrice": "100", "cumQuote": "100", "status": "FILLED",
                "algoStatus": "NEW",
            })

        svc = _signed_service(handler)
        svc.get_lot_size = lambda symbol: _async_value(
            {"stepSize": "0.001", "minQty": "0.001"})
        try:
            await getattr(svc, call)(*args)
        finally:
            await svc.close()

        assert seen, f"{call} made no request"
        assert "newClientOrderId=" in seen[0], (
            f"{call} posts an order without a client order id, so a timed-out "
            "one cannot be reconciled or de-duplicated"
        )


class TestNoSecretFragmentIsLogged:
    """`auth_service` logged `FUBOT_SECRET_KEY[:4]` on every failed login — so
    the prefix was written to journalctl on every password guess. A masked
    secret is still a credential fragment and is enough to narrow an offline
    brute force of an HS256 signing key."""

    def test_no_module_logs_any_part_of_the_secret_key(self):
        import pathlib
        src = pathlib.Path("backend/services/auth_service.py").read_text()
        assert "FUBOT_SECRET_KEY[:4]" not in src, \
            "a fragment of the JWT signing key must never reach the log"
        assert "FUBOT_SECRET_KEY[" not in src

    async def test_a_failed_login_logs_the_username_but_not_the_key(self, caplog):
        import logging
        from backend.services import auth_service

        class _Result:
            def scalar_one_or_none(self):
                return None

        class _DB:
            async def execute(self, *a, **k):
                return _Result()

        auth_service._failed_attempts.clear()
        with caplog.at_level(logging.WARNING):
            await auth_service.authenticate(
                _DB(), "attacker", "guess", ip="1.2.3.4")
        auth_service._failed_attempts.clear()

        assert any("attacker" in r.getMessage() for r in caplog.records)
        assert not any("masked key" in r.getMessage() for r in caplog.records), \
            "the secret-key prefix must not appear in a login-failure log line"


class TestReduceOnlyRejectionIsNotAFailedClose:
    """`reduceOnly=true` introduces a rejection the old code could not produce:
    Binance answers -2021 when there is nothing left to reduce, which is exactly
    the race where the exchange's own TP/SL filled first. `_market_close` must
    confirm flatness and report SUCCESS — reporting failure re-arms an emergency
    stop against an already-flat account and escalates a clean exit into an
    alert."""

    async def _close_with(self, positions_after_reject, exc):
        from backend.core.position_manager import PositionManager
        svc = _signed_service(lambda r: (_ for _ in ()).throw(exc))
        svc.get_lot_size = lambda symbol: _async_value(
            {"stepSize": "0.001", "minQty": "0.001"})

        async def open_positions():
            return positions_after_reject
        svc.get_all_open_positions = open_positions

        pm = PositionManager(binance=svc, order_service=None, daily_reset=None,
                             alert_service=None, settings={})
        try:
            return await pm._market_close("BTCUSDT", "LONG", 1.0)
        finally:
            await svc.close()

    async def test_rejected_because_already_flat_is_reported_as_closed(self):
        result = await self._close_with(
            [], BinanceAPIError(-2021, "ReduceOnly Order is rejected."))
        assert result is True

    async def test_rejected_but_still_holding_is_reported_as_failure(self):
        """-2021 can also mean the quantity exceeded what is left to reduce.
        If exposure remains, the caller MUST re-arm protection."""
        result = await self._close_with(
            [{"symbol": "BTCUSDT", "quantity": 1.0}],
            BinanceAPIError(-2021, "ReduceOnly Order is rejected."))
        assert result is False

    async def test_a_500_is_still_a_failure(self):
        result = await self._close_with(
            [], BinanceAPIError(-1003, "Too many requests"))
        assert result is False


def _async_value(value):
    async def _coro():
        return value
    return _coro()