# Phase 1 — Step 3: AES-256 Encryption + Binance Service
## FUBOT: Secure API Key Storage & Exchange Connectivity

---

## Objective
Implement AES-256-GCM encryption for the Binance API key/secret, and build a `BinanceService` class that wraps all Binance Futures API calls needed by the bot. This is a critical security component — the API key must never appear in plaintext in logs or memory longer than necessary.

---

## Context
- **Exchange**: Binance Futures USDS-M (`https://fapi.binance.com`)
- **WebSocket**: `wss://fstream.binance.com`
- **Encryption**: AES-256-GCM — encryption key from `FUBOT_ENCRYPTION_KEY` env var
- **Depends on**: Phase 1 Steps 1 and 2

---

## Part A: Encryption (`backend/encryption.py`)

### Requirements
- Use Python's `cryptography` library (Fernet/AES-GCM)
- Key: 32-byte key from `FUBOT_ENCRYPTION_KEY` env var (hex decode or raw bytes)
- API key and secret are encrypted before being stored in the `accounts` table
- Provide two functions:

```python
def encrypt(plaintext: str, key: bytes) -> str:
    """Returns base64-encoded ciphertext (includes nonce)"""

def decrypt(ciphertext: str, key: bytes) -> str:
    """Returns original plaintext string"""
```

- **Never log the plaintext API key** — only the encrypted form or last 4 characters (`****ABCD`)
- The `key` is the `FUBOT_ENCRYPTION_KEY` env var — it must NOT be stored in the database

### Implementation
- Use `cryptography.hazmat.primitives.ciphers.aead.AESGCM`
- Generate a random 12-byte nonce per encryption
- Output format: `base64(nonce + ciphertext)` — self-contained, can be stored as text
- Decrypt by extracting nonce from first 12 bytes

---

## Part B: BinanceService (`backend/services/binance_service.py`)

### Architecture
- `BinanceService` class — **not** a singleton
- Initialized with decrypted API key + secret
- All methods are `async`
- Use `python-binance` client in async mode OR raw `httpx` async calls
- Rate limiting: Binance Futures allows 1200 weight/min on REST — implement a token bucket

### Required Methods

#### Market Data (Public — no API key needed)
```python
async def get_klines(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    """
    Returns candlestick data.
    interval: '1m', '5m', '15m', '1h', '4h', '1d'
    Returns: [{'open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time'}, ...]
    """

async def get_symbol_info(symbol: str) -> dict:
    """
    Returns symbol metadata: tick_size, step_size, min_qty, max_qty, notional.
    """

async def get_order_book(symbol: str, limit: int = 20) -> dict:
    """
    Returns: {'bids': [[price, qty], ...], 'asks': [[price, qty], ...]}
    """

async def get_ticker(symbol: str) -> dict:
    """
    Returns: {'price': float, 'symbol': str}
    """

async def get_mark_price(symbol: str) -> dict:
    """
    Returns: {'markPrice': float, 'indexPrice': float}
    """

async def get_open_interest(symbol: str) -> dict:
    """
    Returns: {'openInterest': float}
    """
```

#### Account Data (Requires API key — signed requests)
```python
async def get_account_balance() -> list[dict]:
    """
    Returns: [{'asset': 'USDT', 'availableBalance': float, 'initialMargin'}, ...]
    """

async def get_position(symbol: str) -> dict:
    """
    Returns position info for symbol.
    Returns: {'symbol', 'side', 'quantity', 'entryPrice', 'unrealizedPnl', 'leverage', 'margin'}
    Returns empty dict if no open position.
    """

async def get_all_positions() -> list[dict]:
    """
    Returns all open positions.
    """

async def set_leverage(symbol: str, leverage: int) -> dict:
    """
    Sets leverage for symbol. Returns API response.
    """
```

#### Order Placement (Trade execution — requires API key)
```python
async def place_limit_order(symbol: str, side: str, price: float, quantity: float) -> dict:
    """
    Places a LIMIT buy/sell order.
    Returns: {'orderId', 'symbol', 'side', 'price', 'quantity', 'status'}
    """

async def place_market_order(symbol: str, side: str, quantity: float) -> dict:
    """
    Places a MARKET buy/sell order.
    Returns: {'orderId', 'symbol', 'side', 'executedQty', 'status'}
    """

async def cancel_order(symbol: str, order_id: int) -> dict:
    """
    Cancels an open order.
    """

async def get_order(symbol: str, order_id: int) -> dict:
    """
    Gets status of a specific order.
    """

async def modify_position_stop(symbol: str, stop_price: float) -> dict:
    """
    Adds or modifies the stop-loss order for the open position.
    Uses Binance's position side: STOP_MARKET or TP_SL_MARKET.
    """

async def get_open_orders(symbol: str) -> list[dict]:
    """
    Returns all open (unfilled) orders for symbol.
    """
```

#### WebSocket Market Streams (Public — no API key)
```python
async def ws_aggtrade(symbol: str, callback: Callable):
    """
    Subscribes to aggregate trade stream for symbol.
    callback(data) is called on each trade.
    Data: {'symbol', 'price', 'quantity', 'is_buyer_maker', 'trade_time'}
    """

async def ws_kline(symbol: str, interval: str, callback: Callable):
    """
    Subscribes to candlestick stream.
    callback(data) is called on each kline update.
    """

async def ws_book_ticker(symbol: str, callback: Callable):
    """
    Subscribes to best bid/ask updates.
    callback(data) called on each update: {'symbol', 'bid_price', 'ask_price', 'bid_qty', 'ask_qty'}
    """
```

### Binance Testnet Support
- When `BINANCE_TESTNET=true`, use:
  - REST: `https://testnet.binancefuture.com`
  - WebSocket: `wss://testnet.binancefuture.com`
- When `BINANCE_TESTNET=false`, use production endpoints
- All code paths must handle testnet correctly

---

## Deliverables

### 1. `backend/encryption.py`
- `encrypt()` and `decrypt()` functions
- Documented, tested, no logging of plaintext

### 2. `backend/services/binance_service.py`
- Complete `BinanceService` class
- All methods listed above implemented
- Rate limiting (token bucket)
- Proper error handling — Binance API errors should raise a custom exception class
- **No API key logged anywhere** — use masked print statements for debugging: `print(f"API Key: {api_key[:4]}****")`

### 3. `backend/services/__init__.py`
Export `BinanceService`

### 4. Unit Tests `tests/backend/test_binance_service.py`
- Mock Binance API responses with `httpx` mock or `respx`
- Test encryption/decryption round-trip
- Test rate limiter doesn't exceed 1200 weight/min
- Test all methods return correct data types

### 5. Validation
- Can instantiate `BinanceService` with test credentials
- `get_klines` returns valid OHLCV data for BTCUSDT
- `place_limit_order` works on testnet
- Encrypt/decrypt roundtrip works
- Plaintext key never appears in stdout or logs

---

## Dependencies
- **Requires**: Phase 1 Steps 1 and 2

## Files Modified/Created
- `backend/encryption.py`
- `backend/services/binance_service.py`
- `backend/services/__init__.py`
- `tests/backend/test_binance_service.py`
- `tests/backend/test_encryption.py`

## Next Step
Phase 1 Step 4: User Auth — Models, Service & Login Endpoint

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
