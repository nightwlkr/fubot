# Phase 3 — Step 1: Order Service + Binance Execution
## FUBOT: Async Order Placement, Modification & Cancellation

---

## Objective
Build `OrderService` — the service responsible for all order interactions with Binance: placing limit orders, modifying stops, cancelling orders, and tracking order state. All Binance trading happens through this service.

---

## Context
- **Framework**: python-binance async client (or raw httpx with signed requests)
- **Order types used**: LIMIT (primary entry), STOP_MARKET (stop loss), TAKE_PROFIT_MARKET (TP)
- **Position linking**: Each order must be linked to a `signal_id` in the DB
- **Depends on**: Phase 1 Step 3 (BinanceService exists), Phase 2 Step 7 (SignalService)

---

## Order Types We Use

| Order Type | Purpose | Price |
|---|---|---|
| `LIMIT` | Entry | FVG midpoint or pullback zone |
| `STOP_MARKET` | Stop Loss | Structural stop price |
| `TAKE_PROFIT_MARKET` | Take Profit | Structural TP price |
| `MARKET` | Emergency exit only | Market price |

---

## OrderService: `backend/services/order_service.py`

```python
@dataclass
class OrderResult:
    success: bool
    binance_order_id: int | None
    status: str | None
    filled_qty: float | None
    avg_fill_price: float | None
    error: str | None

class OrderService:
    def __init__(
        self,
        db: AsyncSession,
        binance: BinanceService,
        binance_account: BinanceAccountService  # For account-level ops
    ):
        self.db = db
        self.binance = binance
        self.account = binance_account

    # --- Entry Orders ---

    async def place_entry_order(
        self,
        signal: Signal,
        entry_price: float,
        quantity: float
    ) -> OrderResult:
        """
        Place a LIMIT buy/sell order at entry_price.
        Side: LONG → BUY, SHORT → SELL
        Returns: OrderResult with binance_order_id
        """

    async def place_stop_loss(
        self,
        position_id: int,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float
    ) -> OrderResult:
        """
        Place a STOP_MARKET order as stop loss.
        Binance STOP_MARKET: triggers when mark price crosses stop_price.
        """

    async def place_take_profit(
        self,
        position_id: int,
        symbol: str,
        side: str,
        tp_price: float,
        quantity: float
    ) -> OrderResult:
        """
        Place a TAKE_PROFIT_MARKET order as TP.
        Triggers when mark price crosses tp_price.
        """

    # --- Order Management ---

    async def cancel_order(self, symbol: str, order_id: int) -> OrderResult:
        """
        Cancel an open (unfilled) order.
        """

    async def get_order_status(self, symbol: str, order_id: int) -> OrderResult:
        """
        Query Binance for current order status.
        Returns filled_qty, avg_fill_price if filled.
        """

    async def modify_stop_loss(
        self,
        symbol: str,
        current_order_id: int,
        new_stop_price: float,
        quantity: float
    ) -> OrderResult:
        """
        Cancel existing SL order and place new one.
        Must be done atomically — if cancel fails, don't place new one.
        """

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """
        Get all open orders for symbol.
        """

    # --- Position Helpers ---

    async def get_current_position(self, symbol: str) -> dict | None:
        """
        Get open position from Binance.
        Returns: {'quantity', 'entry_price', 'unrealized_pnl', 'side'}
        Returns None if no open position.
        """

    async def set_symbol_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for symbol.
        Returns True if successful.
        """
```

---

## Stop Loss Placement (Binance Specific)

On Binance Futures, stop losses are placed as **separate conditional orders**, not as parameters on the entry order.

