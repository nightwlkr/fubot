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

    query = select(Trade)

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
    if from_date:
        try:
            fd = datetime.fromisoformat(from_date)
            query = query.where(Trade.closed_at >= fd)
        except ValueError:
            pass
    if to_date:
        try:
            td = datetime.fromisoformat(to_date)
            # Include the full end day (fromisoformat gives midnight at start)
            if len(to_date) == 10:  # YYYY-MM-DD format
                td = td + timedelta(days=1)
            query = query.where(Trade.closed_at < td)
        except ValueError:
            pass

    # Count total
    count_query = select(sqlfunc.count()).select_from(Trade)
    if symbol:
        count_query = count_query.where(Trade.symbol == symbol)
    if side:
        count_query = count_query.where(Trade.side == side)
    if outcome == "win":
        count_query = count_query.where(Trade.pnl_r > 0)
    elif outcome == "loss":
        count_query = count_query.where(Trade.pnl_r < 0)
    elif outcome == "breakeven":
        count_query = count_query.where(Trade.pnl_r == 0)
    if from_date:
        try:
            fd = datetime.fromisoformat(from_date)
            count_query = count_query.where(Trade.closed_at >= fd)
        except ValueError:
            pass
    if to_date:
        try:
            td = datetime.fromisoformat(to_date)
            if len(to_date) == 10:
                td = td + timedelta(days=1)
            count_query = count_query.where(Trade.closed_at < td)
        except ValueError:
            pass

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
):
    user = await get_current_user_from_cookie(request, db)
    result = await db.execute(select(Trade))
    trades = result.scalars().all()
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_r > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    avg_r = sum(t.pnl_r for t in trades) / total if total > 0 else 0.0
    best_trade = max((t.pnl_r for t in trades), default=0.0)
    worst_trade = min((t.pnl_r for t in trades), default=0.0)

    return TradeStatsResponse(
        total_trades=total,
        win_rate=win_rate,
        avg_r=avg_r,
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
