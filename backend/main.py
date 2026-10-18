from contextlib import asynccontextmanager
import logging

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from backend.config import settings
from backend.core.bot_engine import BotEngine
from backend.core.daily_reset import DailyResetService
from backend.core.position_manager import PositionManager
from backend.core.websocket_manager import WebSocketRouter, ws_manager
from backend.database import AsyncSessionLocal, close_db, init_db
from backend.models.setting import Setting
from backend.routers import auth, bot, dashboard, trades, signals, positions, orders, settings as settings_router, performance
from backend.routers.bot import set_bot_engine
from backend.services.alert_service import get_alert_service
from backend.services.binance_service import BinanceService
from backend.services.order_service import OrderService
from backend.services.risk_service import get_risk_service
from backend.services.signal_service import SignalService
from backend.services.strategy_service import StrategyService, set_strategy_service
from backend.core.bot_mode import normalise_bot_mode

logger = logging.getLogger(__name__)


async def load_bot_settings(db) -> dict:
    """Flatten the settings key-value table into the dict the engine expects."""
    result = await db.execute(select(Setting))
    return {row.key: row.value for row in result.scalars().all()}


async def load_binance_credentials(binance: BinanceService) -> None:
    """Load the saved (encrypted) API key/secret from the Account table into the
    Binance client at startup.

    Keys saved via the Settings UI live encrypted in `Account`; without this the
    engine fell back to the (empty) env-var key and every signed call — balance,
    positions, equity snapshots — failed. Precedence: DB key > env key.

    Loads the key matching the environment (binance._testnet) — a mainnet key
    401s on testnet and vice versa, so the environments must never share a key.
    Falls back to the other env's key only if the matching one is absent (with
    a warning, since it will fail auth).
    """
    from backend.models.account import Account
    from backend.encryption import decrypt

    is_testnet = bool(getattr(binance, "_testnet", False))
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Account).where(
            Account.is_active == True,  # noqa: E712
            Account.testnet == is_testnet,
        ))
        # scalars().first() — not scalar_one() — so a duplicate active row (e.g.
        # from a data mishap) can't crash the whole app at startup.
        account = result.scalars().first()
        if not account:
            logger.warning("No %s API key stored — signed calls will fail. "
                           "Save one in Settings.",
                           "testnet" if is_testnet else "mainnet")
            return
        try:
            key = decrypt(account.api_key_encrypted)
            secret = decrypt(account.api_secret_encrypted)
        except Exception:
            logger.exception("Failed to decrypt stored Binance credentials; using env key")
            return
        if key and secret:
            binance.set_credentials(key, secret)
            logger.info("Loaded %s Binance credentials from the Account table",
                        "testnet" if is_testnet else "mainnet")



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await init_bot(app)

    yield

    # Shutdown
    engine = getattr(app.state, "bot_engine", None)
    position_manager = getattr(app.state, "position_manager", None)
    binance = getattr(app.state, "binance", None)
    market_data_ws = getattr(app.state, "market_data_ws", None)
    if engine:
        await engine.stop()
    if position_manager:
        await position_manager.stop()
    if market_data_ws:
        await market_data_ws.close()
    if binance:
        await binance.close()
    await close_db()


async def init_bot(app: FastAPI) -> None:
    """Build the bot engine stack and register it on the app (PHASE4_STEP1).

    Every component shares the AsyncSessionLocal FACTORY and opens short-lived
    sessions per operation — AsyncSession is not safe for concurrent use by the
    scan loop and request handlers.
    """
    binance = BinanceService()
    await load_binance_credentials(binance)

    # Real-time market-data stream (bookTicker) for position TP/SL monitoring.
    # Connected lazily; subscriptions are managed per open-position symbol.
    from backend.services.binance_ws import BinanceWebSocketManager
    market_data_ws = BinanceWebSocketManager(testnet=binance._testnet)
    try:
        await market_data_ws.connect()
    except Exception:
        logger.warning("Binance market-data WebSocket failed to connect; "
                       "position monitoring falls back to REST polling", exc_info=True)
        market_data_ws = None

    alert_service = get_alert_service()
    alert_service.register_ws_manager(ws_manager)
    risk_service = get_risk_service()
    order_service = OrderService(binance=binance, session_factory=AsyncSessionLocal)

    async with AsyncSessionLocal() as db:
        bot_settings = await load_bot_settings(db)
        # Restore the last-used mode so a restart doesn't revert to paper
        from backend.models.setting import Setting
        from sqlalchemy import select
        result = await db.execute(select(Setting).where(Setting.key == "bot_mode"))
        saved_mode = result.scalar_one_or_none()
        # normalise_bot_mode so a JSON-quoted '"live"' is honoured rather than
        # discarded — see backend/core/bot_mode.py.
        bot_settings["bot_mode"] = normalise_bot_mode(
            saved_mode.value if saved_mode else None)

    signal_service = SignalService(
        session_factory=AsyncSessionLocal, binance=binance, settings=bot_settings
    )
    # Built before StrategyService so the flatten path can notify it (a FLATTENED
    # trade is a real loss and must reach the -3R accumulator immediately).
    daily_reset = DailyResetService(
        session_factory=AsyncSessionLocal, settings=bot_settings, alert_service=alert_service
    )
    strategy_service = StrategyService(
        risk_service, order_service, binance=binance,
        session_factory=AsyncSessionLocal, settings=bot_settings,
        daily_reset=daily_reset,
    )
    set_strategy_service(strategy_service)

    engine = BotEngine(
        settings=bot_settings,
        binance=binance,
        signal_service=signal_service,
        strategy_service=strategy_service,
        risk_service=risk_service,
        order_service=order_service,
        alert_service=alert_service,
        daily_reset=daily_reset,
        session_factory=AsyncSessionLocal,
    )
    daily_reset.bot = engine  # -3R stop can stop the engine

    position_manager = PositionManager(
        binance=binance,
        order_service=order_service,
        daily_reset=daily_reset,
        alert_service=alert_service,
        settings=bot_settings,
        session_factory=AsyncSessionLocal,
        market_data_ws=market_data_ws,
    )

    set_bot_engine(engine)
    engine.position_manager = position_manager  # wire for /bot/close-all
    app.state.bot_engine = engine
    app.state.position_manager = position_manager
    app.state.binance = binance
    app.state.market_data_ws = market_data_ws

    # Position manager monitors open positions every 30s, independent of bot start/stop
    await position_manager.start()


