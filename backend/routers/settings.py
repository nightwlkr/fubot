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

# Ceiling on an R multiple that `exchange_sync` will write. The strategy's R:R is
# 3:1, so a real trade lands well inside single digits; anything at or above this
# means the division used an implausible risk basis, not that the trade was
# exceptional. See the guard in `exchange_sync`.
MAX_PLAUSIBLE_R_MULTIPLE = 10.0


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
        "strategy_obfvg_enabled", "strategy_obfvg_max_age_bars",
        "paper_use_real_balance", "paper_balance_usdt",
        "trade_neutral_markets", "trade_min_trend_strength", "volume_filter_enabled",
        "symbol_cooldown_minutes", "max_daily_trades",
        "max_position_notional_pct",
        "be_trail_trigger_r",
        "profit_lock_trigger_r", "profit_lock_trail_r", "stale_trade_hours",
        "funding_rate_filter_enabled", "funding_skip_threshold", "atr_stop_mult",
        "weekly_drawdown_limit_r",
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
        "strategy_obfvg_enabled": True,
        "strategy_obfvg_max_age_bars": 2,
        "paper_use_real_balance": True,
        "paper_balance_usdt": 10000.0,
        "trade_neutral_markets": True,
        "trade_min_trend_strength": "MODERATE",
        "volume_filter_enabled": False,
        "symbol_cooldown_minutes": 240,
        "max_daily_trades": 12,
        "max_position_notional_pct": 50.0,
        "be_trail_trigger_r": 0.5,
        "profit_lock_trigger_r": 1.0,
        "profit_lock_trail_r": 0.5,
        "stale_trade_hours": 24.0,
        "funding_rate_filter_enabled": True,
        "funding_skip_threshold": -0.0001,
        "atr_stop_mult": 1.5,
        "weekly_drawdown_limit_r": 5.0,
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
        strategy_obfvg_enabled=bool(settings.get("strategy_obfvg_enabled", True)),
        strategy_obfvg_max_age_bars=max(0, int(settings.get("strategy_obfvg_max_age_bars", 2))),
        paper_use_real_balance=bool(settings.get("paper_use_real_balance", True)),
        paper_balance_usdt=float(settings.get("paper_balance_usdt", 10000.0)),
        trade_neutral_markets=bool(settings.get("trade_neutral_markets", True)),
        trade_min_trend_strength=str(settings.get("trade_min_trend_strength", "MODERATE")),
        symbol_cooldown_minutes=int(settings.get("symbol_cooldown_minutes", 240)),
        max_daily_trades=int(settings.get("max_daily_trades", 12)),
        volume_filter_enabled=bool(settings.get("volume_filter_enabled", False)),
        max_position_notional_pct=float(settings.get("max_position_notional_pct", 50.0)),
        be_trail_trigger_r=float(settings.get("be_trail_trigger_r", 0.5)),
        profit_lock_trigger_r=float(settings.get("profit_lock_trigger_r", 1.0)),
        profit_lock_trail_r=float(settings.get("profit_lock_trail_r", 0.5)),
        stale_trade_hours=float(settings.get("stale_trade_hours", 24.0)),
        funding_rate_filter_enabled=bool(settings.get("funding_rate_filter_enabled", True)),
        funding_skip_threshold=float(settings.get("funding_skip_threshold", -0.0001)),
        atr_stop_mult=float(settings.get("atr_stop_mult", 1.5)),
        weekly_drawdown_limit_r=float(settings.get("weekly_drawdown_limit_r", 5.0)),
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

    # Apply immediately. Every engine component (BotEngine, StrategyService,
    # SignalService, DailyResetService, PositionManager) is handed the SAME
    # settings dict at startup, so mutating it in place hot-reloads the running
    # engine — before this, changing a slider (Risk Per Trade, the notional cap,
    # filters, guards) had no effect until the service was restarted.
    try:
        from backend.main import app, load_bot_settings
        engine = getattr(app.state, "bot_engine", None)
        live = getattr(engine, "settings", None) if engine is not None else None
        if isinstance(live, dict):
            fresh = await load_bot_settings(db)
            # clear+update with no await between them: atomic w.r.t. the loop
            live.clear()
            live.update(fresh)
    except Exception:
        logger.exception("Failed to hot-reload bot settings")

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
    testnet: bool = Query(False, description="Check the testnet key instead of mainnet"),
):
    user = await get_current_user_from_cookie(request, db)

    from backend.models.account import Account
    result = await db.execute(select(Account).where(
        Account.is_active == True, Account.testnet == testnet))
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

    # One active account per environment (testnet/mainnet) — a mainnet key
    # 401s on testnet and vice versa, so they must never share a row.
    existing = await db.execute(select(Account).where(
        Account.is_active == True, Account.testnet == bool(body.testnet)))
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
            testnet=bool(body.testnet),
        )
        db.add(account)

    await db.commit()

    env = "testnet" if body.testnet else "mainnet"
    logger.info(f"{env} API key updated by user '{user.username}' (masked: {mask_key(body.api_key)})")

    return {"ok": True, "masked_key": mask_key(body.api_key)}


