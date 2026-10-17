"""Orders router — list, cancel."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.order import Order
from backend.schemas.order import OrderResponse, OrderListResponse, OrderCancelResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_order(o: Order) -> OrderResponse:
    return OrderResponse(
        id=o.id,
        binance_order_id=o.binance_order_id,
        symbol=o.symbol,
        side=o.side,
        order_type=o.order_type,
        price=float(o.price) if o.price is not None else None,
        quantity=float(o.quantity),
        filled_qty=float(o.filled_qty),
        status=o.status,
        created_at=o.created_at,
        updated_at=o.updated_at,
    )


# Registered TWICE, deliberately.
#
# Starlette's `redirect_slashes` answers GET /api/orders (no trailing slash) with a
# 307 to /api/orders/ — and that redirect DISCARDS THE QUERY STRING. axios follows it
# transparently, so `?side=SHORT` became `/api/orders/` and the client got EVERY row
# back with a 200 and no error: the filters silently did nothing.
#
# Registering the empty-path variant means there is nothing to redirect, so the
# filters survive no matter how the client spells the URL.
@router.get("", include_in_schema=False)
@router.get("/", response_model=OrderListResponse)
async def list_orders(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    user = await get_current_user_from_cookie(request, db)

    query = select(Order)

    if status:
        query = query.where(Order.status == status)
    if symbol:
        query = query.where(Order.symbol == symbol)

    query = query.order_by(Order.created_at.desc()).limit(limit)

    result = await db.execute(query)
    orders = result.scalars().all()

    return OrderListResponse(
        orders=[_format_order(o) for o in orders]
    )


@router.delete("/{order_id}", response_model=OrderCancelResponse)
async def cancel_order(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in ("FILLED", "CANCELLED", "REJECTED"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel order in status '{order.status}'")

    # Note: Actual Binance cancellation happens via OrderService (Phase 3).
    # For now, mark as CANCELLED in DB if no Binance integration yet.
    order.status = "CANCELLED"
    await db.commit()
    await db.refresh(order)

    return OrderCancelResponse(ok=True)
