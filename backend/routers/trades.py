"""Trade log router — list, stats, filters, pagination."""
import logging
from datetime import datetime, date, timezone, timedelta
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.sql import func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.trade import Trade
from backend.schemas.trade import TradeResponse, TradeListResponse, TradeStatsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trades", tags=["trades"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _apply_trade_filters(
    query,
    *,
    symbol: Optional[str],
    side: Optional[str],
    outcome: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    mode: Optional[str] = None,
):
    """Apply the shared Trade-Log filters to a query.

    Used by BOTH `/trades` (for the page and, separately, for the total count)
    and `/trades/stats` (for the summary cards). They were previously duplicated
    inline — the same six filters written out three times — which is how the
    cards drifted out of sync with the table: `/trades/stats` accepted no
    filters at all, so filtering the table to BTCUSDT still showed "45 trades,
    40% win rate" from all-time history directly above a 5-row table.
    """
    if symbol:
        query = query.where(Trade.symbol == symbol)
    if side:
        query = query.where(Trade.side == side)
    if outcome == "win":
        query = query.where(Trade.pnl_r > 0)
    elif outcome == "loss":
        query = query.where(Trade.pnl_r < 0)
    elif outcome == "breakeven":
        query = query.where(Trade.pnl_r == 0)
    if mode and mode != "all":
        query = query.where(Trade.mode == mode)
    if from_date:
        try:
            query = query.where(Trade.closed_at >= datetime.fromisoformat(from_date))
        except ValueError:
            pass  # an unparseable date must not 500 the page; ignore it
    if to_date:
        try:
            td = datetime.fromisoformat(to_date)
            # A bare YYYY-MM-DD parses to midnight at the START of that day, so
            # a `to_date` of 2026-10-17 would silently exclude every trade from
            # the 17th. Extend to the next midnight to include the whole day.
            if len(to_date) == 10:
                td = td + timedelta(days=1)
            query = query.where(Trade.closed_at < td)
        except ValueError:
            pass
    return query


@router.get("/", response_model=TradeListResponse)
async def list_trades(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    symbol: Optional[str] = Query(None),
    side: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    user = await get_current_user_from_cookie(request, db)

    filters = dict(symbol=symbol, side=side, outcome=outcome,
                   from_date=from_date, to_date=to_date)
    query = _apply_trade_filters(select(Trade), **filters)
    count_query = _apply_trade_filters(
        select(sqlfunc.count()).select_from(Trade), **filters)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    pages = ceil(total / limit) if total > 0 else 1

    query = query.order_by(Trade.closed_at.desc()).offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    trades = result.scalars().all()

    return TradeListResponse(
        trades=[TradeResponse(
            id=t.id,
            position_id=t.position_id,
            symbol=t.symbol,
            side=t.side,
            entry_price=float(t.entry_price),
            exit_price=float(t.exit_price),
            quantity=float(t.quantity),
            pnl_usdt=float(t.pnl_usdt),
            pnl_r=float(t.pnl_r),
            exit_reason=t.exit_reason,
            strategy_type=t.strategy_type,
            created_at=t.created_at,
            closed_at=t.closed_at,
        ) for t in trades],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/stats", response_model=TradeStatsResponse)
async def trade_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: Optional[int] = Query(None, ge=1, le=730),
    mode: Optional[str] = Query("live", pattern="^(live|paper|all)$"),
    symbol: Optional[str] = Query(None),
    side: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Headline stats for the Performance page AND the Trade Log summary cards.

    Accepts the SAME filters as `/trades` plus `days`. Two range controls, two
    separate bugs of the same shape:

    - `days` so the Performance cards honour the range buttons. Without it they
      were ALL-TIME while every chart beside them respected 7d/30d/90d/365d.
    - symbol/side/outcome/from_date/to_date so the Trade Log cards describe the
      FILTERED set. Without them, filtering the table to one symbol still showed
      "45 trades, 40% win rate" from all-time history directly above a 5-row
      table — which reads as "the filters don't work".

    Live-only by default: paper fills are not real money and must never be
    averaged into a live win-rate or R-per-trade figure.

    pnl_r is coerced through float() — the column is Numeric and comes back as
    Decimal, so a NULL would raise TypeError inside the sum().
    """
    user = await get_current_user_from_cookie(request, db)

    query = _apply_trade_filters(
        select(Trade),
        symbol=symbol, side=side, outcome=outcome,
        from_date=from_date, to_date=to_date,
        mode=mode or "live",
    )
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(Trade.closed_at >= cutoff)
    result = await db.execute(query)
    trades = [float(t.pnl_r or 0) for t in result.scalars().all()]

    total = len(trades)
    wins = sum(1 for r in trades if r > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    avg_r = (sum(trades) / total) if total > 0 else 0.0
    best_trade = max(trades, default=0.0)
    worst_trade = min(trades, default=0.0)

    return TradeStatsResponse(
        total_trades=total,
        win_rate=round(win_rate, 2),
        avg_r=round(avg_r, 4),
        best_trade=best_trade,
        worst_trade=worst_trade,
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return TradeResponse(
        id=trade.id,
        position_id=trade.position_id,
        symbol=trade.symbol,
        side=trade.side,
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price),
        quantity=float(trade.quantity),
        pnl_usdt=float(trade.pnl_usdt),
        pnl_r=float(trade.pnl_r),
        exit_reason=trade.exit_reason,
        strategy_type=trade.strategy_type,
        created_at=trade.created_at,
        closed_at=trade.closed_at,
    )
