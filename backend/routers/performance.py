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
    PnLPoint,
    PnLResponse,
    MonthlyReturn,
    MonthlyResponse,
    RBucket,
    StrategyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["performance"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
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


@router.get("/pnl", response_model=PnLResponse)
async def pnl_curve(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(365, ge=1, le=730),
):
    """Cumulative trading P&L (R and USD) from closed trades, in close order.

    Unlike the equity curve (total account value, moves with collateral prices),
    this shows ONLY realized PnL from the strategy — the honest performance line.
    Returns ascending (oldest first) for charting.
    """
    user = await get_current_user_from_cookie(request, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Trade)
        .where(Trade.closed_at >= cutoff)
        .order_by(Trade.closed_at)
    )
    rows = result.scalars().all()

    cum_r = 0.0
    cum_usd = 0.0
    points: list[PnLPoint] = []
    for t in rows:
        cum_r += float(t.pnl_r or 0)
        cum_usd += float(t.pnl_usdt or 0)
        points.append(PnLPoint(
            date=t.closed_at.isoformat(),
            pnl_r=round(cum_r, 4),
            pnl_usdt=round(cum_usd, 4),
        ))

    return PnLResponse(data=points)


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
    """R:R distribution of closed trades — feeds the Performance histogram.

    Buckets match the frontend labels (PHASE5_STEP8); ordered from worst to best.
    """
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(select(Trade))
    trades = [float(t.pnl_r) for t in result.scalars().all()]

    edges = [
        ("-3R or worse", lambda r: r <= -3.0),
        ("-3 to -2R", lambda r: -3.0 < r <= -2.0),
        ("-2 to -1R", lambda r: -2.0 < r <= -1.0),
        ("-1 to 0R", lambda r: -1.0 < r < 0.0),
        ("0 to 0.5R", lambda r: 0.0 <= r < 0.5),
        ("0.5 to 1R", lambda r: 0.5 <= r < 1.0),
        ("1 to 2R", lambda r: 1.0 <= r < 2.0),
        ("2 to 3R", lambda r: 2.0 <= r < 3.0),
        ("3R+", lambda r: r >= 3.0),
    ]
    data = [RBucket(bucket=label, count=sum(1 for r in trades if match(r)))
            for label, match in edges]
    return StrategyResponse(data=data)
