# Phase 3 — Step 6: Alert Service
## FUBOT: Trade Notifications

---

## Objective
Build `AlertService` — the notification system that sends alerts to the user when important events happen: trades executed, TP/SL hit, daily limit warnings, bot errors.

---

## Context
- **Channel**: For v1, log to console + WebSocket push to dashboard
- **Future**: Telegram bot (Phase 2+), email
- **Alert levels**: `info`, `warn`, `error`, `critical`
- **Depends on**: Phase 1 Step 4 (auth)

---

## AlertService: `backend/services/alert_service.py`

```python
from enum import Enum

class AlertLevel(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class Alert:
    level: AlertLevel
    message: str
    timestamp: datetime
    symbol: str | None = None
    metadata: dict | None = None

class AlertService:
    def __init__(
        self,
        db: AsyncSession,
        ws_manager: WebSocketManager  # For pushing to dashboard
    ):
        self.db = db
        self.ws = ws_manager

    async def send(
        self,
        level: str,
        message: str,
        symbol: str | None = None,
        metadata: dict | None = None
    ) -> Alert:
        """
        Send an alert via all configured channels.
        """
        alert = Alert(
            level=AlertLevel(level),
            message=message,
            timestamp=datetime.utcnow(),
            symbol=symbol,
            metadata=metadata
        )

        # 1. Log to console with level-based formatting
        self._log_to_console(alert)

        # 2. Push to WebSocket dashboard (if connected)
        await self._push_to_websocket(alert)

        # 3. Log to DB (optional — for history)
        await self._log_to_db(alert)

        # 4. Future: Telegram, email
        return alert

    def _log_to_console(self, alert: Alert):
        symbols = f"[{alert.symbol}] " if alert.symbol else ""
        level_tags = {
            'INFO': '',
            'WARN': '⚠️',
            'ERROR': '❌',
            'CRITICAL': '🚨'
        }
        tag = level_tags.get(alert.level.value, '')
        print(f"{tag} {alert.timestamp.isoformat()} {symbols}{alert.message}")

    async def _push_to_websocket(self, alert: Alert):
        if self.ws:
            await self.ws.broadcast({
                'type': 'alert',
                'data': {
                    'level': alert.level.value,
                    'message': alert.message,
                    'symbol': alert.symbol,
                    'timestamp': alert.timestamp.isoformat(),
                    'metadata': alert.metadata
                }
            })

    async def _log_to_db(self, alert: Alert):
        # Optional: store alerts in a dedicated alerts table
        # For now, just log
        pass
```

---

## Standard Alert Messages

```python
# Trade executed
await alerts.send('info', f"🚀 Entry: {signal.direction} {signal.symbol} @ {entry_price:.2f}, SL: {sl:.2f}, TP: {tp:.2f}, R:R: {rr:.1f}", symbol=signal.symbol, metadata={'signal_id': signal.id})

# Stop loss hit
await alerts.send('warn', f"❌ SL hit on {position.symbol}: PnL {pnl_r:.2f}R", symbol=position.symbol)

# Take profit hit
await alerts.send('info', f"✅ TP hit on {position.symbol}: PnL {pnl_r:.2f}R", symbol=position.symbol)

# BE trailing activated
await alerts.send('info', f"💰 {position.symbol} SL moved to BE", symbol=position.symbol)

# Daily limit warning
await alerts.send('warn', f"⚠️ Daily limit warning: {used_r:.1f}R / {limit_r:.1f}R used")

# Daily limit hit
await alerts.send('error', f"🚨 DAILY LOSS LIMIT HIT: Bot stopped for the day.")

# Bot started/stopped
await alerts.send('info', "▶️ FUBOT started")
await alerts.send('info', "⏹️ FUBOT stopped")

# New signal detected
await alerts.send('info', f"📡 Signal: {signal.direction} {signal.symbol} ({signal.strategy_type}), R:R: {signal.rr_ratio:.1f}", symbol=signal.symbol)
```

---

## Deliverables

### 1. `backend/services/alert_service.py`
- `AlertLevel` enum
- `Alert` dataclass
- `AlertService` class
- All channel methods

### 2. Update `backend/services/__init__.py`
Export `AlertService`.

### 3. Validation
```python
alerts = AlertService(db, ws_manager)

# Info alert
alert = await alerts.send('info', 'Test info alert')
assert alert.level == AlertLevel.INFO

# Error alert
alert = await alerts.send('error', 'Test error alert', symbol='BTCUSDT')
assert alert.symbol == 'BTCUSDT'
```

---

## Dependencies
- **Requires**: Phase 1 Step 4 (WebSocket manager reference)

## Files Modified/Created
- `backend/services/alert_service.py`
- `backend/services/__init__.py` (updated)

## Next Step
Phase 3 Step 7: Paper Trading Test (72h)

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
