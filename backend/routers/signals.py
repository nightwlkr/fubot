"""Signal router — list, active, missed signals."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.signal import Signal
from backend.schemas.signal import SignalResponse, SignalListResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/signals", tags=["signals"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = request.cookies.get("fubot_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_cookie(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_signal(s: Signal) -> SignalResponse:
    return SignalResponse(
        id=s.id,
        symbol=s.symbol,
        strategy_type=s.strategy_type,
        direction=s.direction,
        entry_zone_min=float(s.entry_zone_min),
        entry_zone_max=float(s.entry_zone_max),
        stop_loss=float(s.stop_loss),
        take_profit=float(s.take_profit),
        rr_ratio=float(s.rr_ratio),
        volume_confirmed=s.volume_confirmed,
        status=s.status,
        created_at=s.created_at,
        skip_reason=s.skip_reason,
    )


@router.get("/", response_model=SignalListResponse)
async def list_signals(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    user = await get_current_user_from_cookie(request, db)

    query = select(Signal)

    if status:
        query = query.where(Signal.status == status)
    if symbol:
        query = query.where(Signal.symbol == symbol)
    if strategy:
        query = query.where(Signal.strategy_type == strategy)

    query = query.order_by(Signal.created_at.desc()).limit(limit)

    result = await db.execute(query)
    signals = result.scalars().all()

    return SignalListResponse(
        signals=[_format_signal(s) for s in signals]
    )


@router.get("/active", response_model=SignalListResponse)
async def active_signals(
    request: Request,
    db: AsyncSession = Depends(get_db),
    symbol: Optional[str] = Query(None),
):
    user = await get_current_user_from_cookie(request, db)

    query = select(Signal).where(Signal.status == "PENDING")

    if symbol:
        query = query.where(Signal.symbol == symbol)

    query = query.order_by(Signal.created_at.desc()).limit(100)

    result = await db.execute(query)
    signals = result.scalars().all()

    return SignalListResponse(
        signals=[_format_signal(s) for s in signals]
    )


@router.get("/missed", response_model=SignalListResponse)
async def missed_signals(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
):
    user = await get_current_user_from_cookie(request, db)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = (
        select(Signal)
        .where(Signal.status == "SKIPPED")
        .where(Signal.created_at >= cutoff)
        .order_by(Signal.created_at.desc())
    )

    result = await db.execute(query)
    signals = result.scalars().all()

    return SignalListResponse(
        signals=[_format_signal(s) for s in signals]
    )