@router.post("/exchange-sync")
async def exchange_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reconcile trade history, PnL, and performance with Binance's actual data.

    Fetches realized PnL from Binance's income API for each recent trade and
    corrects any DB trades where the PnL drifted (e.g. recorded as $0 because
    the income event hadn't been recorded yet at close time).
    """
    user = await get_current_user_from_cookie(request, db)

    from backend.models.trade import Trade
    from backend.models.position import Position
    from backend.models.account import Account
    from backend.encryption import decrypt

    # Get Binance credentials
    result = await db.execute(select(Account).where(
        Account.is_active == True, Account.testnet == False))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=400, detail="No active Binance account configured")

    api_key = decrypt(account.api_key_encrypted)
    api_secret = decrypt(account.api_secret_encrypted)

    # Build a Binance client
    from backend.services.binance_service import BinanceService
    binance = BinanceService(api_key=api_key, api_secret=api_secret)

    # Get recent trades (last 48h)
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    result = await db.execute(
        select(Trade).where(Trade.closed_at >= cutoff).order_by(Trade.closed_at))
    trades = result.scalars().all()

    fixed = 0
    checked = 0
    for trade in trades:
        checked += 1
        try:
            opened_at = trade.closed_at - timedelta(minutes=5)  # approximate open time
            start_ms = int(opened_at.timestamp() * 1000)
            # Bound the END of the window too. Without it the query runs to "now",
            # so on a symbol the bot has since re-entered it sweeps in the NEXT
            # position's commission and funding as well — which, now that fees
            # are actually counted, would charge one trade another's costs.
            end_ms = int((trade.closed_at + timedelta(minutes=15)).timestamp() * 1000)
            income = await binance.get_position_income(
                trade.symbol, start_time=start_ms, end_time=end_ms)

            if income.get("events", 0) == 0:
                continue  # no income events — keep synthetic PnL

            exchange_pnl = income.get("totalPnl", 0)
            db_pnl = float(trade.pnl_usdt)

            # Only fix if significantly different (> $0.01)
            if abs(exchange_pnl - db_pnl) > 0.01:
                # Recalculate R from the position's frozen risk distance.
                #
                # GUARD (2026-10-18): this used to divide unconditionally by
                # `risk_amt`. On positions whose stored risk basis is far below
                # the intended 1R that produced absurd multiples — a $1.36 trade
                # became +92.69R because its frozen 1R was $0.0147. One sync run
                # put four trades past |R| > 10 and the daily summary read
                # +134.6R, which is the breaker's OWN input.
                #
                # The division is only sound if the risk basis is credible. When
                # it is not, the USD correction is still applied but R is LEFT
                # ALONE: an unreadable risk basis must not silently become a
                # fabricated multiple.
                pnl_r = float(trade.pnl_r)
                if trade.position_id:
                    pos = await db.get(Position, trade.position_id)
                    if pos and pos.initial_risk_distance and float(pos.initial_risk_distance) > 0:
                        risk_amt = float(pos.initial_risk_distance) * float(trade.quantity)
                        candidate_r = exchange_pnl / risk_amt
                        if abs(candidate_r) >= MAX_PLAUSIBLE_R_MULTIPLE:
                            logger.warning(
                                "Exchange sync: SKIPPING R rewrite for %s trade "
                                "%s — %.2fR implied from a frozen 1R of $%.4f is "
                                "not credible; keeping the recorded %.2fR",
                                trade.symbol, trade.id, candidate_r, risk_amt,
                                float(trade.pnl_r))
                            await db.execute(
                                update(Trade).where(Trade.id == trade.id)
                                .values(pnl_usdt=round(exchange_pnl, 4))
                            )
                            fixed += 1
                            continue
                        pnl_r = candidate_r

                await db.execute(
                    update(Trade).where(Trade.id == trade.id)
                    .values(pnl_usdt=round(exchange_pnl, 4), pnl_r=round(pnl_r, 4))
                )
                fixed += 1
                logger.info(f"Exchange sync: {trade.symbol} {db_pnl} -> {exchange_pnl} USDT")
        except Exception as exc:
            logger.warning(f"Exchange sync: failed to check {trade.symbol}: {exc}")

    await db.commit()

    # The latch threshold must equal the breaker's configured limit. It used to
    # be the literal `-3.0`, so `daily_loss_limit_r=-5` left the sync latching
    # days at -3. Read through the shared clamp so a malformed row degrades
    # identically here and in the breaker, and read the row shape with
    # `limit_setting_row_value` so the sync cannot accept a shape the breaker's
    # loader (`routers/settings.py` load_bot_settings) would have discarded.
    from backend.core.daily_reset import limit_r_from, limit_setting_row_value
    limit_row = await db.execute(
        select(Setting).where(Setting.key == "daily_loss_limit_r"))
    limit_setting = limit_row.scalar_one_or_none()
    raw_limit = limit_setting_row_value(limit_setting)
    limit_r = limit_r_from({"daily_loss_limit_r": raw_limit}
                           if raw_limit is not None else {})

    # Recalculate daily summaries for affected dates.
    #
    # Two things about this statement are load-bearing and both were wrong until
    # 2026-10-18. Read them before editing.
    #
    # 1. The day key is `(closed_at AT TIME ZONE 'UTC')::date`, NOT a bare
    #    `DATE(closed_at)`. `closed_at` is `timestamptz`, so `DATE()` resolves it
    #    in the SESSION `TimeZone` -- on the production VPS `Europe/Berlin`
    #    (UTC+2) -- while `daily_summary.date` is written in UTC by
    #    `DailyResetService.update_daily_summary`. They disagree for the four
    #    hours a day between 22:00 and 24:00 UTC. The consequence was worse than
    #    "matches no rows": the group key became the LOCAL day, so
    #    `WHERE daily_summary.date = t.d` matched TODAY'S UTC row and overwrote
    #    it with a PARTIAL day, which on a losing day pushed `used_r` back UNDER
    #    the limit and silently re-opened an allowance the breaker had consumed.
    #
    # 2. The window is filtered on the DATE (`:first_day`), not on the 48h
    #    timestamp. `closed_at >= :cutoff` is a timestamp comparison, so a sync
    #    at 23:02 on Oct 17 used a cutoff of Oct 15 23:02 and recomputed Oct 15
    #    from only the trades after 23:02. This statement SETS absolute
    #    aggregates, so that is not a stale day -- it is a COMPLETE day
    #    overwritten with a PARTIAL one, silently dropping trades out of the very
    #    total the daily-loss breaker reads. The date filter widens the window to
    #    up to ~72h of days. That is the correct trade: understating a losing day
    #    is the failure mode that matters, and recomputing a couple of extra
    #    whole days is harmless (the statement is idempotent -- it overwrites).
    #
    # `:cutoff` is still bound for the per-trade income-API loop above, which
    # filters individual trades and is affected by neither bug.
    # Pinned by TestExchangeSyncGroupsByTheUtcDay and
    # TestExchangeSyncSourceContract in tests/backend/test_daily_limit_rules.py.
    from sqlalchemy import text
    first_day = (datetime.now(timezone.utc) - timedelta(hours=48)).date()
    await db.execute(text("""
        UPDATE daily_summary SET
            total_trades = t.cnt, wins = t.wins, losses = t.losses, breakeven = t.bes,
            total_pnl_r = t.total_r, total_pnl_usdt = t.total_usdt,
            worst_trade_r = t.worst_r, best_trade_r = t.best_r,
            -- `used_r` is max(0, -total_r). The max() is NOT spelled here, and
            -- the COALESCE below is DEFENSIVE, not load-bearing: the subquery
            -- already emits `COALESCE(SUM(pnl_r),0) as total_r`, so `total_r`
            -- cannot be NULL by the time it is read. It is kept as an explicit
            -- NULL guard, but it guards nothing reachable -- do not describe it
            -- as protecting a real case. A winning day is excluded because
            -- `limit_r` is strictly POSITIVE -- -4.5 >= 3.0 is simply false.
            -- `limit_r_from` only returns values in (0, MAX] (see
            -- TestLimitRFrom), so the two spellings agree on every input it can
            -- produce. Do not "fix" this into GREATEST(0, -total_r) for symmetry
            -- with used_r_from: it would be equivalent, and the shared clamp is
            -- what actually keeps the two answers equal.
            daily_loss_limit_hit = (COALESCE(-t.total_r, 0) >= :limit_r)
        FROM (SELECT (closed_at AT TIME ZONE 'UTC')::date as d, COUNT(*) as cnt,
            COUNT(*) FILTER (WHERE pnl_r > 0) as wins,
            COUNT(*) FILTER (WHERE pnl_r < 0) as losses,
            COUNT(*) FILTER (WHERE pnl_r = 0) as bes,
            -- Inner COALESCE prices a NULL (unknown 1R) row at a full -1R so the
            -- recompute can never treat an unmeasurable trade as free; the outer
            -- one only guards an empty result set.
            COALESCE(SUM(COALESCE(pnl_r,-1)),0) as total_r,
            COALESCE(SUM(pnl_usdt),0) as total_usdt,
            COALESCE(MIN(COALESCE(pnl_r,-1)),0) as worst_r,
            COALESCE(MAX(COALESCE(pnl_r,-1)),0) as best_r
            FROM trades
            WHERE (closed_at AT TIME ZONE 'UTC')::date >= :first_day
            GROUP BY (closed_at AT TIME ZONE 'UTC')::date) t
        WHERE daily_summary.date = t.d
    """), {"first_day": first_day, "limit_r": limit_r})
    await db.commit()

    logger.info(f"Exchange sync by user '{user.username}': checked {checked}, fixed {fixed}")
    return {"ok": True, "checked": checked, "fixed": fixed}
