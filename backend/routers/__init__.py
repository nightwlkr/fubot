"""FastAPI router modules for FUBOT API."""
from backend.routers import auth, bot, dashboard, trades, signals, positions, orders, settings, performance

__all__ = [
    "auth",
    "bot",
    "dashboard",
    "trades",
    "signals",
    "positions",
    "orders",
    "settings",
    "performance",
]
