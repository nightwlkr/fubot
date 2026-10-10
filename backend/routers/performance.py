"""Performance router — equity curve, monthly returns, strategy breakdown."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.equity_log import EquityLog
from backend.models.daily_summary import DailySummary
from backend.models.trade import Trade
from backend.schemas.performance import (
    EquityPoint,
    EquityResponse,
    MonthlyReturn,
    MonthlyResponse,
    StrategyPerformance,
    StrategyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["performance"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = request.cookies.get("fubot_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_cookie(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@router.get("/equity", response_model=EquityResponse)
async def equity_curve(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(365, ge=1, le=730),
):
    user = await get_current_user_from_cookie(request, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(EquityLog)
        .where(EquityLog.recorded_at >= cutoff)
        .order_by(EquityLog.recorded_at)
    )
    rows = result.scalars().all()

    return EquityResponse(
        data=[
            EquityPoint(
                date=r.recorded_at.isoformat(),
                balance=float(r.balance_usdt),
            )
            for r in rows
        ]
    )


@router.get("/monthly", response_model=MonthlyResponse)
async def monthly_returns(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(
        select(DailySummary)
        .order_by(DailySummary.date)
    )
    summaries = result.scalars().all()

    # Group by month
    monthly: dict[str, dict] = {}
    for s in summaries:
        month_key = s.date.strftime("%Y-%m")
        if month_key not in monthly:
            monthly[month_key] = {
                "month": month_key,
                "pnl_r": 0.0,
                "pnl_usdt": 0.0,
                "trades": 0,
                "wins": 0,
            }
        monthly[month_key]["pnl_r"] += float(s.total_pnl_r)
        monthly[month_key]["pnl_usdt"] += float(s.total_pnl_usdt)
        monthly[month_key]["trades"] += s.total_trades
        monthly[month_key]["wins"] += s.wins

    data = []
    for month_key in sorted(monthly.keys()):
        m = monthly[month_key]
        win_rate = (m["wins"] / m["trades"] * 100) if m["trades"] > 0 else 0.0
        data.append(MonthlyReturn(
            month=m["month"],
            pnl_r=float(m["pnl_r"]),
            trades=m["trades"],
            win_rate=win_rate,
        ))

    return MonthlyResponse(data=data)


@router.get("/strategies", response_model=StrategyResponse)
async def strategy_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(select(Trade))
    trades = result.scalars().all()

    by_strategy: dict[str, list[Trade]] = {}
    for t in trades:
        strat = t.strategy_type
        if strat not in by_strategy:
            by_strategy[strat] = []
        by_strategy[strat].append(t)

    data = []
    for strat, strat_trades in sorted(by_strategy.items()):
        total = len(strat_trades)
        wins = sum(1 for t in strat_trades if t.pnl_r > 0)
        win_rate = (wins / total * 100) if total > 0 else 0.0
        avg_r = sum(t.pnl_r for t in strat_trades) / total if total > 0 else 0.0
        data.append(StrategyPerformance(
            strategy=strat,
            trades=total,
            win_rate=win_rate,
            avg_r=avg_r,
        ))

    return StrategyResponse(data=data)
