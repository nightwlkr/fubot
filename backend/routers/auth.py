"""Auth router — login, logout, me endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.auth import LoginRequest, LoginResponse
from backend.services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    credentials: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        user = await auth_service.authenticate(db, credentials.username, credentials.password, ip)
    except auth_service.RateLimitExceeded as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = auth_service.create_access_token(user)
    response.set_cookie(
        key="fubot_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400,
    )
    return LoginResponse(
        token=token,
        user=auth_service.UserResponse(id=user.id, username=user.username, role=user.role),
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="fubot_token", path="/")
    return {"ok": True}


@router.get("/me")
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get("fubot_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user = await auth_service.get_current_user_from_cookie(token, db)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return auth_service.UserResponse(id=user.id, username=user.username, role=user.role)
