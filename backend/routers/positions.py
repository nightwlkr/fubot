"""Positions router — list, get, update SL/TP."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.position import Position
from backend.schemas.position import PositionResponse, PositionListResponse, PositionUpdateRequest

async def _mark_prices(symbols) -> dict:
    """Live mark prices for the given symbols (public endpoint — no API keys)."""
    from backend.services.binance_service import BinanceService
    prices: dict = {}
    binance = BinanceService()
    for s in set(symbols):
        try:
            prices[s] = float((await binance.get_ticker(s))["price"])
        except Exception:
            prices[s] = 0.0
    return prices


def _unrealized_pnl(side: str, entry: float, mark: float, quantity: float) -> float:
    if mark <= 0:
        return 0.0
    direction = 1.0 if side == "LONG" else -1.0
    return (mark - entry) * quantity * direction


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/positions", tags=["positions"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_position(pos: Position, mark_price: float = 0.0) -> PositionResponse:
    return PositionResponse(
        id=pos.id,
        signal_id=pos.signal_id,
        symbol=pos.symbol,
        side=pos.side,
        entry_price=float(pos.entry_price),
        quantity=float(pos.quantity),
        leverage=pos.leverage,
        stop_loss=float(pos.stop_loss) if pos.stop_loss is not None else None,
        take_profit=float(pos.take_profit) if pos.take_profit is not None else None,
        status=pos.status,
        opened_at=pos.opened_at,
        closed_at=pos.closed_at,
        mark_price=mark_price,
        unrealized_pnl=_unrealized_pnl(pos.side, float(pos.entry_price), mark_price,
                                       float(pos.quantity)),
    )


@router.get("/", response_model=PositionListResponse)
async def list_positions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
):
    user = await get_current_user_from_cookie(request, db)

    query = select(Position)

    if status:
        query = query.where(Position.status == status)
    if symbol:
        query = query.where(Position.symbol == symbol)

    query = query.order_by(Position.opened_at.desc())

    result = await db.execute(query)
    positions = result.scalars().all()
    prices = await _mark_prices([p.symbol for p in positions]) if positions else {}

    return PositionListResponse(
        positions=[_format_position(p, prices.get(p.symbol, 0.0)) for p in positions]
    )


@router.get("/{position_id}", response_model=PositionResponse)
async def get_position(
    position_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)
    result = await db.execute(select(Position).where(Position.id == position_id))
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return _format_position(position)


@router.patch("/{position_id}", response_model=PositionResponse)
async def update_position(
    position_id: int,
    body: PositionUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)
    result = await db.execute(select(Position).where(Position.id == position_id))
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "OPEN":
        raise HTTPException(status_code=400, detail="Position is not open")

    if body.stop_loss is not None:
        position.stop_loss = body.stop_loss
    if body.take_profit is not None:
        position.take_profit = body.take_profit

    await db.commit()
    await db.refresh(position)
    return _format_position(position)
