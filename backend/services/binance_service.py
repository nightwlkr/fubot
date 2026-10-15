"""Binance Futures service with async REST + WebSocket support."""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator
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


class RateLimiter:
    """Token bucket rate limiter for Binance REST API (1200 weight/min)."""

    def __init__(self, max_tokens: int = 1200, rate_per_sec: float = 20.0):
        self.max_tokens = max_tokens
        self.rate = rate_per_sec
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.max_tokens, self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens < weight:
                wait = (weight - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= weight


class BinanceService:
    """Async wrapper around Binance Futures API."""

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, testnet: bool | None = None):
        self._api_key = api_key or settings.BINANCE_API_KEY
        self._api_secret = api_secret or settings.BINANCE_API_SECRET
        self._testnet = testnet if testnet is not None else settings.BINANCE_TESTNET
        self._base_url = TESTNET_BASE if self._testnet else PROD_BASE
        self._limiter = RateLimiter()
        self._client: httpx.AsyncClient | None = None

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
                    logger.warning(f"Rate limited, waiting {retry_after}s (attempt {attempt+1})")
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
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
                        code = -1
                    if code != 200:
                        raise BinanceAPIError(code, data["msg"])
                return data
            except (httpx.HTTPStatusError, BinanceAPIError):
                raise
            except Exception as e:
                if attempt == 2:
                    logger.error(f"Request failed after 3 attempts: {e}")
                    raise
                await asyncio.sleep(2 ** attempt)
        return {}

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
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "code" in data and "msg" in data:
                    raise BinanceAPIError(data["code"], data["msg"])
                return data
            except (httpx.HTTPStatusError, BinanceAPIError):
                raise
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        return {}

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
        """GET /fapi/v1/klines"""
        data = await self._public_request("fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
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
        """GET /fapi/v2/exchangeInfo — filtered for one symbol."""
        data = await self._public_request("fapi/v2/exchangeInfo")
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
        """GET /fapi/v2/balance"""
        data = await self._signed_request("GET", "fapi/v2/balance")
        return [
            {"asset": a["asset"], "availableBalance": float(a.get("availableBalance", 0)), "initialMargin": float(a.get("initialMargin", 0))}
            for a in data
        ]

    async def get_position(self, symbol: str) -> dict:
        """GET /fapi/v2/positionRisk — single symbol."""
        data = await self._signed_request("GET", "fapi/v2/positionRisk", {"symbol": symbol})
        for p in data:
            if p["symbol"] == symbol and float(p.get("positionAmt", 0)) != 0:
                return {
                    "symbol": p["symbol"],
                    "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                    "quantity": abs(float(p["positionAmt"])),
                    "entryPrice": float(p["entryPrice"]),
                    "unrealizedPnl": float(p.get("unRealizedProfit", 0)),
                    "leverage": int(p.get("leverage", 1)),
                    "margin": float(p.get("isolatedWallet", 0)),
                }
        return {}

    async def get_all_positions(self) -> list[dict]:
        """GET /fapi/v2/positionRisk — all positions."""
        data = await self._signed_request("GET", "fapi/v2/positionRisk")
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
        """POST /fapi/v1/order — LIMIT"""
        data = await self._signed_request(
            "POST", "fapi/v1/order",
            {"symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
             "quantity": quantity, "price": price},
            weight=2,
        )
        return {
            "orderId": data.get("orderId"),
            "symbol": symbol,
            "side": side,
            "price": price,
            "quantity": quantity,
            "status": data.get("status", "NEW"),
        }

    async def get_lot_size(self, symbol: str) -> dict:
        """GET /fapi/v1/exchangeInfo — LOT_SIZE + MARKET_LOT_SIZE + PRICE_FILTER for one symbol."""
        try:
            info = await self._public_request("fapi/v1/exchangeInfo")
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol:
                    lot = {}
                    for f in s.get("filters", []):
                        ft = f.get("filterType", "")
                        if ft in ("LOT_SIZE", "MARKET_LOT_SIZE", "PRICE_FILTER"):
                            lot[ft] = f
                    return lot
        except Exception:
            pass
        return {}

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

    async def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        """POST /fapi/v1/order — MARKET. Rounds quantity to LOT_SIZE step.
        
        Returns avgPrice (actual fill price) from Binance — use this instead of
        the planned entry price. Binance fills at market, which can drift from
        the signal's entry-zone midpoint.
        """
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        data = await self._signed_request(
            "POST", "fapi/v1/order",
            {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty},
            weight=2,
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
        Binance migrated conditional orders to the Algo Order API (2025-12-09)."""
        close_side = "SELL" if side == "LONG" else "BUY"
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        price = self._round_price(stop_price, lot)
        data = await self._signed_request(
            "POST", "fapi/v1/algoOrder",
            {"symbol": symbol, "side": close_side, "type": "STOP_MARKET",
             "triggerPrice": price, "quantity": qty, "reduceOnly": "true",
             "algoType": "CONDITIONAL"},
            weight=2,
        )
        return {
            "orderId": data.get("algoId"),
            "symbol": symbol,
            "side": close_side,
            "status": data.get("algoStatus", "NEW"),
        }

    async def place_take_profit_order(self, symbol: str, side: str, tp_price: float, quantity: float) -> dict:
        """POST /fapi/v1/algoOrder — TAKE_PROFIT_MARKET (reduce-only). Rounds qty + price."""
        close_side = "SELL" if side == "LONG" else "BUY"
        lot = await self.get_lot_size(symbol)
        qty = self._round_qty(quantity, lot)
        price = self._round_price(tp_price, lot)
        data = await self._signed_request(
            "POST", "fapi/v1/algoOrder",
            {"symbol": symbol, "side": close_side, "type": "TAKE_PROFIT_MARKET",
             "triggerPrice": price, "quantity": qty, "reduceOnly": "true",
             "algoType": "CONDITIONAL"},
            weight=2,
        )
        return {
            "orderId": data.get("algoId"),
            "symbol": symbol,
            "side": close_side,
            "status": data.get("algoStatus", "NEW"),
        }

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """GET /fapi/v1/openOrders"""
        data = await self._signed_request("GET", "fapi/v1/openOrders", {"symbol": symbol})
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
            "GET", "fapi/v1/openAlgoOrders", {"symbol": symbol})
        return [
            {
                "algoId": a.get("algoId"),
                "symbol": a.get("symbol"),
                "orderType": a.get("orderType"),
                "triggerPrice": a.get("triggerPrice"),
                "quantity": a.get("quantity"),
                "algoStatus": a.get("algoStatus"),
            }
            for a in data
        ]

    async def get_position_income(
        self, symbol: str, income_type: str = "REALIZED_PNL", start_time: int | None = None
    ) -> dict:
        """GET /fapi/v1/income — fetch realized PnL + fees for a symbol.
        
        Binance returns income events (REALIZED_PNL, COMMISSION, FUNDING_FEE).
        This fetches events and sums them to get the actual PnL including
        fees. Use this after closing a position to record exchange-truth PnL.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            income_type: Filter by type, default "REALIZED_PNL" (includes fees)
            start_time: Epoch ms — only sum events from this instant. Pass the
                POSITION's open time; without it the 7-day window would also
                sum a previous position's PnL on the same symbol.
        
        Returns:
            {"totalPnl": float, "realizedPnl": float, "fees": float, "funding": float}
        """
        # Last 7 days of income (or since the given instant)
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        if start_time is None:
            start_time = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
        
        data = await self._signed_request(
            "GET", "fapi/v1/income",
            {
                "symbol": symbol,
                "incomeType": income_type,
                "startTime": start_time,
                "endTime": end_time,
                "limit": 1000,
            }
        )
        
        # Sum income events
        realized_pnl = 0.0
        fees = 0.0
        funding = 0.0
        
        for item in data:
            income = float(item.get("income", 0))
            itype = item.get("incomeType", "")
            
            if itype == "REALIZED_PNL":
                realized_pnl += income
            elif itype == "COMMISSION":
                fees += income  # negative
            elif itype == "FUNDING_FEE":
                funding += income
        
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
