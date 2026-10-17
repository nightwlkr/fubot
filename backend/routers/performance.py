"""Performance router — equity curve, monthly returns, strategy breakdown."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

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
    StrategyBreakdown,
    StrategyBreakdownResponse,
    RBucket,
    StrategyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["performance"])


def _mode_filter(query, mode: Optional[str]):
    """Restrict a query to live or paper trades.

    `Trade.mode` exists precisely so paper and live results never mix (added
    with migration 009), but nothing read it — so every statistic on the
    Performance page summed paper fills into live performance with no
    indication. Default is live-only: paper results are not real money and must
    never contaminate a live P&L number. Pass `mode='paper'` or `'all'` to see
    the others.
    """
    if mode == "all":
        return query
    return query.where(Trade.mode == (mode or "live"))


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
    mode: Optional[str] = Query("live", pattern="^(live|paper|all)$"),
):
    """Cumulative trading P&L (R and USD) from closed trades, in close order.

    Unlike the equity curve (total account value, moves with collateral prices),
    this shows ONLY realized PnL from the strategy — the honest performance line.
    Returns ascending (oldest first) for charting.

    Live-only by default so paper fills cannot contaminate real-money P&L.
    """
    user = await get_current_user_from_cookie(request, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        _mode_filter(
            select(Trade)
            .where(Trade.closed_at >= cutoff),
            mode,
        ).order_by(Trade.closed_at)
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
    mode: Optional[str] = Query("live", pattern="^(live|paper|all)$"),
):
    """Per-month R, trade count and win rate.

    Aggregated from `trades`, NOT `daily_summary`. The two disagreed: this
    endpoint read daily_summary while /pnl read trades, and nothing backfills
    daily_summary from trades (the exchange-sync recompute only covers the last
    48h). So Monthly Returns could show nothing while the PnL chart next to it
    was full, and the two would quietly disagree on the totals. trades is the
    source of truth for per-trade P&L.
    """
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(_mode_filter(select(Trade), mode))
    trades = result.scalars().all()

    monthly: dict[str, dict] = {}
    for t in trades:
        if t.closed_at is None:
            continue
        # closed_at is timestamptz-aware; use its own date so a trade closed at
        # 23:30 UTC lands in the month a human would expect.
        month_key = t.closed_at.strftime("%Y-%m")
        m = monthly.setdefault(month_key, {
            "pnl_r": 0.0, "trades": 0, "wins": 0, "losses": 0,
        })
        m["pnl_r"] += float(t.pnl_r or 0)
        m["trades"] += 1
        if float(t.pnl_r or 0) > 0:
            m["wins"] += 1
        elif float(t.pnl_r or 0) < 0:
            m["losses"] += 1

    return MonthlyResponse(data=[
        MonthlyReturn(
            month=key,
            pnl_r=round(monthly[key]["pnl_r"], 4),
            trades=monthly[key]["trades"],
            win_rate=(monthly[key]["wins"] / monthly[key]["trades"] * 100)
            if monthly[key]["trades"] > 0 else 0.0,
        )
        for key in sorted(monthly.keys())
    ])


@router.get("/strategies", response_model=StrategyResponse)
async def strategy_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: Optional[int] = Query(None, ge=1, le=730),
    mode: Optional[str] = Query("live", pattern="^(live|paper|all)$"),
):
    """R:R distribution of closed trades — feeds the Performance histogram.

    Buckets match the frontend labels (PHASE5_STEP8); ordered from worst to best.

    `days` was missing, so this chart ignored the 7d/30d/90d/365d range buttons
    and always showed all-time while the charts beside it respected the range.
    Omitted (None) it stays all-time, which is the historical behaviour.
    """
    user = await get_current_user_from_cookie(request, db)

    query = _mode_filter(select(Trade), mode)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(Trade.closed_at >= cutoff)
    result = await db.execute(query)
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


@router.get("/strategies/breakdown", response_model=StrategyBreakdownResponse)
async def strategy_breakdown(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: Optional[int] = Query(None, ge=1, le=730),
    mode: Optional[str] = Query("live", pattern="^(live|paper|all)$"),
):
    """Per-strategy realised performance — which strategy actually makes money.

    `/strategies` (above) buckets every trade by R for the histogram and
    deliberately carries no per-strategy data, so there was no way to attribute
    the P&L to a strategy. That mattered: the live book reported +$17.02 and
    -10.3R simultaneously, and only a per-strategy split shows why (at
    risk_percent=5 the notional cap bound on tight stops, so winners carried
    1.70x the risk of losers). 3-candle is the best performer in dollars and
    break-even in R — the opposite of what the blended R column suggested.

    Aggregated in Python rather than SQL so the R and USD totals, the win/loss
    counts and the profit factor all come from ONE pass over the same rows —
    a per-strategy GROUP BY with conditional aggregates is easy to get subtly
    inconsistent, and these numbers drive a decision about which strategies
    to keep trading.

    `profit_factor` is None when a strategy has no losing trade: gross win /
    gross loss is undefined there, and 0.0 would rank a flawless strategy last.

    Ordered worst avg_r first so the strategies needing attention lead.
    """
    user = await get_current_user_from_cookie(request, db)

    query = _mode_filter(select(Trade), mode)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(Trade.closed_at >= cutoff)
    result = await db.execute(query)

    buckets: dict[str, dict] = {}
    for t in result.scalars().all():
        name = t.strategy_type or "UNKNOWN"
        # Both columns are Numeric -> Decimal; coerce before any arithmetic.
        r = float(t.pnl_r or 0)
        usd = float(t.pnl_usdt or 0)
        b = buckets.setdefault(name, {
            "n": 0, "wc": 0, "lc": 0, "be": 0,
            "r": 0.0, "usd": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
        })
        b["n"] += 1
        b["r"] += r
        b["usd"] += usd
        if r > 0:
            b["wc"] += 1
            b["gross_win"] += usd
        elif r < 0:
            b["lc"] += 1
            b["gross_loss"] += usd
        else:
            b["be"] += 1

    data: list[StrategyBreakdown] = []
    for name, b in buckets.items():
        n = b["n"]
        data.append(StrategyBreakdown(
            strategy=name,
            total_trades=n,
            wins=b["wc"],
            losses=b["lc"],
            breakevens=b["be"],
            win_rate=round(b["wc"] / n * 100, 2) if n else 0.0,
            sum_r=round(b["r"], 4),
            avg_r=round(b["r"] / n, 4) if n else 0.0,
            profit_factor=(round(b["gross_win"] / abs(b["gross_loss"]), 4)
                           if b["gross_loss"] else None),
            total_pnl_usdt=round(b["usd"], 2),
        ))
    # Worst expectancy first — the strategies that need attention lead the table.
    data.sort(key=lambda d: d.avg_r)
    return StrategyBreakdownResponse(data=data)
