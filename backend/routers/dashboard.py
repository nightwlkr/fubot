"""Dashboard router — overview and today stats."""
import logging
from datetime import datetime, date, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.daily_summary import DailySummary
from backend.models.position import Position
from backend.models.trade import Trade
from backend.models.equity_log import EquityLog
from backend.routers.position_pricing import apply_pricing, fetch_pricing
from backend.schemas.dashboard import (
    BotStatus,
    DailyLimitStatus,
    TodayStats,
    PositionItem,
    EquityPoint,
    DashboardOverview,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_bot_status(is_running: bool, mode: str, uptime: float, scan_count: int, last_scan: Optional[datetime]):
    return BotStatus(
        is_running=is_running,
        mode=mode,
        uptime_seconds=uptime,
    )


def _format_position(pos: Position, pricing: dict) -> PositionItem:
    px = apply_pricing(pos, pricing)
    return PositionItem(
        id=pos.id,
        symbol=pos.symbol,
        side=pos.side,
        entry_price=px["entry_price"],
        quantity=px["quantity"],
        leverage=px["leverage"],
        stop_loss=float(pos.stop_loss) if pos.stop_loss is not None else None,
        take_profit=float(pos.take_profit) if pos.take_profit is not None else None,
        status=pos.status,
        opened_at=pos.opened_at,
        mark_price=px["mark_price"],
        unrealized_pnl=px["unrealized_pnl"],
        unrealized_pnl_pct=px["unrealized_pnl_pct"],
        liquidation_price=px["liquidation_price"],
        margin_usd=px["margin_usd"],
    )


@router.get("/overview")
async def dashboard_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    # Bot status — from the real engine (app.state.bot_engine, wired in main.init_bot)
    from backend.main import app
    engine = getattr(app.state, "bot_engine", None)
    if engine is not None:
        engine_status = await engine.get_status()
    else:
        engine_status = {"status": "stopped", "mode": "paper", "uptime_seconds": 0.0,
                         "scan_count": 0, "last_scan": None}

    # Open positions (+ live pricing for the table: exchange truth in live
    # mode, mark price in paper mode)
    result = await db.execute(select(Position).where(Position.status == "OPEN"))
    positions = result.scalars().all()
    pricing = await fetch_pricing(request, [p.symbol for p in positions]) if positions else {}

    # Equity curve for the Overview card, which is labelled "7D".
    #
    # This used to take the newest 30 ROWS of equity_log regardless of age.
    # Snapshots are written every 5 scans (~2.5 min at a 30s interval), so 30
    # rows is about 75 MINUTES — a card labelled "7D" was showing barely an
    # hour. Filter by time to match the label, and order ASCENDING so the
    # chart's series is in the order lightweight-charts requires (it throws
    # 'Value is null' on out-of-order time) instead of relying on the frontend
    # to re-sort a descending payload.
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    eq_result = await db.execute(
        select(EquityLog)
        .where(EquityLog.recorded_at >= cutoff)
        .order_by(EquityLog.recorded_at)
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
            status=engine_status["status"],
            is_running=engine_status["status"] == "running",
            mode=engine_status["mode"],
            uptime_seconds=engine_status["uptime_seconds"],
            scan_count=engine_status["scan_count"],
            # engine.get_status() already returns last_scan as an ISO string
            last_scan=engine_status.get("last_scan"),
        ),
        today_stats=TodayStats(
            realized_pnl_usdt=float(daily.total_pnl_usdt) if daily else 0.0,
            unrealized_pnl_usdt=0.0,
            win_rate=win_rate,
            trades_today=daily.total_trades if daily else 0,
            pnl_r=float(daily.total_pnl_r) if daily else 0.0,
            pnl_usdt=float(daily.total_pnl_usdt) if daily else 0.0,
            wins=daily.wins if daily else 0,
            losses=daily.losses if daily else 0,
            trades_count=daily.total_trades if daily else 0,
        ),
        open_positions=[_format_position(p, pricing) for p in positions],
        equity_curve=[
            EquityPoint(date=r.recorded_at.isoformat(),
                        balance=float(r.balance_usdt),
                        balance_usdt=float(r.balance_usdt))
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
        pnl_r=float(daily.total_pnl_r) if daily else 0.0,
        pnl_usdt=float(daily.total_pnl_usdt) if daily else 0.0,
        wins=daily.wins if daily else 0,
        losses=daily.losses if daily else 0,
        trades_count=daily.total_trades if daily else 0,
        daily_limit_status=DailyLimitStatus(
            used_r=used_r,
            limit_r=limit_r,
            remaining_r=max(0.0, limit_r - used_r),
        ),
    )
