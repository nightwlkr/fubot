"""Bot control router — start, stop, status endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.bot import BotStartRequest, BotStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot", tags=["bot"])

# Bot engine reference (set by main.py lifespan)
_bot_engine = None


def set_bot_engine(engine):
    global _bot_engine
    _bot_engine = engine


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    """Extract user from JWT cookie. Replicates auth pattern."""
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user = await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


@router.post("/start")
async def start_bot(
    request: Request,
    body: BotStartRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    global _bot_engine
    if _bot_engine is None:
        raise HTTPException(status_code=503, detail="Bot engine not initialized")
    user = await get_current_user_from_cookie(request, db)
    if body.mode is not None:
        mode = body.mode.lower()
    else:
        # Use the saved mode from settings (survives restarts)
        from backend.models.setting import Setting
        from sqlalchemy import select
        result = await db.execute(select(Setting).where(Setting.key == "bot_mode"))
        saved = result.scalar_one_or_none()
        mode = (saved.value if saved and saved.value in ("paper", "live") else "paper")
    if mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="Mode must be 'paper' or 'live'")
    await _bot_engine.start(mode=mode)
    # Persist the mode so a restart doesn't revert to paper
    from backend.models.setting import Setting
    from sqlalchemy import select
    result = await db.execute(select(Setting).where(Setting.key == "bot_mode"))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = mode
    else:
        db.add(Setting(key="bot_mode", value=mode))
    await db.commit()
    logger.info(f"Bot started in {mode} mode by user '{user.username}'")
    return {"ok": True, "status": "running", "mode": mode}


@router.post("/stop")
async def stop_bot(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    global _bot_engine
    if _bot_engine is None:
        raise HTTPException(status_code=503, detail="Bot engine not initialized")
    user = await get_current_user_from_cookie(request, db)
    await _bot_engine.stop()
    logger.info(f"Bot stopped by user '{user.username}'")
    return {"ok": True, "status": "stopped"}


@router.post("/close-all")
async def close_all_positions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Close ALL open positions at market price (manual stop / kill switch).

    In live mode this places real MARKET orders on the exchange. Useful when
    stopping the bot — without this, stopping leaves positions open with no
    TP/SL management (dangerous in live mode).
    """
    global _bot_engine
    if _bot_engine is None:
        raise HTTPException(status_code=503, detail="Bot engine not initialized")
    user = await get_current_user_from_cookie(request, db)
    pm = getattr(_bot_engine, "position_manager", None)
    if pm is None:
        raise HTTPException(status_code=503, detail="Position manager not initialized")
    closed = await pm.close_all_positions(reason="MANUAL_CLOSE")
    logger.info(f"Closed {closed} position(s) by user '{user.username}'")
    return {"ok": True, "closed": closed}


@router.post("/close/{position_id}")
async def close_position(
    position_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Close a SINGLE position at market price.

    In live mode this places a real MARKET order on the exchange and cancels
    the SL/TP. Use this for manual intervention on a specific position.
    """
    global _bot_engine
    if _bot_engine is None:
        raise HTTPException(status_code=503, detail="Bot engine not initialized")
    user = await get_current_user_from_cookie(request, db)
    pm = getattr(_bot_engine, "position_manager", None)
    if pm is None:
        raise HTTPException(status_code=503, detail="Position manager not initialized")
    
    success = await pm.close_position(position_id, reason="MANUAL_CLOSE")
    if success:
        logger.info(f"Closed position {position_id} by user '{user.username}'")
        return {"ok": True, "position_id": position_id}
    else:
        raise HTTPException(status_code=404, detail="Position not found or already closed")


@router.get("/status")
async def get_bot_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    global _bot_engine
    if _bot_engine is None:
        return BotStatusResponse(
            status="stopped",
            mode="paper",
            uptime_seconds=0,
            scan_count=0,
            last_scan=None,
        )
    user = await get_current_user_from_cookie(request, db)
    status_dict = await _bot_engine.get_status()
    return BotStatusResponse(**status_dict)
