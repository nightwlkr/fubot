# Phase 4 — Step 3: REST API Completion
## FUBOT: All Dashboard Endpoints

---

## Objective
Implement all remaining REST API routers that the React dashboard needs: dashboard overview, positions, trades, signals, settings, orders, performance.

---

## Context
- **All routes are protected** — require valid JWT in cookie (except `/api/auth/*`)
- **Use**: `Depends(get_current_user_from_cookie)` as the auth dependency
- **Depends on**: Phase 1 Step 4 (auth), Phase 3 (data models exist)

---

## Routers to Implement

### 1. `backend/routers/dashboard.py` — `/api/dashboard`

```python
@router.get("/overview")
async def dashboard_overview(user=Depends(get_current_user)):
    """Main dashboard overview data."""
    today = datetime.utcnow().date()

    # Today's stats
    daily = await get_daily_summary(today)
    open_positions = await db.execute(select(Position).where(Position.status == 'OPEN'))
    positions = open_positions.scalars().all()

    # Equity curve (last 30 days)
    equity_rows = await db.execute(
        select(EquityLog).order_by(EquityLog.recorded_at.desc()).limit(30)
    )
    equity = [{'date': r.recorded_at, 'balance': r.balance_usdt} for r in equity_rows]

    # Quick stats
    stats = await db.execute(select(Trade))
    trades = stats.scalars().all()
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_r > 0)
    win_rate = (wins / total * 100) if total > 0 else 0

    return {
        'bot_status': 'running',  # from BotEngine
        'today_pnl': daily.total_pnl_usdt if daily else 0,
        'today_r': daily.total_pnl_r if daily else 0,
        'open_positions': [format_position(p) for p in positions],
        'equity_curve': equity,
        'quick_stats': {
            'total_trades': total,
            'win_rate': round(win_rate, 1),
            'best_trade': max((t.pnl_r for t in trades), default=0),
            'worst_trade': min((t.pnl_r for t in trades), default=0),
        }
    }

@router.get("/today")
async def today_stats(user=Depends(get_current_user)):
    daily = await get_daily_summary(datetime.utcnow().date())
    return format_daily_summary(daily)
```

### 2. `backend/routers/positions.py` — `/api/positions`

```python
@router.get("/")
async def list_positions(
    status: str | None = None,
    symbol: str | None = None,
    user=Depends(get_current_user)
):
    query = select(Position)
    if status: query = query.where(Position.status == status)
    if symbol: query = query.where(Position.symbol == symbol)
    query = query.order_by(Position.opened_at.desc())

    result = await db.execute(query)
    positions = result.scalars().all()
    return {'positions': [format_position(p) for p in positions]}

@router.patch("/{position_id}")
async def update_position(
    position_id: int,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    user=Depends(get_current_user)
):
    """Modify SL or TP of an open position."""
    position = await db.get(Position, position_id)
    if not position or position.status != 'OPEN':
        raise HTTPException(404, "Position not found or not open")

    if stop_loss:
        # Cancel old SL, place new SL via OrderService
        await order_service.modify_stop_loss(position.symbol, position.sl_order_id, stop_loss, position.quantity)
        position.stop_loss = stop_loss

    if take_profit:
        position.take_profit = take_profit

    await db.commit()
    return {'position': format_position(position)}
```

### 3. `backend/routers/trades.py` — `/api/trades`

