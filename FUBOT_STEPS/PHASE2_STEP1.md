# Phase 2 — Step 1: Binance Market Data Service
## FUBOT: REST + WebSocket Market Data

---

## Objective
Build on the existing `BinanceService` class (Phase 1 Step 3) to add complete market data collection: klines, orderbook depth, ticker, mark price, funding rate, open interest, and real-time WebSocket streams for live price/volume data.

---

## Context
- **Binance Futures base URL**: `https://fapi.binance.com` (prod) / `https://testnet.binancefuture.com` (testnet)
- **WebSocket base**: `wss://fstream.binance.com` (prod) / `wss://testnet.binancefuture.com` (testnet)
- **reuse**: `BinanceService` from Phase 1 Step 3 — extend it, don't rewrite
- **Depends on**: Phase 1 Step 3 (basic BinanceService exists)

---

## Additions to `backend/services/binance_service.py`

### A. REST Market Data (Public)

```python
async def get_klines(symbol: str, interval: str, limit: int = 200) -> list[dict]:
    """
    GET /fapi/v1/klines
    Returns list of OHLCV candles.
    interval: '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'
    Returns: [{
        'open_time': int (ms),
        'open': float,
        'high': float,
        'low': float,
        'close': float,
        'volume': float,
        'close_time': int,
        'quote_volume': float,
        'trades': int,
        'taker_buy_volume': float,
    }, ...]
    """

async def get_funding_rate(symbol: str) -> float:
    """
    GET /fapi/v1/fundingRate?symbol=BTCUSDT
    Returns most recent funding rate (annualized, e.g. 0.0001 = 0.01%)
    """

async def get_open_interest(symbol: str) -> float:
    """
    GET /fapi/v1/openInterest?symbol=BTCUSDT
    Returns open interest in USDT
    """

async def get_recent_trades(symbol: str, limit: int = 50) -> list[dict]:
    """
    GET /fapi/v1/trades?symbol=BTCUSDT&limit=50
    Returns: [{'id', 'price', 'qty', 'quote_qty', 'time', 'is_buyer_maker'}, ...]
    """
```

### B. WebSocket Streams (Public — no auth needed)

All WebSocket methods should:
- Accept a `callback: Callable[[dict], None]` function
- Run as background tasks using `asyncio.create_task`
- Handle reconnection automatically (exponential backoff: 1s, 2s, 4s, max 30s)
- Stop cleanly on `await ws.close()`

```python
class BinanceWebSocketManager:
    """Manages multiple WS subscriptions."""

    def __init__(self, testnet: bool = False):
        self.testnet = testnet
        self.ws: WebSocket | None = None
        self.subscriptions: dict[str, Callable] = {}
        self._listen_task: asyncio.Task | None = None
        self._reconnect_delay: float = 1.0

    async def connect(self):
        """Establish WS connection."""

    async def subscribe(self, stream: str, callback: Callable):
        """
        Subscribe to a stream.
        stream format: '<symbol>@<stream_type>'
        e.g. 'btcusdt@kline_1m', 'btcusdt@aggTrade', 'btcusdt@bookTicker'
        """

    async def unsubscribe(self, stream: str):
        """Unsubscribe from a stream."""

    async def close(self):
        """Close WS connection gracefully."""

    # --- Convenience methods ---

    async def watch_aggtrades(self, symbol: str, callback: Callable):
        """
        stream: <symbol>@aggTrade
        callback receives: {'symbol', 'price', 'quantity', 'is_buyer_maker', 'trade_time'}
        """

    async def watch_klines(self, symbol: str, interval: str, callback: Callable):
        """
        stream: <symbol>@kline_<interval>
        callback receives: {
            'symbol', 'interval', 'open', 'high', 'low', 'close',
            'volume', 'is_closed', 'open_time', 'close_time'
        }
        """

    async def watch_book_ticker(self, symbol: str, callback: Callable):
        """
        stream: <symbol>@bookTicker
        callback receives: {
            'symbol', 'bid_price', 'bid_qty', 'ask_price', 'ask_qty',
            'update_id'
        }
        """
```

---

## Rate Limiting

Binance Futures REST: **1200 weight/minute**
- Public endpoints: weight = 1 (klines), 5 (ticker), 10 (book)
- Private endpoints: weight = 1 (account), 1 (orders)

Implement a **token bucket rate limiter**:
```python
class RateLimiter:
    """
    Token bucket: refills at rate per second.
    max_tokens: 1200
    rate: 20/second (1200/60)
    """
```

Wrap all Binance REST calls through the rate limiter. If rate limit hit (HTTP 429), wait the `Retry-After` header value before retrying.

---

## Error Handling

Create custom exception class:
```python
class BinanceAPIError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Binance API Error {code}: {message}")
```

Handle:
- HTTP 429 (rate limited) → wait and retry
- HTTP 4xx → raise `BinanceAPIError` with code + message
- HTTP 5xx → wait and retry (Binance server error)
- Network timeout → retry up to 3 times with exponential backoff

---

## Deliverables

### 1. Update `backend/services/binance_service.py`
Add all new methods above. Keep backward compatibility with Phase 1 Step 3 methods.

### 2. `backend/services/binance_ws.py` (new file)
New `BinanceWebSocketManager` class.

### 3. Update `backend/services/__init__.py`
Export both `BinanceService` and `BinanceWebSocketManager`.

### 4. Tests `tests/backend/test_binance_market_data.py`
- Mock responses using `respx` or `httpx/mock`
- Test rate limiter doesn't exceed 1200/min
- Test WS reconnection logic
- Test klines parsing correctness

### 5. Validation
```python
# In a Python shell or test:
import asyncio
from backend.services import BinanceService, BinanceWebSocketManager

# REST
svc = BinanceService(api_key="test", api_secret="test", testnet=True)
klines = await svc.get_klines("BTCUSDT", "1h", 100)
print(len(klines))  # Should be 100
print(klines[0]['close'])  # Should be float

# WebSocket
ws = BinanceWebSocketManager(testnet=True)
messages = []
await ws.watch_klines("btcusdt", "1m", lambda m: messages.append(m))
await asyncio.sleep(5)  # Wait 5 seconds
await ws.close()
print(len(messages))  # Should have received several kline updates
```

---

## Dependencies
- **Requires**: Phase 1 Step 3 (BinanceService exists)

## Files Modified/Created
- `backend/services/binance_service.py` (updated)
- `backend/services/binance_ws.py` (new)
- `backend/services/__init__.py` (updated)
- `tests/backend/test_binance_market_data.py` (new)

## Next Step
Phase 2 Step 2: 50 SMA + RSI Indicators

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
