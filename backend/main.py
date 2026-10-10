from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from backend.config import settings
from backend.database import init_db, close_db

# Import all routers
from backend.routers import auth, bot, dashboard, trades, signals, positions, orders, settings as settings_router, performance
from backend.services import auth_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown
    await close_db()


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
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(bot.router, prefix="/api/bot", tags=["bot"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(performance.router, prefix="/api/performance", tags=["performance"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "FUBOT API — Automated Binance Futures Trading Bot"}
