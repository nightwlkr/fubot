"""Settings router — trading params, symbol config, API key."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.setting import Setting
from backend.models.symbol import Symbol
from backend.schemas.settings import (
    TradingSettingsResponse,
    TradingSettingsUpdate,
    SymbolConfigResponse,
    SymbolListResponse,
    SymbolUpdateRequest,
    APIKeyStatusResponse,
    APIKeySaveRequest,
)
from backend.encryption import encrypt, decrypt, mask_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


async def get_current_user_from_cookie(request: Request, db: AsyncSession):
    from backend.services import auth_service
    token = auth_service.get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_service.get_current_user_from_token(token, db)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def _get_setting(db: AsyncSession, key: str):
    result = await db.execute(select(Setting).where(Setting.key == key))
    return result.scalar_one_or_none()


async def _get_or_create_setting(db: AsyncSession, key: str, value) -> Setting:
    existing = await _get_setting(db, key)
    if existing:
        existing.value = value
        await db.commit()
        await db.refresh(existing)
        return existing
    new_setting = Setting(key=key, value=value)
    db.add(new_setting)
    await db.commit()
    await db.refresh(new_setting)
    return new_setting


@router.get("/trading", response_model=TradingSettingsResponse)
async def get_trading_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    keys = [
        "risk_percent", "max_positions", "daily_loss_limit_r", "rr_min",
        "check_interval_seconds", "leverage_btc", "leverage_alts",
        "strategy_fvg_enabled", "strategy_3candle_enabled", "strategy_choch_enabled",
        "paper_use_real_balance", "paper_balance_usdt",
        "trade_neutral_markets", "trade_min_trend_strength", "volume_filter_enabled",
        "symbol_cooldown_minutes", "max_daily_trades",
    ]
    defaults = {
        "risk_percent": 1.0,
        "max_positions": 3,
        "daily_loss_limit_r": -3.0,
        "rr_min": 3.0,
        "check_interval_seconds": 60,
        "leverage_btc": 10,
        "leverage_alts": 5,
        "strategy_fvg_enabled": True,
        "strategy_3candle_enabled": True,
        "strategy_choch_enabled": True,
        "paper_use_real_balance": True,
        "paper_balance_usdt": 10000.0,
        "trade_neutral_markets": True,
        "trade_min_trend_strength": "MODERATE",
        "volume_filter_enabled": False,
        "symbol_cooldown_minutes": 240,
        "max_daily_trades": 12,
    }

    settings = {}
    for key in keys:
        s = await _get_setting(db, key)
        if s and isinstance(s.value, (int, float, bool, str)):
            settings[key] = s.value
        else:
            settings[key] = defaults.get(key)

    return TradingSettingsResponse(
        risk_percent=float(settings.get("risk_percent", 1.0)),
        max_positions=int(settings.get("max_positions", 3)),
        daily_loss_limit_r=float(settings.get("daily_loss_limit_r", -3.0)),
        rr_min=float(settings.get("rr_min", 3.0)),
        check_interval_seconds=int(settings.get("check_interval_seconds", 60)),
        leverage_btc=int(settings.get("leverage_btc", 10)),
        leverage_alts=int(settings.get("leverage_alts", 5)),
        strategy_fvg_enabled=bool(settings.get("strategy_fvg_enabled", True)),
        strategy_3candle_enabled=bool(settings.get("strategy_3candle_enabled", True)),
        strategy_choch_enabled=bool(settings.get("strategy_choch_enabled", True)),
        paper_use_real_balance=bool(settings.get("paper_use_real_balance", True)),
        paper_balance_usdt=float(settings.get("paper_balance_usdt", 10000.0)),
        trade_neutral_markets=bool(settings.get("trade_neutral_markets", True)),
        trade_min_trend_strength=str(settings.get("trade_min_trend_strength", "MODERATE")),
        symbol_cooldown_minutes=int(settings.get("symbol_cooldown_minutes", 240)),
        max_daily_trades=int(settings.get("max_daily_trades", 12)),
        volume_filter_enabled=bool(settings.get("volume_filter_enabled", False)),
    )


@router.put("/trading", response_model=TradingSettingsResponse)
async def update_trading_settings(
    body: TradingSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    updates = body.model_dump(exclude_unset=True)

    for key, value in updates.items():
        await _get_or_create_setting(db, key, value)

    # Return updated settings
    return await get_trading_settings(request, db)


@router.get("/symbols", response_model=SymbolListResponse)
async def get_symbol_configs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(select(Symbol).order_by(Symbol.symbol))
    symbols = result.scalars().all()

    return SymbolListResponse(
        symbols=[
            SymbolConfigResponse(
                symbol=s.symbol,
                enabled=s.enabled,
                max_leverage=s.max_leverage,
            )
            for s in symbols
        ]
    )


@router.put("/symbols/{symbol}", response_model=SymbolConfigResponse)
async def update_symbol_config(
    symbol: str,
    body: SymbolUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    result = await db.execute(select(Symbol).where(Symbol.symbol == symbol.upper()))
    sym = result.scalar_one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="Symbol not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sym, key, value)

    await db.commit()
    await db.refresh(sym)

    return SymbolConfigResponse(
        symbol=sym.symbol,
        enabled=sym.enabled,
        max_leverage=sym.max_leverage,
    )


@router.get("/apikey", response_model=APIKeyStatusResponse)
async def get_api_key_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    # Check if any account exists with an API key
    from backend.models.account import Account
    result = await db.execute(select(Account).where(Account.is_active == True))
    account = result.scalar_one_or_none()

    if not account:
        return APIKeyStatusResponse(has_key=False)

    try:
        masked = mask_key(decrypt(account.api_key_encrypted))
    except Exception:
        logger.exception("Failed to decrypt stored API key")
        masked = None

    return APIKeyStatusResponse(
        has_key=True,
        masked_key=masked,
    )


@router.post("/apikey")
async def save_api_key(
    body: APIKeySaveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user_from_cookie(request, db)

    if not body.api_key or not body.api_secret:
        raise HTTPException(status_code=400, detail="API key and secret are required")

    from backend.models.account import Account

    # Check for existing account
    existing = await db.execute(select(Account).where(Account.is_active == True))
    account = existing.scalar_one_or_none()

    encrypted_key = encrypt(body.api_key)
    encrypted_secret = encrypt(body.api_secret)

    if account:
        account.api_key_encrypted = encrypted_key
        account.api_secret_encrypted = encrypted_secret
        account.updated_at = datetime.now(timezone.utc)
    else:
        account = Account(
            api_key_encrypted=encrypted_key,
            api_secret_encrypted=encrypted_secret,
            is_active=True,
        )
        db.add(account)

    await db.commit()

    logger.info(f"API key updated by user '{user.username}' (masked: {mask_key(body.api_key)})")

    return {"ok": True, "masked_key": mask_key(body.api_key)}
