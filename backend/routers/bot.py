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
    token = request.cookies.get("fubot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user = await auth_service.get_current_user_from_cookie(token, db)
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
    mode = body.mode.lower()
    if mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="Mode must be 'paper' or 'live'")
    await _bot_engine.start(mode=mode)
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
