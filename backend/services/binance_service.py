"""Binance Futures service with async REST + WebSocket support."""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

# Binance Futures API endpoints
PROD_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"
PROD_WS = "wss://fstream.binance.com"
TESTNET_WS = "wss://testnet.binancefuture.com"


@dataclass
class BinanceAPIError(Exception):
    code: int
    message: str

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Binance API Error {code}: {message}")


# Credential material that must never reach the journal. A signed Binance URL
# embeds `signature=<hmac(secret, query)>`; the API key travels in the
# `X-MBX-APIKEY` header (and in `apiKey=` on some endpoints). httpx renders the
# FULL request URL into `HTTPStatusError`, so an exception built from a raw URL
# writes the HMAC into `journalctl` (observed live:
# `...&timestamp=1792240296815&signature=48e771c4153b5b92cfdfdee...`).
#
# The sensitive parameter is DROPPED, not blanked to `signature=REDACTED`: the
# key name plus a placeholder would still read like a value to a log scraper, and
# the remaining context (method, path, non-secret params, HTTP status) is what an
# operator actually needs.
_REDACTED = "[redacted]"
_SENSITIVE_QUERY_KEYS = ("signature", "apiKey", "apikey", "api_key")
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&])(" + "|".join(re.escape(k) for k in _SENSITIVE_QUERY_KEYS) +
    r")=[^&\s\"']*", re.IGNORECASE)
_SENSITIVE_HEADER_RE = re.compile(r"(X-MBX-APIKEY)(\s*[:=]\s*)([^\s&\"']+)", re.IGNORECASE)


def redact_url(url: str) -> str:
    """Strip credential material out of a URL/header blob before it is logged or
    embedded in an exception message.

    Redacts by KEY NAME, not by value: the signed query string is rebuilt per
    request, so a value-based scrub would miss every new timestamp/signature.
    """
    if not url:
        return url
    out = _SENSITIVE_QUERY_RE.sub(rf"\1{_REDACTED}", url)
    return _SENSITIVE_HEADER_RE.sub(rf"\1\2{_REDACTED}", out)


class RateLimiter:
    """Token bucket rate limiter for Binance REST API.

    Sized to Binance Futures' real caps so the bot stays under them:
    request WEIGHT 2400/min, plus ORDER/RAW-REQUEST 3000/day and 1200/5min.
    The bucket refills at `rate_per_sec` tokens (weight) and holds at most
    `max_tokens`, so a burst can never exceed the 1-min budget and the
    sustained rate can never exceed the 5-min budget.

    NOTE the earlier bug this replaces: `acquire` used to `await sleep()` while
    holding the lock AND refill a flat 20 tokens/sec from an oversized 1200
    bucket, so it never actually throttled — the bot consumed Binance's real
    budget many times faster than the limiter believed. The sleep now happens
    OUTSIDE the lock and the bucket is sized to the true 1-min cap.
    """

    def __init__(self, max_tokens: int = 2200, rate_per_sec: float = 36.0):
        self.max_tokens = max_tokens
        self.rate = rate_per_sec
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1):
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.max_tokens, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                # compute the wait, then sleep OUTSIDE the lock so other tasks
                # can keep consuming/refilling instead of serialising behind us
                wait = (weight - self._tokens) / self.rate
            await asyncio.sleep(wait)


class CircuitBreaker:
    """Prevents repeated Binance API failures from triggering rate-limit bans.

    Binance bans accounts that exceed rate limits (429s). If we get N consecutive
    failures (transport errors, 5xx, or 429s), we open the circuit and pause all
    trading for a reset period. This is better than spamming a rate-limited API
    and getting permanently banned.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit tripped, all requests rejected immediately
    - HALF_OPEN: Reset timeout elapsed, allow one probe request

    Triggered by:
    - 5 consecutive failures (default threshold)
    - Any single 429 rate limit response (immediate open)

    Reset:
    - 60 seconds after tripping (default timeout)
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    async def check(self) -> bool:
        """Check if requests are allowed. Returns True if CLOSED or HALF_OPEN."""
        async with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                # Check if reset timeout has elapsed
                if self._last_failure_time and \
                   time.monotonic() - self._last_failure_time > self.reset_timeout:
                    self._state = "HALF_OPEN"
                    logger.warning("Circuit breaker HALF_OPEN: allowing probe request")
                    return True
                return False
            # HALF_OPEN: allow one probe
            return True

    async def record_success(self):
        """Record a successful request. Reset failure counter."""
        async with self._lock:
            self._failure_count = 0
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                logger.info("Circuit breaker CLOSED: probe succeeded, resuming normal operation")

    async def record_failure(self, is_rate_limit: bool = False):
        """Record a failed request. Trip circuit if threshold exceeded."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if is_rate_limit:
                # Immediate open on rate limit — don't spam the API
                self._state = "OPEN"
                logger.error(
                    "Circuit breaker OPEN: Binance rate limit hit (429). "
                    "Pausing all API requests for %ds", self.reset_timeout)
            elif self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.error(
                    "Circuit breaker OPEN: %d consecutive failures. "
                    "Pausing for %ds to prevent rate-limit ban",
                    self._failure_count, self.reset_timeout)
            else:
                logger.warning(
                    "Circuit breaker: failure %d/%d",
                    self._failure_count, self.failure_threshold)

    async def get_status(self) -> dict:
        """Get current circuit breaker status for monitoring."""
        async with self._lock:
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "last_failure_time": self._last_failure_time,
                "reset_timeout": self.reset_timeout,
            }


