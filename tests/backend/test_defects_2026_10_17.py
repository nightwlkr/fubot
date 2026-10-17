"""Regression tests for three small defects fixed on 2026-10-17 (post-57f5187).

Each class maps to one defect and mixes BEHAVIOURAL tests (the observable
outcome) with SOURCE-CONTRACT tests (structural guards — these defects are
structural, so a behavioural test alone can be satisfied by a re-broken
implementation).

  1. `_signed_request` discarded Binance's error body and leaked the request
     signature into the journal.
  2. A FLATTENED trade never reached `daily_summary` (the -3R circuit breaker
     input) until the 48h exchange-sync recompute.
  3. `/bot/status` hardcoded `open_positions: []`.

No test here touches the network (httpx.MockTransport) and every test that
writes a row deletes it in a `finally` — see tests/AGENTS.md.
"""
import inspect
import logging

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
# 1. _signed_request destroys the error and leaks the signature
# ---------------------------------------------------------------------------

class TestBinanceErrorBodyIsSurfaced:
    """`_signed_request` called `resp.raise_for_status()`, so a 400 surfaced as
    `Client error '400 Bad Request' for url '...'` and Binance's own body —
    `{"code":-2021,"msg":"ReduceOnly Order is rejected..."}` — was never read.
    Diagnosing a live order rejection took a journal dump plus a code read.
    """

    async def test_400_with_a_binance_json_body_surfaces_code_and_msg(self):
        def handler(request):
            return httpx.Response(400, json={
                "code": -2021,
                "msg": "ReduceOnly Order is rejected. Please check your existing "
                       "position and open order.",
            })

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("POST", "fapi/v1/order")
        finally:
            await svc.close()

        assert exc.value.code == -2021, \
            "Binance's own error code must survive; the HTTP status alone is useless"
        assert "ReduceOnly Order is rejected" in str(exc.value), \
            "Binance's message must be in the exception the caller logs"

    async def test_404_with_an_html_body_still_raises_with_the_http_status(self):
        """Binance serves an HTML error page for some 404s — there is no JSON to
        read, so the error must still be raised (never swallowed into `{}`) and
        must fall back to the HTTP status."""
        def handler(request):
            return httpx.Response(404, text="<html><body>404 Not Found</body></html>",
                                  headers={"Content-Type": "text/html"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v1/nonexistent")
        finally:
            await svc.close()

        assert exc.value.code == 404
        assert "404" in str(exc.value)

    async def test_public_request_surfaces_the_binance_body_too(self):
        """The public path had the identical `raise_for_status()` blind spot."""
        def handler(request):
            return httpx.Response(418, json={"code": -1001, "msg": "Internal error."})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._public_request("fapi/v1/klines", {"symbol": "BTCUSDT"})
        finally:
            await svc.close()

        assert exc.value.code == -1001
        assert "Internal error." in str(exc.value)


class TestRequestSignatureNeverLeaks:
    """The signed URL embeds `signature=<hmac>`. httpx builds the
    `HTTPStatusError` message from the URL, so every 4xx wrote credential
    material into `journalctl` (observed live:
    `...&timestamp=1792240296815&signature=48e771c4153b5b92cfdfdee...`)."""

    async def test_no_raised_string_contains_a_signature(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(400, text="<html>nope</html>",
                                  headers={"Content-Type": "text/html"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(Exception) as exc:
                await svc._signed_request("GET", "fapi/v2/positionRisk")
            rendered = f"{exc.value!r} {exc.value}"
        finally:
            await svc.close()

        # The signature IS sent (that is what signs the request)...
        assert "signature=" in seen["url"]
        # ...but it must not be in anything an operator reads.
        assert "signature=" not in rendered, \
            f"the HMAC signature leaked into the exception: {rendered}"
        assert svc._sign("timestamp=1") not in rendered

    async def test_no_logged_string_contains_a_signature(self, caplog):
        """`logger.error(f"Request failed after 3 attempts: {e}")` writes the
        exception text to the journal — it must be redacted too."""

        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        svc = _signed_service(handler)
        with caplog.at_level(logging.DEBUG, logger="backend.services.binance_service"):
            try:
                with pytest.raises(Exception):
                    await svc._signed_request("GET", "fapi/v2/positionRisk")
            finally:
                await svc.close()

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert blob, "expected the retry loop to log something"
        assert "signature=" not in blob, \
            f"the HMAC signature leaked into the log: {blob}"
        assert svc._sign("timestamp=1") not in blob

    def test_redaction_is_a_named_helper_that_covers_the_credential_keys(self):
        """Structural guard: redaction must be a reusable helper, not an ad-hoc
        `str.replace` at one call site, and it must cover `apiKey` too."""
        import backend.services.binance_service as bs
        assert hasattr(bs, "redact_url"), "a reusable redaction helper must exist"
        for key in ("signature", "apiKey"):
            assert key in bs._SENSITIVE_QUERY_KEYS, f"redact_url must cover {key}"

        blob = ("https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDT"
                "&apiKey=KEY123&signature=deadbeefcafe&timestamp=1 "
                "X-MBX-APIKEY: KEY123")
        out = bs.redact_url(blob)
        assert "deadbeefcafe" not in out
        assert "KEY123" not in out
        assert "signature=" not in out and "apiKey=" not in out
        assert "symbol=BTCUSDT" in out, "non-secret context must survive redaction"

    def test_both_request_paths_build_their_error_from_the_parsed_body(self):
        """Structural guard against reintroducing `raise_for_status()`, which
        discards Binance's body AND renders the signed URL into the exception."""
        import backend.services.binance_service as bs
        for name in ("_signed_request", "_public_request"):
            body = inspect.getsource(getattr(bs.BinanceService, name))
            assert "raise_for_status()" not in body, (
                f"{name} must not call raise_for_status()")
            assert "_error_from_response(" in body, (
                f"{name} must build its error from the parsed body")
        src = inspect.getsource(bs.BinanceService._error_from_response)
        assert "redact_url(" in src, \
            "the fallback URL embedded in the error must be redacted at the source"

    def test_the_url_logged_is_the_redacted_one_not_the_signed_one(self):
        """Prefer constructing the error with a sanitised URL over post-hoc
        string surgery on the exception message."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs)
        assert "redact_url(" in src
        assert "str(resp.request.url)" not in src or "redact_url(str(resp.request.url))" in src, \
            "the request URL must be redacted at the point it is read"


class TestBinanceErrorSemanticsUnchanged:
    """The 200-with-code path and the 3x429 exhaustion path have dedicated
    behaviour pinned by `test_review_2026_10_17.py`. Re-asserted here because
    this fix touches the exact lines that implement them."""

    async def test_200_with_string_code_200_is_success(self):
        def handler(request):
            return httpx.Response(200, json={"code": "200", "msg": "success"})

        svc = _signed_service(handler)
        try:
            data = await svc._signed_request("DELETE", "fapi/v1/algoOrder",
                                            {"algoId": 1})
        finally:
            await svc.close()
        assert data["code"] == "200"

    async def test_200_with_a_negative_code_still_raises(self):
        def handler(request):
            return httpx.Response(200, json={"code": -2011, "msg": "Unknown order"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v1/algoOrder")
        finally:
            await svc.close()
        assert exc.value.code == -2011

    async def test_200_with_an_unparseable_code_is_treated_as_success(self):
        def handler(request):
            return httpx.Response(200, json={"code": None, "msg": "success"})

        svc = _signed_service(handler)
        try:
            data = await svc._signed_request("DELETE", "fapi/v1/algoOrder")
        finally:
            await svc.close()
        assert data["msg"] == "success"

    async def test_three_429s_still_raise_rate_limit_not_empty_dict(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v2/positionRisk")
        finally:
            await svc.close()

        assert calls["n"] == 3
        assert exc.value.code == -1003
