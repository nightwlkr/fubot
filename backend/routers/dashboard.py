"""Dashboard router — overview and today stats."""
import logging
from datetime import datetime, date, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.daily_summary import DailySummary
from backend.models.position import Position
from backend.models.trade import Trade
from backend.models.equity_log import EquityLog
from backend.schemas.dashboard import (
    BotStatus,
    TodayStats,
    PositionItem,
    EquityPoint,
    DashboardOverview,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = request.cookies.get("fubot_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_cookie(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_bot_status(is_running: bool, mode: str, uptime: float, scan_count: int, last_scan: Optional[datetime]):
    return BotStatus(
        is_running=is_running,
        mode=mode,
        uptime_seconds=uptime,
    )


def _format_position(pos: Position) -> PositionItem:
    return PositionItem(
        id=pos.id,
        symbol=pos.symbol,
        side=pos.side,
        entry_price=float(pos.entry_price),
        quantity=float(pos.quantity),
        leverage=pos.leverage,
        stop_loss=float(pos.stop_loss) if pos.stop_loss is not None else None,
        take_profit=float(pos.take_profit) if pos.take_profit is not None else None,
        status=pos.status,
        opened_at=pos.opened_at,
    )


@router.get("/overview")
async def dashboard_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    # Bot status (from app state or default)
    from backend.main import app
    bot_running = getattr(app.state, "bot_running", False)
    bot_mode = getattr(app.state, "bot_mode", "paper")
    bot_scan_count = getattr(app.state, "bot_scan_count", 0)
    bot_last_scan = getattr(app.state, "bot_last_scan", None)

    # Open positions
    result = await db.execute(select(Position).where(Position.status == "OPEN"))
    positions = result.scalars().all()

    # Equity curve (last 30 days)
    cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)
    eq_result = await db.execute(
        select(EquityLog)
        .where(EquityLog.recorded_at >= cutoff)
        .order_by(EquityLog.recorded_at.desc())
        .limit(30)
    )
    equity_rows = eq_result.scalars().all()

    # Today's stats
    today = date.today()
    daily_result = await db.execute(select(DailySummary).where(DailySummary.date == today))
    daily = daily_result.scalar_one_or_none()

    # Total trades, best/worst
    trade_result = await db.execute(select(Trade))
    trades = trade_result.scalars().all()
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_r > 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    best_trade = max((t.pnl_r for t in trades), default=0.0)
    worst_trade = min((t.pnl_r for t in trades), default=0.0)

    return DashboardOverview(
        bot_status=BotStatus(
            is_running=bot_running,
            mode=bot_mode,
            uptime_seconds=getattr(app.state, "bot_uptime_seconds", 0.0),
        ),
        today_stats=TodayStats(
            realized_pnl_usdt=float(daily.total_pnl_usdt) if daily else 0.0,
            unrealized_pnl_usdt=0.0,
            win_rate=win_rate,
            trades_today=daily.total_trades if daily else 0,
        ),
        open_positions=[_format_position(p) for p in positions],
        equity_curve=[
            EquityPoint(date=r.recorded_at.isoformat(), balance_usdt=float(r.balance_usdt))
            for r in equity_rows
        ],
        total_trades=total,
        best_trade_r=best_trade,
        worst_trade_r=worst_trade,
    )


@router.get("/today")
async def today_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)
    today = date.today()

    result = await db.execute(select(DailySummary).where(DailySummary.date == today))
    daily = result.scalar_one_or_none()

    # Calculate daily limit status — read from settings table
    from backend.models.setting import Setting
    limit_r = 3.0
    settings_result = await db.execute(select(Setting).where(Setting.key == "daily_loss_limit_r"))
    setting = settings_result.scalar_one_or_none()
    if setting and isinstance(setting.value, dict):
        limit_r = abs(float(setting.value.get("value", -3.0)))
    elif setting and isinstance(setting.value, (int, float)):
        limit_r = abs(float(setting.value))

    # Only losses count toward the limit (a winning day must not trip -3R)
    used_r = max(0.0, -float(daily.total_pnl_r)) if daily else 0.0

    return TodayStats(
        realized_pnl_usdt=float(daily.total_pnl_usdt) if daily else 0.0,
        unrealized_pnl_usdt=0.0,
        win_rate=0.0,
        trades_today=daily.total_trades if daily else 0,
        wins=daily.wins if daily else 0,
        losses=daily.losses if daily else 0,
        daily_limit_status={
            "used_r": used_r,
            "limit_r": limit_r,
            "remaining_r": max(0.0, limit_r - used_r),
        },
    )