```python
@router.get("/")
async def list_trades(
    page: int = 1,
    limit: int = 50,
    symbol: str | None = None,
    side: str | None = None,
    outcome: str | None = None,  # win, loss, breakeven
    from_date: date | None = None,
    to_date: date | None = None,
    user=Depends(get_current_user)
):
    query = select(Trade)
    if symbol: query = query.where(Trade.symbol == symbol)
    if side: query = query.where(Trade.side == side)
    if outcome == 'win': query = query.where(Trade.pnl_r > 0)
    elif outcome == 'loss': query = query.where(Trade.pnl_r < 0)
    elif outcome == 'breakeven': query = query.where(Trade.pnl_r == 0)
    if from_date: query = query.where(Trade.closed_at >= from_date)
    if to_date: query = query.where(Trade.closed_at <= to_date)

    total = await db.scalar(select(count()).select_from(Trade).where(...))
    trades = await db.execute(query.offset((page-1)*limit).limit(limit))
    return {'trades': [format_trade(t) for t in trades], 'total': total, 'page': page, 'pages': ceil(total/limit)}

@router.get("/stats")
async def trade_stats(user=Depends(get_current_user)):
    trades = await db.execute(select(Trade))
    all_trades = trades.scalars().all()
    total = len(all_trades)
    wins = sum(1 for t in all_trades if t.pnl_r > 0)
    return {
        'total_trades': total,
        'win_rate': wins/total*100 if total else 0,
        'avg_r': sum(t.pnl_r for t in all_trades)/total if total else 0,
        'best_trade': max((t.pnl_r for t in all_trades), default=0),
        'worst_trade': min((t.pnl_r for t in all_trades), default=0),
    }
```

### 4. `backend/routers/signals.py` — `/api/signals`

```python
@router.get("/")
async def list_signals(
    status: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    user=Depends(get_current_user)
):
    query = select(Signal)
    if status: query = query.where(Signal.status == status)
    if symbol: query = query.where(Signal.symbol == symbol)
    if strategy: query = query.where(Signal.strategy_type == strategy)
    query = query.order_by(Signal.created_at.desc()).limit(100)

    result = await db.execute(query)
    return {'signals': [format_signal(s) for s in result.scalars().all()]}

@router.get("/active")
async def active_signals(user=Depends(get_current_user)):
    result = await db.execute(
        select(Signal).where(Signal.status == 'PENDING').order_by(Signal.created_at.desc())
    )
    return {'signals': [format_signal(s) for s in result.scalars().all()]}

@router.get("/missed")
async def missed_signals(days: int = 7, user=Depends(get_current_user)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Signal).where(
            Signal.status == 'SKIPPED',
            Signal.created_at >= cutoff
        ).order_by(Signal.created_at.desc())
    )
    return {'signals': [format_signal(s) for s in result.scalars().all()]}
```

### 5. `backend/routers/settings.py` — `/api/settings`

```python
@router.get("/")
async def get_public_settings(user=Depends(get_current_user)):
    """Get all public settings (no secrets)."""
    settings = await db.execute(select(Setting))
    return {'settings': {s.key: s.value for s in settings.scalars().all()}}

@router.put("/trading")
async def update_trading_settings(
    risk_percent: float | None = None,
    max_positions: int | None = None,
    daily_loss_limit_r: float | None = None,
    rr_min: float | None = None,
    check_interval_seconds: int | None = None,
    user=Depends(get_current_user)
):
    """Update trading parameters."""
    updates = {}
    if risk_percent is not None: updates['risk_percent'] = risk_percent
    if max_positions is not None: updates['max_positions'] = max_positions
    if daily_loss_limit_r is not None: updates['daily_loss_limit_r'] = daily_loss_limit_r
    if rr_min is not None: updates['rr_min'] = rr_min
    if check_interval_seconds is not None: updates['check_interval_seconds'] = check_interval_seconds

    for key, value in updates.items():
        await db.execute(
            insert(Setting).values(key=key, value=value)
            .on_conflict_do_update(index_elements=['key'], set_={'value': value})
        )
    await db.commit()
    return {'ok': True}

@router.get("/symbols")
async def list_symbols(user=Depends(get_current_user)):
    result = await db.execute(select(Symbol))
    return {'symbols': [{'symbol': s.symbol, 'enabled': s.enabled, 'max_leverage': s.max_leverage} for s in result.scalars().all()]}

@router.put("/symbols/{symbol}")
async def update_symbol(
    symbol: str,
    enabled: bool | None = None,
    max_leverage: int | None = None,
    user=Depends(get_current_user)
):
    sym = await db.execute(select(Symbol).where(Symbol.symbol == symbol))
    sym = sym.scalar_one_or_none()
    if not sym:
        raise HTTPException(404, "Symbol not found")
    if enabled is not None: sym.enabled = enabled
    if max_leverage is not None: sym.max_leverage = max_leverage
    await db.commit()
    return {'symbol': sym.symbol, 'enabled': sym.enabled, 'max_leverage': sym.max_leverage}
```