```python
async def place_stop_loss(
    self,
    symbol: str,
    side: str,        # 'LONG' or 'SHORT'
    stop_price: float,
    quantity: float
) -> OrderResult:
    """
    For LONG: SL triggers when price FALLS to stop_price → sell to exit
    For SHORT: SL triggers when price RISES to stop_price → buy to cover

    Binance API:
    - For LONG: stop_price is below entry → use STOP_MARKET with side SELL
    - For SHORT: stop_price is above entry → use STOP_MARKET with side BUY

    Note: Use REDUCE_ONLY for stop losses to avoid accidentally opening new positions.
    """
    if side == 'LONG':
        # Price falling to stop_price triggers sell (close long)
        stop_side = 'SELL'
        trigger_side = 'BELOW'  # price crosses below
    else:
        stop_side = 'BUY'
        trigger_side = 'ABOVE'  # price crosses above

    result = await self.binance.client.futures_place_order(
        symbol=symbol,
        side=stop_side,
        type='STOP_MARKET',
        quantity=quantity,
        stop_price=stop_price,
        reduce_only=True  # Critical: only closes position, doesn't open new one
    )
    return OrderResult(success=True, binance_order_id=result['orderId'], ...)
```

---

## Order Tracking in Database

Every order placed must be logged in the `orders` table:

```python
async def _log_order(self, order_result: OrderResult, order_params: dict, signal_id: int):
    order = Order(
        binance_order_id=order_result.binance_order_id,
        symbol=order_params['symbol'],
        side=order_params['side'],
        order_type=order_params['type'],
        price=order_params.get('price'),
        quantity=order_params['quantity'],
        filled_qty=order_result.filled_qty or 0,
        status=order_result.status or 'NEW',
    )
    self.db.add(order)
    await self.db.commit()
```

---

## Risk Rules (Hardcoded in OrderService)

1. **Never place an order that would use more than available margin**
2. **Never place a SELL order without a STOP_MARKET attached**
3. **Never place a TAKE_PROFIT that is closer than 1.5× ATR to entry**
4. **Reduce-only always**: All closing orders use `reduce_only=True`
5. **Position quantity from Binance**: Always verify quantity via `futures_position_information` before placing SL/TP

---

## Validation with Testnet

```python
# In test environment (BINANCE_TESTNET=true):
svc = OrderService(db, binance, account)

# 1. Place a limit buy on BTCUSDT
result = await svc.place_entry_order(
    signal=signal_obj,
    entry_price=62500.0,
    quantity=0.01  # 0.01 BTC
)
assert result.success is True
assert result.binance_order_id is not None

# 2. Place stop loss
sl_result = await svc.place_stop_loss(
    symbol='BTCUSDT',
    side='LONG',
    stop_price=62200.0,
    quantity=0.01
)
assert sl_result.success is True

# 3. Cancel the limit order
cancel_result = await svc.cancel_order('BTCUSDT', result.binance_order_id)
assert cancel_result.success is True
```

---

## Deliverables

### 1. `backend/services/order_service.py`
- `OrderResult` dataclass
- `OrderService` class with all methods
- Full Binance Futures order placement

### 2. `backend/services/account_service.py` (new)
For account-level operations: balance, leverage, position info.
```python
class BinanceAccountService:
    async def get_balance(self) -> float  # Available USDT
    async def set_leverage(self, symbol: str, leverage: int)
    async def get_position(self, symbol: str) -> dict | None
```

### 3. Update `backend/services/__init__.py`
Export `OrderService`, `BinanceAccountService`.

### 4. Tests `tests/backend/test_order_service.py`
- Mock Binance API responses
- Test order placement flow
- Test that reduce_only is always set for closing orders
- Test SL modification

### 5. Validation
- All methods work on testnet
- Orders appear in Binance testnet order history
- Order records appear in `orders` table

---

## Dependencies
- **Requires**: Phase 1 Step 3 (BinanceService), Phase 2 Step 7

## Files Modified/Created
- `backend/services/order_service.py`
- `backend/services/account_service.py`
- `backend/services/__init__.py` (updated)
- `tests/backend/test_order_service.py`

## Next Step
Phase 3 Step 2: Risk Service — Position Sizing & R:R Validation

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
