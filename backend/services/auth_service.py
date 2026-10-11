"""Authentication service with JWT, bcrypt, and rate limiting."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.user import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory rate limiting: ip -> list of failed attempt timestamps
_failed_attempts: dict[str, list[datetime]] = {}
RATE_LIMIT_WINDOW = timedelta(minutes=15)
RATE_LIMIT_MAX = 5


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plaintext: str, hashed: str) -> bool:
    return pwd_context.verify(plaintext, hashed)


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.FUBOT_SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.FUBOT_SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        return None


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def authenticate(
    db: AsyncSession, username: str, password: str, ip: str
) -> Optional[User]:
    """
    Authenticate user. Returns User if valid, None otherwise.
    Implements IP-based rate limiting (5 failed attempts per 15 min).
    """
    # Check rate limit
    now = datetime.now(timezone.utc)
    if ip in _failed_attempts:
        # Clean old attempts
        _failed_attempts[ip] = [
            t for t in _failed_attempts[ip]
            if now - t < RATE_LIMIT_WINDOW
        ]
        if len(_failed_attempts[ip]) >= RATE_LIMIT_MAX:
            remaining = RATE_LIMIT_WINDOW - (now - _failed_attempts[ip][0])
            raise RateLimitExceeded(f"Too many attempts, try again in {remaining.seconds // 60 + 1} minutes")

    user = await get_user_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash):
        # Increment failed attempt
        _failed_attempts.setdefault(ip, []).append(now)
        logger.warning(f"Failed login attempt for user='{username}' from ip='{ip}' (masked key: {settings.FUBOT_SECRET_KEY[:4]}****)")
        return None

    # Success — clear failed attempts
    _failed_attempts.pop(ip, None)
    return user


class RateLimitExceeded(Exception):
    pass


def get_token_from_request(request) -> str | None:
    """Extract JWT token from cookie or Authorization Bearer header."""
    token = request.cookies.get("fubot_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    return token


async def get_current_user_from_request(request, db: AsyncSession) -> User:
    token = get_token_from_request(request)
    if not token:
        raise ValueError("Missing token")
    return await get_current_user_from_token(token, db)


async def get_current_user_from_token(
    token: str, db: AsyncSession
) -> User:
    """Decode JWT and return User. Raises ValueError if invalid."""
    payload = decode_access_token(token)
    if payload is None:
        raise ValueError("Invalid token")
    user_id = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("User not found")
    return user


async def get_current_user_from_cookie(
    token: str, db: AsyncSession
) -> User:
    """Decode JWT from cookie and return User. Raises 401 if invalid."""
    return await get_current_user_from_token(token, db)