# WebSocket route (PHASE4_STEP2) — auth handshake + broadcast channel
ws_router = WebSocketRouter()

app = FastAPI(
    title="FUBOT",
    description="Automated Binance Futures Trading Bot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"https://{settings.DOMAIN}", f"http://{settings.VPS_HOST}:3000", "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
# Routers carry their own sub-prefix (/auth, /bot, ...) — main adds /api only.
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(bot.router, prefix="/api", tags=["bot"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(signals.router, prefix="/api", tags=["signals"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(orders.router, prefix="/api", tags=["orders"])
app.include_router(settings_router.router, prefix="/api", tags=["settings"])
app.include_router(performance.router, prefix="/api", tags=["performance"])

app.add_api_websocket_route("/ws", ws_router.websocket_endpoint)
# The frontend dials API_BASE + "/ws" (= /api/ws in dev and prod) — alias it.
app.add_api_websocket_route("/api/ws", ws_router.websocket_endpoint)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Root now serves the SPA via the catch-all below (the old JSON root handler
# was a leftover that blocked "/"; the catch-all serves index.html for both
# "/" and any client-side route).
# ---- Static SPA hosting (added 2026-10-16 for the Nginx Proxy Manager cut-over) ----
# The tier ABOVE us (NPM) is a dumb reverse proxy: it cannot serve files or do
# SPA try_files fallback, so the backend owns the built frontend instead.
#   - assets under /assets/*  -> exact files (cacheable, immutable)
#   - anything else           -> index.html (client-side routing)
# API/WS routes are registered ABOVE this catch-all and win by registration order.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    # SPA catch-all: matches any GET that wasn't claimed by an earlier
    # route. We MUST not swallow real /api/* requests — many endpoints
    # (`/api/signals`, `/api/trades`, `/api/positions`, …) are registered
    # with a trailing slash and rely on FastAPI's redirect_slashes to
    # 307 `/api/foo` -> `/api/foo/`. Starlette's `path` converter is
    # greedy, so this catch-all is reached for `/api/foo` too; we
    # forward the 307 manually and 404 for unknown `/api/*` paths so
    # axios treats them as real misses instead of HTML responses.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str):
        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse
        if full_path.startswith(("api/", "ws", "health", "docs", "openapi")):
            if full_path.startswith("api/") and not full_path.endswith("/"):
                # PRESERVE THE QUERY STRING. A 307 to the slashed form drops
                # it, which silently discards every filter parameter — the same
                # failure as Starlette's own redirect_slashes. The four listing
                # routers now register both "/" and "" so this branch is only
                # reached for paths with no route at all, but a filter-stripping
                # redirect should not exist in our own code either.
                target = f"/{full_path}/"
                qs = request.url.query
                if qs:
                    target = f"{target}?{qs}"
                return RedirectResponse(url=target, status_code=307)
            raise HTTPException(status_code=404, detail="Not Found")
        idx = _FRONTEND_DIST / "index.html"
        if idx.is_file():
            # NO CACHE on the shell. Vite content-hashes the asset filenames, so
            # index.html is the only thing that can go stale — a cached shell
            # pins the browser to the previous bundle after a deploy and the
            # new code silently never runs.
            return FileResponse(
                idx,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return {"message": "FUBOT API — Automated Binance Futures Trading Bot"}
else:
    logger.warning("frontend/dist not found at %s — SPA will not be served", _FRONTEND_DIST)