### 6. `backend/routers/orders.py` — `/api/orders`

```python
@router.get("/")
async def list_orders(
    status: str | None = None,
    symbol: str | None = None,
    user=Depends(get_current_user)
):
    query = select(Order)
    if status: query = query.where(Order.status == status)
    if symbol: query = query.where(Order.symbol == symbol)
    query = query.order_by(Order.created_at.desc()).limit(50)
    result = await db.execute(query)
    return {'orders': [format_order(o) for o in result.scalars().all()]}

@router.delete("/{order_id}")
async def cancel_order(order_id: int, user=Depends(get_current_user)):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    result = await order_service.cancel_order(order.symbol, order.binance_order_id)
    if result.success:
        order.status = 'CANCELLED'
        await db.commit()
    return {'ok': result.success, 'error': result.error}
```

### 7. `backend/routers/performance.py` — `/api/performance`

```python
@router.get("/equity")
async def equity_curve(days: int = 365, user=Depends(get_current_user)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(EquityLog)
            .where(EquityLog.recorded_at >= cutoff)
            .order_by(EquityLog.recorded_at)
    )
    return {'data': [{'date': r.recorded_at, 'balance': r.balance_usdt} for r in result.scalars().all()]}

@router.get("/monthly")
async def monthly_returns(user=Depends(get_current_user)):
    result = await db.execute(select(DailySummary).order_by(DailySummary.date))
    summaries = result.scalars().all()
    return {'data': [{
        'month': s.date.strftime('%Y-%m'),
        'pnl_r': s.total_pnl_r,
        'trades': s.total_trades,
        'win_rate': s.wins / s.total_trades * 100 if s.total_trades else 0
    } for s in summaries]}

@router.get("/strategies")
async def strategy_performance(user=Depends(get_current_user)):
    trades = await db.execute(select(Trade))
    all_trades = trades.scalars().all()
    by_strategy = {}
    for t in all_trades:
        strat = t.strategy_type
        if strat not in by_strategy:
            by_strategy[strat] = []
        by_strategy[strat].append(t)

    return {'data': [{
        'strategy': strat,
        'trades': len(trades),
        'win_rate': sum(1 for t in trades if t.pnl_r > 0) / len(trades) * 100 if trades else 0,
        'avg_r': sum(t.pnl_r for t in trades) / len(trades) if trades else 0
    } for strat, trades in by_strategy.items()]}
```

---

## Deliverables

### 1. All Router Files
- `backend/routers/dashboard.py`
- `backend/routers/positions.py`
- `backend/routers/trades.py`
- `backend/routers/signals.py`
- `backend/routers/settings.py`
- `backend/routers/orders.py`
- `backend/routers/performance.py`

### 2. Helper Formatters
Create `backend/schemas/responses.py` with shared response formatters:
```python
def format_position(p: Position) -> dict: ...
def format_trade(t: Trade) -> dict: ...
def format_signal(s: Signal) -> dict: ...
def format_order(o: Order) -> dict: ...
def format_daily_summary(s: DailySummary) -> dict: ...
```

### 3. Update `backend/main.py`
Include all new routers.

### 4. Validation
```bash
# As authenticated user:
curl -H "Cookie: fubot_token=..." https://fubot.bedikian.cc/api/dashboard/overview
curl -H "Cookie: fubot_token=..." https://fubot.bedikian.cc/api/trades?limit=5
curl -H "Cookie: fubot_token=..." https://fubot.bedikian.cc/api/signals/active
curl -H "Cookie: fubot_token=..." https://fubot.bedikian.cc/api/settings
```

---

## Dependencies
- **Requires**: Phase 1 Step 4, Phase 3

## Files Modified/Created
- `backend/routers/dashboard.py`
- `backend/routers/positions.py`
- `backend/routers/trades.py`
- `backend/routers/signals.py`
- `backend/routers/settings.py`
- `backend/routers/orders.py`
- `backend/routers/performance.py`
- `backend/schemas/responses.py`
- `backend/main.py` (updated)

## Next Step
Phase 4 Step 4: Connect Live + 72h Stability Test

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
