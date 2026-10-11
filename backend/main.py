from contextlib import asynccontextmanager

from fastapi import FastAPI
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


async def load_bot_settings(db) -> dict:
    """Flatten the settings key-value table into the dict the engine expects."""
    result = await db.execute(select(Setting))
    return {row.key: row.value for row in result.scalars().all()}


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
    if engine:
        await engine.stop()
    if position_manager:
        await position_manager.stop()
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

    alert_service = get_alert_service()
    alert_service.register_ws_manager(ws_manager)
    risk_service = get_risk_service()
    order_service = OrderService(binance=binance, session_factory=AsyncSessionLocal)

    async with AsyncSessionLocal() as db:
        bot_settings = await load_bot_settings(db)

    signal_service = SignalService(
        session_factory=AsyncSessionLocal, binance=binance, settings=bot_settings
    )
    strategy_service = StrategyService(
        risk_service, order_service, binance=binance,
        session_factory=AsyncSessionLocal, settings=bot_settings,
    )
    set_strategy_service(strategy_service)

    daily_reset = DailyResetService(
        session_factory=AsyncSessionLocal, settings=bot_settings, alert_service=alert_service
    )
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
    )

    set_bot_engine(engine)
    app.state.bot_engine = engine
    app.state.position_manager = position_manager
    app.state.binance = binance

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


@app.get("/")
async def root():
    return {"message": "FUBOT API — Automated Binance Futures Trading Bot"}
