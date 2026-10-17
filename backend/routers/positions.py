"""Positions router — list, get, update SL/TP."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.position import Position
from backend.routers.position_pricing import (
    apply_pricing, fetch_pricing, get_engine_mode,
)
from backend.schemas.position import PositionResponse, PositionListResponse, PositionUpdateRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/positions", tags=["positions"])


async def _cancel_superseded_stops(binance, symbol: str, keep_algo_id,
                                    keep_trigger=None) -> None:
    """Cancel the symbol's other resting STOP_MARKET algos, keeping one.

    A manual stop change re-places the stop without removing the old one, which
    is exactly how stops came to stack (up to 7 on one position). Sweep the
    exchange rather than the orders table — the trail/manual paths never write
    their algoId back there.

    Keeps the new stop by algoId OR trigger price: the id from the place
    response has not always matched the id in the list response, and matching
    on id alone cancelled the NEW stop and kept the stale one (observed live).

    Skips the sweep entirely when neither is known: cancelling blind would
    remove the stop we just placed and leave the position naked.
    """
    keep_id = str(keep_algo_id) if keep_algo_id is not None else None
    keep_px = None
    if keep_trigger is not None:
        try:
            keep_px = float(keep_trigger)
        except (TypeError, ValueError):
            keep_px = None
    if keep_id is None and keep_px is None:
        logger.error(
            "[%s] Stop sweep skipped — Binance returned neither an algoId nor a "
            "trigger price for the new stop, so it cannot be distinguished from "
            "the superseded ones. Leaving all resting stops armed.", symbol)
        return
    px_tol = abs(keep_px) * 0.001 if keep_px else 0.0
    try:
        for algo in await binance.get_open_algo_orders(symbol):
            if str(algo.get("orderType")) != "STOP_MARKET":
                continue
            algo_id = algo.get("algoId")
            if algo_id is None:
                continue
            if keep_id is not None and str(algo_id) == keep_id:
                continue
            if keep_px is not None:
                trig = float(algo.get("triggerPrice") or 0)
                if trig > 0 and abs(trig - keep_px) <= px_tol:
                    continue
            try:
                await binance.cancel_algo_order(symbol, int(algo_id))
                logger.info("[%s] Cancelled superseded stop %s (%s) after a manual "
                            "stop change", symbol, algo_id, algo.get("triggerPrice"))
            except Exception as exc:
                logger.debug("[%s] Cancel superseded stop %s failed: %s",
                             symbol, algo_id, exc)
    except Exception:
        logger.exception("[%s] Stop sweep failed after a manual stop change", symbol)


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _format_position(pos: Position, pricing: dict) -> PositionResponse:
    px = apply_pricing(pos, pricing)
    return PositionResponse(
        id=pos.id,
        signal_id=pos.signal_id,
        symbol=pos.symbol,
        side=pos.side,
        entry_price=px["entry_price"],
        quantity=px["quantity"],
        leverage=px["leverage"],
        stop_loss=float(pos.stop_loss) if pos.stop_loss is not None else None,
        take_profit=float(pos.take_profit) if pos.take_profit is not None else None,
        status=pos.status,
        opened_at=pos.opened_at,
        closed_at=pos.closed_at,
        mark_price=px["mark_price"],
        unrealized_pnl=px["unrealized_pnl"],
        unrealized_pnl_pct=px["unrealized_pnl_pct"],
        liquidation_price=px["liquidation_price"],
        margin_usd=px["margin_usd"],
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
    pricing = await fetch_pricing(request, [p.symbol for p in positions]) if positions else {}

    return PositionListResponse(
        positions=[_format_position(p, pricing) for p in positions]
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
    pricing = await fetch_pricing(request, [position.symbol])
    return _format_position(position, pricing)


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

    # Side-aware validation: a LONG's stop must sit BELOW its target and the
    # target ABOVE the stop, mirrored for SHORT. Without this a LONG's
    # stop_loss could be set above its take_profit — an invalid state the same
    # class of bug evaluate_signal's gate 4b exists to reject.
    new_sl = body.stop_loss if body.stop_loss is not None else position.stop_loss
    new_tp = body.take_profit if body.take_profit is not None else position.take_profit
    if new_sl is not None and new_tp is not None:
        if position.side == "LONG" and float(new_sl) >= float(new_tp):
            raise HTTPException(
                status_code=400,
                detail=f"For a LONG the stop ({float(new_sl)}) must be below the "
                       f"target ({float(new_tp)}).")
        if position.side == "SHORT" and float(new_sl) <= float(new_tp):
            raise HTTPException(
                status_code=400,
                detail=f"For a SHORT the stop ({float(new_sl)}) must be above the "
                       f"target ({float(new_tp)}).")

    # LIVE MODE: the exchange holds a resting reduce-only algo SL/TP for this
    # position. Writing only the DB row left Binance's stop unchanged while the
    # dashboard showed the new one — the operator believed a position was
    # protected when it was not. That is the same naked-position failure the
    # engine already has machinery for, reintroduced through this route.
    # Place on the exchange FIRST, commit the row only on success (the mirror
    # of _trail_stop_to_be, which also places before cancelling).
    if get_engine_mode(request) == "live" and body.stop_loss is not None:
        engine = getattr(request.app.state, "bot_engine", None)
        binance = getattr(engine, "binance", None) if engine is not None else None
        if binance is not None:
            try:
                sl_res = await binance.modify_position_stop(
                    position.symbol, position.side, float(body.stop_loss),
                    float(position.quantity))
                new_algo_id = sl_res.get("orderId") if isinstance(sl_res, dict) else None
            except Exception as exc:
                logger.error(
                    "[%s] Failed to place the new stop on the exchange (%s) — "
                    "DB not updated, the exchange keeps its existing stop",
                    position.symbol, exc)
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not place the stop on Binance ({exc}). "
                           f"The database was NOT changed.")
            # Cancel any superseded resting stop, keeping the one just placed.
            await _cancel_superseded_stops(binance, position.symbol, new_algo_id,
                                        keep_trigger=float(body.stop_loss))

    if body.stop_loss is not None:
        position.stop_loss = body.stop_loss
    if body.take_profit is not None:
        position.take_profit = body.take_profit

    await db.commit()
    await db.refresh(position)
    pricing = await fetch_pricing(request, [position.symbol])
    return _format_position(position, pricing)