class BinanceService:
    """Async wrapper around Binance Futures API."""

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, testnet: bool | None = None):
        self._api_key = api_key or settings.BINANCE_API_KEY
        self._api_secret = api_secret or settings.BINANCE_API_SECRET
        self._testnet = testnet if testnet is not None else settings.BINANCE_TESTNET
        self._base_url = TESTNET_BASE if self._testnet else PROD_BASE
        self._limiter = RateLimiter()
        self._circuit_breaker = CircuitBreaker()
        self._client: httpx.AsyncClient | None = None
        # symbol -> LOT_SIZE/MARKET_LOT_SIZE/PRICE_FILTER, cached for the
        # process lifetime. Filters change at most weekly; the uncached version
        # re-downloaded the full ~1MB exchangeInfo per order placement.
        self._lot_cache: dict[str, dict] = {}

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        """Load API credentials (e.g. from the encrypted Account table) at runtime.

        Takes precedence over the env-var key. Used so a key saved in the
        Settings UI actually reaches the engine without a redeploy/restart of
        env vars. Public endpoints (klines/ticker) work regardless.
        """
        self._api_key = api_key
        self._api_secret = api_secret

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(10.0),
                headers={"User-Agent": "FUBOT/1.0"},
            )
        return self._client

    def _error_from_response(self, resp: httpx.Response, method: str,
                             path: str) -> BinanceAPIError:
        """Build a `BinanceAPIError` from a 4xx/5xx response, keeping Binance's
        OWN code and message.

        `resp.raise_for_status()` throws the body away: a rejected order surfaced
        as `Client error '400 Bad Request' for url '...'` while the actionable
        `{"code": -2021, "msg": "ReduceOnly Order is rejected..."}` was never
        read, so diagnosing a live failure needed a journal dump and a code read.

        Falls back to the HTTP status when the body is not JSON — Binance serves
        an HTML error page for some 404s. The URL is redacted: it is only
        included for that fallback (the JSON branch carries no URL at all).
        """
        body = None
        try:
            body = resp.json()
        except Exception:
            body = None

        if isinstance(body, dict):
            msg = body.get("msg") or body.get("message")
            code = body.get("code")
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None
            if msg:
                return BinanceAPIError(
                    code if code is not None else resp.status_code,
                    f"{msg} [{method} {path}]")

        url = ""
        try:
            url = redact_url(str(resp.request.url))
        except Exception:
            url = ""
        return BinanceAPIError(
            resp.status_code,
            f"HTTP {resp.status_code} on {method} {path} — non-JSON error body"
            + (f" ({url})" if url else ""))

    async def _signed_request(
        self, method: str, path: str, params: dict | None = None, weight: int = 1
    ) -> dict:
        await self._limiter.acquire(weight)
        client = self._get_client()
        query = urlencode(params or {})
        url = f"/{path}{'?' + query if query else ''}"
        timestamp = int(time.time() * 1000)
        query_string = f"timestamp={timestamp}"
        if query:
            query_string = f"{query}&timestamp={timestamp}"
        signature = self._sign(query_string)

        headers = {"X-MBX-APIKEY": self._api_key}
        full_url = f"{self._base_url}/{path}?{query_string}&signature={signature}"

        for attempt in range(3):
            try:
                resp = await client.request(method, full_url, headers=headers)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 1))
                    # Rate limit hit — trip circuit breaker immediately
                    logger.warning(f"Rate limited (429), waiting {retry_after}s (attempt {attempt+1})")
                    await self._circuit_breaker.record_failure(is_rate_limit=True)
                    await asyncio.sleep(retry_after)
                    continue
                if resp.status_code >= 400:
                    raise self._error_from_response(resp, method, path)
                data = resp.json()
                if isinstance(data, dict) and "code" in data and "msg" in data:
                    # Algo endpoints return {"code": "200", "msg": "success"} on
                    # SUCCESS — only non-200 codes are errors (Binance error
                    # codes are negative or 4xx). `code` arrives as a STRING on
                    # some endpoints ("200" != 200 raised on every successful
                    # cancel_algo_order), so compare numerically.
                    try:
                        code = int(data["code"])
                    except (TypeError, ValueError):
                        # Unparseable code on a code+msg payload: treat as
                        # SUCCESS and warn. Inventing -1 here raised a spurious
                        # BinanceAPIError on a successful cancel, which callers
                        # swallowed at debug level — leaving duplicate stops
                        # armed (the "2 active SLs per position" bug).
                        logger.warning(
                            "Unparseable Binance code %r on %s %s — treating as "
                            "success", data.get("code"), method, path)
                        return data
                    if code != 200:
                        raise BinanceAPIError(code, data["msg"])
                return data
            except (httpx.HTTPStatusError, BinanceAPIError):
                raise
            except Exception as e:
                # Record failure for circuit breaker
                await self._circuit_breaker.record_failure()
                if method.upper() == "POST":
                    # A transport error on a POST leaves the exchange state
                    # UNKNOWN: the order may have been accepted and simply not
                    # answered. Resending it would place a DUPLICATE (there is
                    # no client-order-id dedupe on Binance's side). Note a 429
                    # is NOT this branch — it `continue`s above, and a rejected
                    # request genuinely did not execute, so retrying is safe.
                    logger.error(
                        "%s %s failed in transit (%s) — NOT retried: the request "
                        "may already have executed. Reconcile before retrying.",
                        method, path, e)
                    raise
                if attempt == 2:
                    logger.error(f"Request failed after 3 attempts: {e}")
                    raise
                await asyncio.sleep(2 ** attempt)

        # Retries exhausted (reached here only via repeated 429s, which `continue`
        # rather than raise). MUST raise, not return {}: every caller reads an
        # empty result as "flat" / "no positions" / "success". Returning {}
        # after a rate limit made _ensure_flat believe the exchange had already
        # closed the position (leaving it open and naked), and made the scan
        # loop believe no symbol was held — so it could open a SECOND position on
        # an already-exposed symbol.
        raise BinanceAPIError(
            -1003,
            f"Rate limited after 3 attempts on {method} {path} — state is UNKNOWN, "
            f"not empty. Callers must not treat this as 'flat'.")

    async def _public_request(self, path: str, params: dict | None = None, weight: int = 1) -> dict:
        await self._limiter.acquire(weight)
        client = self._get_client()
        query = urlencode(params or {})
        full_url = f"{self._base_url}/{path}{'?' + query if query else ''}"
        for attempt in range(3):
            try:
                resp = await client.get(full_url)
                if resp.status_code == 429:
                    await asyncio.sleep(1)
                    continue
                if resp.status_code >= 400:
                    raise self._error_from_response(resp, "GET", path)
                data = resp.json()
                if isinstance(data, dict) and "code" in data and "msg" in data:
                    # Mirror _signed_request: `code` may arrive as a STRING
                    # ("200"), and a code+msg payload with a 200 code is a
                    # success, not an error. Comparing raw here would fail-open
                    # on any endpoint that answers {"code":"200","msg":"ok"}.
                    try:
                        code = int(data["code"])
                    except (TypeError, ValueError):
                        logger.warning(
                            "Unparseable Binance code %r on %s — treating as success",
                            data.get("code"), path)
                        return data
                    if code != 200:
                        raise BinanceAPIError(code, data["msg"])
                return data
            except (httpx.HTTPStatusError, BinanceAPIError):
                raise
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        # Exhausted retries (repeated 429s). Raise — see _signed_request: a
        # falsy return here reads as "no data" to every caller.
        raise BinanceAPIError(
            -1003, f"Rate limited after 3 attempts on {path}")

    def _sign(self, query_string: str) -> str:
        import hmac
        import hashlib
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ---- Market Data (Public) ----

    async def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> list[dict]:
        """GET /fapi/v1/klines

        Weight scales with `limit` per Binance's table (1-100→1, 101-200→2,
        201-300→5, 301-500→10, 501-1000→10). The scan fetches 200 bars ×3
        intervals ×24 symbols per pass, so declaring these as weight 1 let the
        bot blow through the real budget ~2× faster than the limiter thought.
        """
        if limit <= 100:
            kweight = 1
        elif limit <= 200:
            kweight = 2
        elif limit <= 300:
            kweight = 5
        else:
            kweight = 10
        data = await self._public_request(
            "fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            weight=kweight,
        )
        return self._parse_klines(data)

    async def get_klines_window(self, symbol: str, interval: str,
                                start_ms: int, end_ms: int,
                                limit: int = 1500) -> list[dict]:
        """GET /fapi/v1/klines over an explicit TIME range — the backtest seam.

        `get_klines` takes only a `limit`, which is why the harness capped
        itself at 1000 bars (3.47 days of 5m) no matter what `--days` said.
        `startTime`/`endTime` are what let a caller walk a long window one
        page at a time; `backend/backtest_data.fetch_klines` drives it.

        Reuses `_public_request` (the shared rate limiter, 429 retry and
        error-body handling) rather than opening a second httpx client — a
        second client means a second limiter and a second blind spot.

        `limit` defaults to 1500, Binance's klines cap. Callers must size the
        range so the window holds no more than that many bars: ask for more and
        the overflow is silently dropped. `fetch_klines` guarantees this via
        `page_ranges`, which steps by `limit` bars OF `interval`.
        """
        data = await self._public_request(
            "fapi/v1/klines",
            {"symbol": symbol, "interval": interval,
             "startTime": int(start_ms), "endTime": int(end_ms), "limit": limit},
            weight=10,  # 501-1500 per Binance's klines weight table
        )
        return self._parse_klines(data)

    @staticmethod
    def _parse_klines(data) -> list[dict]:
        """Binance's positional kline array -> the dicts the rest of the app reads.

        Note `close_time` is kept RAW. It is the anti-lookahead boundary for
        every consumer (the backtest drops still-forming bars on it), and it is
        a millisecond timestamp, not a price — no coercion.
        """
        result = []
        for k in data:
            result.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_volume": float(k[9]),
            })
        return result

    async def get_symbol_info(self, symbol: str) -> dict:
        """GET /fapi/v1/exchangeInfo — filtered for one symbol.

        v1, not v2: /fapi/v2/exchangeInfo does not exist (v2 is the COIN-M
        path). It 404s.
        """
        data = await self._public_request("fapi/v1/exchangeInfo")
        for s in data.get("symbols", []):
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                return {
                    "symbol": symbol,
                    "tick_size": float(filters.get("PRICE_FILTER", {}).get("stepSize", "0.01")),
                    "step_size": float(filters.get("LOT_SIZE", {}).get("stepSize", "0.001")),
                    "min_qty": float(filters.get("LOT_SIZE", {}).get("minQty", "0")),
                    "max_qty": float(filters.get("LOT_SIZE", {}).get("maxQty", "999999")),
                    "notional": float(filters.get("NOTIONAL", {}).get("minNotional", "10")),
                }
        return {}

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """GET /fapi/v1/depth"""
        data = await self._public_request("fapi/v1/depth", {"symbol": symbol, "limit": limit})
        return {
            "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
            "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
        }

    async def get_ticker(self, symbol: str) -> dict:
        """GET /fapi/v1/ticker/price"""
        data = await self._public_request("fapi/v1/ticker/price", {"symbol": symbol})
        return {"symbol": symbol, "price": float(data.get("price", 0))}

    async def get_mark_price(self, symbol: str) -> dict:
        """GET /fapi/v1/premiumIndex — the mark price Binance itself uses for
        unrealized PnL. (fapi/v1/ticker/markPrice does not exist — the old
        path 404'd, so callers silently fell back to last-trade price.)"""
        data = await self._public_request("fapi/v1/premiumIndex", {"symbol": symbol})
        return {
            "markPrice": float(data.get("markPrice", 0)),
            "indexPrice": float(data.get("indexPrice", 0)),
        }

    async def get_open_interest(self, symbol: str) -> float:
        """GET /fapi/v1/openInterest"""
        data = await self._public_request("fapi/v1/openInterest", {"symbol": symbol})
        return float(data.get("openInterest", 0))

    async def get_funding_rate(self, symbol: str) -> float:
        """GET /fapi/v1/fundingRate"""
        data = await self._public_request("fapi/v1/fundingRate", {"symbol": symbol})
        return float(data.get("lastFundingRate", 0))

    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """GET /fapi/v1/trades"""
        data = await self._public_request("fapi/v1/trades", {"symbol": symbol, "limit": limit})
        return [
            {
                "id": t.get("id"),
                "price": float(t.get("price", 0)),
                "qty": float(t.get("qty", 0)),
                "quote_qty": float(t.get("quoteQty", 0)),
                "time": t.get("time"),
                "is_buyer_maker": t.get("isBuyerMaker"),
            }
            for t in data
        ]

    # ---- Account Data (Private) ----

    async def get_account_balance(self) -> list[dict]:
        """GET /fapi/v2/balance (weight 5).

        `balance` is the TOTAL account balance for the asset — what an equity
        curve must chart. `availableBalance` is FREE MARGIN and drops every time
        a position opens, so charting it made the equity curve look like a loss
        when only collateral was locked up. `availableBalance` is kept for
        position sizing, which legitimately wants free margin.
        """
        data = await self._signed_request("GET", "fapi/v2/balance", weight=5)
        return [
            {
                "asset": a["asset"],
                "balance": float(a.get("balance", 0) or 0),
                "availableBalance": float(a.get("availableBalance", 0) or 0),
                "maxWithdrawAmount": float(a.get("maxWithdrawAmount", 0) or 0),
                "initialMargin": float(a.get("initialMargin", 0) or 0),
                "unrealizedProfit": float(a.get("unrealizedProfit", 0) or 0),
            }
            for a in data
        ]

    async def get_account_equity(self) -> dict:
        """USD-denominated ACCOUNT totals — the correct source for an equity curve.

        GET /fapi/v2/account (weight 5). Returns account-level totals already
        valued in USDT across EVERY asset:

            totalMarginBalance      = Wallet Balance + Unrealized PnL  (equity)
            totalWalletBalance      = sum of all asset balances in USD
            totalUnrealizedProfit   = open-position PnL
            totalAvailableBalance   = free margin
            totalInitialMargin      = collateral posted
            totalMaintMargin        = maintenance margin

        WHY NOT `get_account_balance()`: that returns one row per asset, and the
        USDT row alone is NOT the account. This account also holds BTC and BNB
        as cross-margin collateral, so the USDT row was ~$23.90 while real
        margin balance was ~$223.72 — the equity curve understated the account
        by ~90%. Summing the `balance` field across assets does not fix it
        either: those are denominated in each asset (0.0022 BTC, 0.042 BNB), not
        USD, so they cannot be added.

        Live-only: an unauthenticated caller cannot read these.
        """
        data = await self._signed_request("GET", "fapi/v2/account", weight=5)

        def _f(key: str) -> float:
            try:
                return float(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        return {
            "margin_balance": _f("totalMarginBalance"),
            "wallet_balance": _f("totalWalletBalance"),
            "unrealized_pnl": _f("totalUnrealizedProfit"),
            "available_balance": _f("totalAvailableBalance"),
            "initial_margin": _f("totalInitialMargin"),
            "maintenance_margin": _f("totalMaintMargin"),
        }

    async def get_position(self, symbol: str) -> dict:
        """GET /fapi/v2/positionRisk — single symbol (weight 5)."""
        data = await self._signed_request("GET", "fapi/v2/positionRisk", {"symbol": symbol}, weight=5)
        for p in data:
            if p["symbol"] == symbol and float(p.get("positionAmt", 0)) != 0:
                return {
                    "symbol": p["symbol"],
                    "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                    "quantity": abs(float(p["positionAmt"])),
                    "entryPrice": float(p["entryPrice"]),
                    "markPrice": float(p.get("markPrice", 0) or 0),
                    "unrealizedPnl": float(p.get("unRealizedProfit", 0)),
                    "leverage": int(p.get("leverage", 1)),
                    "margin": float(p.get("isolatedWallet", 0)),
                }
        return {}

    async def get_all_positions(self) -> list[dict]:
        """GET /fapi/v2/positionRisk — all positions (weight 5)."""
        data = await self._signed_request("GET", "fapi/v2/positionRisk", weight=5)
        return [
            {
                "symbol": p["symbol"],
                "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                "quantity": abs(float(p["positionAmt"])),
                "entryPrice": float(p["entryPrice"]),
                "markPrice": float(p.get("markPrice", 0) or 0),
                "unrealizedPnl": float(p.get("unRealizedProfit", 0)),
                "leverage": int(p.get("leverage", 1)),
            }
            for p in data
            if float(p.get("positionAmt", 0)) != 0
        ]

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """POST /fapi/v1/leverage"""
        return await self._signed_request("POST", "fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, weight=1)

    # ---- Order Placement ----

    async def place_limit_order(self, symbol: str, side: str, price: float, quantity: float) -> dict:
        """POST /fapi/v1/order — LIMIT (GTX post-only for maker fills).
        GTX ensures we only rest on the book and never cross the spread as taker.
        If the order would immediately fill, Binance rejects it — we catch that
        and fall back to GTC with a warning."""
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        px = self._round_price(price, lot)

        # Try GTX (post-only) first for maker fees
        try:
            data = await self._signed_request(
                "POST", "fapi/v1/order",
                {"symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTX",
                 "quantity": qty, "price": px,
                 "newClientOrderId": self._next_client_order_id()},
                weight=2,
            )
            return {
                "orderId": data.get("orderId"),
                "symbol": symbol,
                "side": side,
                "price": px,
                "quantity": qty,
                "status": data.get("status", "NEW"),
                "timeInForce": "GTX",
            }
        except BinanceAPIError as e:
            # GTX rejected: order would have filled immediately (crossed spread)
            # Error -2019, -1002, -1015, or -5022 typically. Fall back to GTC.
            if e.code in (-2019, -1002, -1015, -5022):
                logger.warning(
                    "GTX rejected for %s %s (would fill immediately), "
                    "falling back to GTC. Code: %d", side, symbol, e.code)
                try:
                    data = await self._signed_request(
                        "POST", "fapi/v1/order",
                        {"symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
                         "quantity": qty, "price": px,
                         "newClientOrderId": self._next_client_order_id()},
                        weight=2,
                    )
                    return {
                        "orderId": data.get("orderId"),
                        "symbol": symbol,
                        "side": side,
                        "price": px,
                        "quantity": qty,
                        "status": data.get("status", "NEW"),
                        "timeInForce": "GTC",
                    }
                except BinanceAPIError as gtc_error:
                    logger.error("GTC order also failed: %s", gtc_error)
                    raise
            raise

    async def get_lot_size(self, symbol: str) -> dict:
        """LOT_SIZE + MARKET_LOT_SIZE + PRICE_FILTER for one symbol.

        RAISES instead of swallowing. This used to catch every exception and
        return {}, which made `_round_qty`/`_round_price` return their input
        UNROUNDED — so one exchangeInfo timeout during a live entry sent the SL
        with quantity 89.332934, Binance rejected it with -1013 Precision, and
        the already-filled position was left with NO stop. An empty filter dict
        is not a real Binance state, so it must never be a fallback.

        Cached per symbol: the full exchange descriptor is ~1MB and this was
        re-downloaded on every order placement (three per live entry). Filters
        change at most weekly.
        """
        cached = self._lot_cache.get(symbol)
        if cached is not None:
            return cached

        # NOTE the version: USDⓈ-M futures exposes exchangeInfo at
        # /fapi/v1/exchangeInfo. There is NO /fapi/v2/exchangeInfo on fapi —
        # v2 is the COIN-M path (dapi). Using v2 404s, and because get_lot_size
        # now RAISES (correctly) instead of returning {}, every order placement
        # aborts with "Live order flow failed: 404". This shipped live in
        # d9213ce — see the comment on the raise below.
        info = await self._public_request("fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                lot = {}
                for f in s.get("filters", []):
                    ft = f.get("filterType", "")
                    if ft in ("LOT_SIZE", "MARKET_LOT_SIZE", "PRICE_FILTER"):
                        lot[ft] = f
                if not lot:
                    raise BinanceAPIError(
                        -1100, f"No LOT_SIZE/PRICE_FILTER filters for {symbol}")
                self._lot_cache[symbol] = lot
                return lot
        raise BinanceAPIError(-1121, f"Invalid symbol {symbol} (no exchangeInfo entry)")

    def _round_qty(self, qty: float, lot_filters: dict) -> float:
        """Round quantity down to the LOT_SIZE step (MARKET_LOT_SIZE if present).

        Decimal-based: the naive `math.floor(qty / step)` mis-floors exact
        multiples through float error (89.3 / 0.1 == 892.9999… → 89.2), which
        left a 0.1-lot slice of a position uncovered by its reduce-only SL/TP.
        """
        from decimal import Decimal, ROUND_DOWN
        f = lot_filters.get("MARKET_LOT_SIZE") or lot_filters.get("LOT_SIZE")
        if not f:
            return qty
        step = Decimal(str(f.get("stepSize", "0.0001")))
        min_qty = float(f.get("minQty", "0"))
        rounded = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        rounded = float(rounded)
        if rounded < min_qty:
            raise ValueError(f"Quantity {qty} below minQty {min_qty}")
        return rounded

    def _round_price(self, price: float, lot_filters: dict) -> float:
        """Round price to the PRICE_FILTER tick size (Decimal — see _round_qty)."""
        from decimal import Decimal, ROUND_DOWN
        f = lot_filters.get("PRICE_FILTER")
        if not f:
            return price
        tick = Decimal(str(f.get("tickSize", "0.1")))
        min_price = float(f.get("minPrice", "0"))
        rounded = (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        rounded = float(rounded)
        if rounded < min_price:
            raise ValueError(f"Price {price} below minPrice {min_price}")
        return rounded

    def _next_client_order_id(self) -> str:
        """A unique `newClientOrderId` for an order POST.

        Without it, a POST that times out is indistinguishable from one that never
        landed: retrying risks a duplicate position, and `GET /fapi/v1/order
        ?origClientOrderId=` has nothing to look up. Binance matches
        `^[.A-Z:/a-z0-9_-]{1,36}$`, and rejects a repeated id, which turns our own
        retry-after-timeout into a hard error rather than a second fill.
        """
        return f"fubot-{uuid.uuid4().hex[:20]}"

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> dict:
        """POST /fapi/v1/order — MARKET. Rounds quantity to LOT_SIZE step.

        Returns avgPrice (actual fill price) from Binance — use this instead of
        the planned entry price. Binance fills at market, which can drift from
        the signal's entry-zone midpoint.

        `reduce_only` MUST be true for every CLOSING order (flatten, manual
        close, `close_all`, `_ensure_flat`). Between our DB read and this order
        the exchange's own TP/SL may already have filled, leaving the symbol flat;
        an unreduced MARKET then opens a fresh position in the opposite direction
        that no code path manages. With `reduceOnly=true` the same race makes the
        order a harmless rejection instead. It must be false for entries —
        Binance rejects reduceOnly on an order that would open exposure.
        """
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "newClientOrderId": self._next_client_order_id(),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        data = await self._signed_request(
            "POST", "fapi/v1/order", params, weight=2,
        )
        return {
            "orderId": data.get("orderId"),
            "symbol": symbol,
            "side": side,
            "executedQty": data.get("executedQty", "0"),
            "avgPrice": data.get("avgPrice", "0"),  # actual fill price
            "cumQuote": data.get("cumQuote", "0"),  # total quote qty (notional)
            "status": data.get("status", "FILLED"),
        }

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        """DELETE /fapi/v1/order — regular orders only. Algo (conditional)
        orders need cancel_algo_order."""
        return await self._signed_request(
            "DELETE", "fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    async def cancel_algo_order(self, symbol: str, algo_id: int) -> dict:
        """DELETE /fapi/v1/algoOrder — cancel a CONDITIONAL (STOP_MARKET /
        TAKE_PROFIT_MARKET) algo order. The param is `algoId` (lowercase 'd')."""
        return await self._signed_request(
            "DELETE", "fapi/v1/algoOrder",
            {"symbol": symbol, "algoId": algo_id},
        )

    async def get_order(self, symbol: str, order_id: int) -> dict:
        """GET /fapi/v1/order"""
        return await self._signed_request(
            "GET", "fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    async def modify_position_stop(self, symbol: str, side: str, stop_price: float, quantity: float) -> dict:
        """POST /fapi/v1/algoOrder — STOP_MARKET (reduce-only). Rounds qty + price.
        Binance migrated conditional orders to the Algo Order API (2025-12-09).

        Uses MARK_PRICE trigger to prevent stop-hunts from wicks. Last-price
        triggers are vulnerable to momentary spikes that sweep retail stops
        before reversing.
        """
        close_side = "SELL" if side == "LONG" else "BUY"
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        price = self._round_price(stop_price, lot)
        data = await self._signed_request(
            "POST", "fapi/v1/algoOrder",
            {"symbol": symbol, "side": close_side, "type": "STOP_MARKET",
             "triggerPrice": price, "quantity": qty, "reduceOnly": "true",
             "algoType": "CONDITIONAL",
             "workingType": "MARK_PRICE",  # Use mark price, not last price
             "newClientOrderId": self._next_client_order_id()},
            weight=2,
        )
        return {
            "orderId": data.get("algoId"),
            "symbol": symbol,
            "side": close_side,
            "status": data.get("algoStatus", "NEW"),
        }

    async def place_take_profit_order(self, symbol: str, side: str, tp_price: float, quantity: float) -> dict:
        """POST /fapi/v1/algoOrder — TAKE_PROFIT_MARKET (reduce-only). Rounds qty + price.

        Uses MARK_PRICE trigger for consistency with stop losses.
        """
        close_side = "SELL" if side == "LONG" else "BUY"
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        price = self._round_price(tp_price, lot)
        data = await self._signed_request(
            "POST", "fapi/v1/algoOrder",
            {"symbol": symbol, "side": close_side, "type": "TAKE_PROFIT_MARKET",
             "triggerPrice": price, "quantity": qty, "reduceOnly": "true",
             "algoType": "CONDITIONAL",
             "workingType": "MARK_PRICE",  # Use mark price for TP too
             "newClientOrderId": self._next_client_order_id()},
            weight=2,
        )
        return {
            "orderId": data.get("algoId"),
            "symbol": symbol,
            "side": close_side,
            "status": data.get("algoStatus", "NEW"),
        }

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """GET /fapi/v1/openOrders (weight 1 when symbol-scoped)."""
        data = await self._signed_request("GET", "fapi/v1/openOrders", {"symbol": symbol}, weight=1)
        return [
            {
                "orderId": o.get("orderId"),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "type": o.get("type"),
                "price": float(o.get("price", 0)),
                "origQty": float(o.get("origQty", 0)),
                "executedQty": float(o.get("executedQty", 0)),
                "status": o.get("status"),
                "time": o.get("time"),
            }
            for o in data
        ]

    async def get_open_algo_orders(self, symbol: str) -> list[dict]:
        """GET /fapi/v1/openAlgoOrders — resting conditional (SL/TP) orders.

        Used to sweep orphans on position close: orders placed outside the
        orders-table logging (BE-trail stops, manual re-placements) are
        invisible to the position-linked cancel and would otherwise survive
        the close."""
        data = await self._signed_request(
            "GET", "fapi/v1/openAlgoOrders", {"symbol": symbol}, weight=1)
        return [
            {
                "algoId": a.get("algoId"),
                "symbol": a.get("symbol"),
                "orderType": a.get("orderType"),
                "side": a.get("side"),
                "positionSide": a.get("positionSide"),
                "triggerPrice": a.get("triggerPrice"),
                "quantity": a.get("quantity"),
                "reduceOnly": a.get("reduceOnly"),
                "closePosition": a.get("closePosition"),
                "workingType": a.get("workingType"),
                "algoStatus": a.get("algoStatus"),
            }
            for a in data
        ]

    async def get_position_income(
        self,
        symbol: str,
        income_type: Optional[str] = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> dict:
        """GET /fapi/v1/income — realized PnL NET of commission and funding.

        Binance emits several income types per trade: ``REALIZED_PNL`` (gross),
        ``COMMISSION`` (negative) and ``FUNDING_FEE`` (negative for longs paid).
        Only the first is money the trader keeps; the other two are the cost of
        having the position at all.

        The window is deliberately queried UNFILTERED and classified here. Passing
        ``incomeType`` to Binance makes the server return only that one type, so the
        ``COMMISSION``/``FUNDING_FEE`` branches below become unreachable and
        ``totalPnl`` silently degrades to GROSS — which is how every live
        ``pnl_usdt``, ``pnl_r`` and the -3R breaker ended up fee-blind for a month.
        ``income_type`` remains only as a client-side filter for narrow queries.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            income_type: Optional client-side filter, e.g. "REALIZED_PNL". None
                (the default) keeps all three cost types and is what callers
                booking a close should use.
            start_time: Epoch ms, inclusive. Pass the POSITION's open time;
                without it the 7-day fallback also covers earlier positions on
                the same symbol.
            end_time: Epoch ms, inclusive. Defaults to now. Pass ``closed_at + a
                few minutes`` when recomputing history, so a later position on the
                same symbol cannot donate its fees to this close.

        Returns:
            {"totalPnl": net, "realizedPnl": gross, "fees": <0, "funding": <0}
        """
        if start_time is None:
            start_time = int(
                (datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
        if end_time is None:
            end_time = int(datetime.now(timezone.utc).timestamp() * 1000)

        params = {
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": 1000,
        }
        # NOTE: incomeType is deliberately NOT sent. See the docstring.
        data = await self._signed_request("GET", "fapi/v1/income", params)
        
        # Sum income events
        realized_pnl = 0.0
        fees = 0.0
        funding = 0.0
        
        for item in data:
            income = float(item.get("income", 0))
            itype = item.get("incomeType", "")
            if income_type is not None and itype != income_type:
                continue

            if itype == "REALIZED_PNL":
                realized_pnl += income
            elif itype == "COMMISSION":
                fees += income  # negative
            elif itype == "FUNDING_FEE":
                funding += income
            # TRANSFER / WELCOME_BONUS / anything else is not PnL — skipped.
        
        total = realized_pnl + fees + funding
        
        return {
            "totalPnl": total,
            "realizedPnl": realized_pnl,
            "fees": fees,
            "funding": funding,
            "events": len(data),
        }

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
