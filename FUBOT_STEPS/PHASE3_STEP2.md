# Phase 3 — Step 2: Risk Service — Position Sizing & R:R Validation
## FUBOT: Risk Management Core

---

## Objective
Build `RiskService` — the component that calculates correct position size from the account balance and risk %, validates R:R before any trade is taken, and enforces the -3R daily loss limit.

---

## Context
- **Risk model**: Risk a fixed % of account balance per trade (1-2%)
- **Position sizing**: `quantity = account_balance × risk_percent / stop_distance_pct`
- **Daily loss limit**: Cumulative daily P&L in R units must not exceed -3R
- **R:R gate**: No trade unless R:R >= configured minimum (default 3:1)
- **Depends on**: Phase 3 Step 1 (OrderService)

---

## Position Sizing Formula

```
Risk Per Trade ($) = Account Balance × Risk %
Position Size ($) = Risk Per Trade ÷ Stop Distance (%)
Stop Distance (%) = |Entry Price - Stop Loss| / Entry Price

Example:
  Account Balance = $180
  Risk % = 1%
  Risk $ = $180 × 0.01 = $1.80
  Entry = $62,500, Stop = $62,000 → Stop Distance = 0.8%
  Position Size = $1.80 / 0.008 = $225 notional
  At 10× leverage → Margin required = $22.50
```

---

## RiskService: `backend/services/risk_service.py`

```python
@dataclass
class PositionSize:
    quantity: float          # Number of contracts
    notional_value: float    # Position value in USDT
    margin_required: float   # Margin at current leverage
    risk_usdt: float         # Actual $ risk
    leverage_used: int       # Leverage applied

@dataclass
class RiskCheck:
    is_valid: bool
    reason: str
    rr_ratio: float | None
    position_size: PositionSize | None

class RiskService:
    def __init__(
        self,
        db: AsyncSession,
        account: BinanceAccountService,
        settings: dict
    ):
        self.db = db
        self.account = account
        self.settings = settings

    async def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        leverage: int | None = None
    ) -> PositionSize:
        """
        Calculate the position size for a given entry and stop loss.

        1. Get available account balance
        2. Calculate risk $ from risk %
        3. Calculate stop distance in %
        4. Calculate notional position size
        5. Apply leverage and calculate margin
        """
        balance = await self.account.get_available_balance()

        risk_percent = self.settings.get('risk_percent', 1.0) / 100.0
        risk_amount = balance * risk_percent

        stop_distance_pct = abs(entry_price - stop_loss) / entry_price
        stop_distance_pct = max(stop_distance_pct, 0.0001)  # Prevent division by zero

        notional = risk_amount / stop_distance_pct

        # Apply leverage
        lev = leverage or self._get_default_leverage(symbol)
        margin = notional / lev
        risk_usdt = notional * stop_distance_pct  # Should equal risk_amount

        return PositionSize(
            quantity=self._round_to_step_size(notional / entry_price, symbol),
            notional_value=notional,
            margin_required=margin,
            risk_usdt=risk_usdt,
            leverage_used=lev
        )

    async def validate_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        direction: str
    ) -> RiskCheck:
        """
        Full risk validation before any order is placed.
        Checks:
          1. R:R >= minimum threshold
          2. Account has sufficient margin for position
          3. Daily loss limit not exceeded
          4. Max positions not exceeded
        """
        # 1. R:R Check
        rr_ratio = self._calculate_rr(entry_price, stop_loss, take_profit, direction)
        rr_min = self.settings.get('rr_min', 3.0)
        if rr_ratio < rr_min:
            return RiskCheck(
                is_valid=False,
                reason=f"R:R {rr_ratio:.2f} < minimum {rr_min}",
                rr_ratio=rr_ratio,
                position_size=None
            )

        # 2. Position size check
        try:
            size = await self.calculate_position_size(symbol, entry_price, stop_loss)
        except Exception as e:
            return RiskCheck(is_valid=False, reason=f"Position sizing error: {e}", rr_ratio=rr_ratio, position_size=None)

        balance = await self.account.get_available_balance()
        if size.margin_required > balance * 0.95:  # Keep 5% buffer
            return RiskCheck(
                is_valid=False,
                reason=f"Insufficient margin: need ${size.margin_required:.2f}, have ${balance:.2f}",
                rr_ratio=rr_ratio,
                position_size=size
            )

        # 3. Daily loss limit check
        daily_check = await self._check_daily_loss_limit()
        if not daily_check['allowed']:
            return RiskCheck(
                is_valid=False,
                reason=f"Daily loss limit reached: {daily_check['used_r']}R used",
                rr_ratio=rr_ratio,
                position_size=size
            )

        # 4. Max positions check
        open_count = await self._get_open_position_count()
        max_pos = self.settings.get('max_positions', 3)
        if open_count >= max_pos:
            return RiskCheck(
                is_valid=False,
                reason=f"Max positions ({max_pos}) already open",
                rr_ratio=rr_ratio,
                position_size=size
            )

        return RiskCheck(is_valid=True, reason="OK", rr_ratio=rr_ratio, position_size=size)

    def _calculate_rr(self, entry, stop, tp, direction) -> float:
        """Calculate actual R:R ratio."""
        if direction == 'LONG':
            risk = entry - stop
            reward = tp - entry
        else:
            risk = stop - entry
            reward = entry - tp
        if risk <= 0:
            return 0.0
        return reward / risk

    def _round_to_step_size(self, quantity: float, symbol: str) -> float:
        """
        Round quantity to Binance's step size for the symbol.
        BTCUSDT: step_size = 0.001
        ETHUSDT: step_size = 0.01
        """
        step_sizes = {
            'BTCUSDT': 0.001,
            'ETHUSDT': 0.01,
            'BNBUSDT': 0.01,
            'SOLUSDT': 0.01,
            'default': 0.01
        }
        step = step_sizes.get(symbol, 0.01)
        return float(int(quantity / step) * step)
```

---

## Daily Loss Limit Tracking

```python
async def _check_daily_loss_limit(self) -> dict:
    """
    Check cumulative daily P&L in R units.
    Returns: {'allowed': bool, 'used_r': float, 'limit_r': float}
    """
    today = datetime.utcnow().date()

    result = await self.db.execute(
        select(daily_summary).where(daily_summary.c.date == today)
    )
    summary = result.scalar_one_or_none()

    limit_r = abs(self.settings.get('daily_loss_limit_r', -3.0))
    used_r = abs(summary.total_pnl_r) if summary else 0.0

    return {
        'allowed': used_r < limit_r,
        'used_r': used_r,
        'limit_r': limit_r,
        'remaining_r': limit_r - used_r
    }
```

---

## Deliverables

### 1. `backend/services/risk_service.py`
- `PositionSize` dataclass
- `RiskCheck` dataclass
- `RiskService` class
- `calculate_position_size()` — core sizing formula
- `validate_trade()` — full pre-trade risk check

### 2. Update `backend/services/__init__.py`
Export `RiskService`.

### 3. Unit Tests `tests/backend/test_risk_service.py`
```python
def test_position_size_calculation():
    """$180 balance, 1% risk, entry=62500, stop=62000"""
    # Risk $ = 180 * 0.01 = $1.80
    # Stop distance = 500/62500 = 0.8%
    # Position = 1.80 / 0.008 = $225 notional
    # At 10x: margin = $22.50
    size = asyncio.run(risk.calculate_position_size(
        "BTCUSDT", 62500.0, 62000.0, 10
    ))
    assert size.notional_value == pytest.approx(225.0, rel=0.1)
    assert size.margin_required == pytest.approx(22.5, rel=0.1)

def test_rr_validation_passes():
    # Entry 62500, SL 62000 (500$), TP 64000 (1500$) → 3:1
    check = asyncio.run(risk.validate_trade("BTCUSDT", 62500, 62000, 64000, "LONG"))
    assert check.is_valid is True

def test_rr_validation_fails():
    # Entry 62500, SL 62000 (500$), TP 63000 (500$) → 1:1
    check = asyncio.run(risk.validate_trade("BTCUSDT", 62500, 62000, 63000, "LONG"))
    assert check.is_valid is False
    assert "R:R" in check.reason

def test_insufficient_margin():
    # Very tight stop would require enormous position
    check = asyncio.run(risk.validate_trade("BTCUSDT", 62500, 62499, 65000, "LONG"))
    assert check.is_valid is False
```

### 4. Validation
```python
# Test position sizing formula
risk = RiskService(db, account, settings)
size = risk.calculate_position_size("BTCUSDT", 62500, 62000, 10)
assert size.quantity > 0
assert size.margin_required < 30  # Should be ~$22

# Test R:R gate
check = risk.validate_trade("BTCUSDT", 62500, 62000, 64000, "LONG")
assert check.is_valid is True
```

---

## Dependencies
- **Requires**: Phase 3 Step 1

## Files Modified/Created
- `backend/services/risk_service.py`
- `backend/services/__init__.py` (updated)
- `tests/backend/test_risk_service.py`

## Next Step
Phase 3 Step 3: Strategy Service — Trade Decision Logic

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
