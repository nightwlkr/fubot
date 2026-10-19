"""Core trading components for FUBOT bot engine.

Deliberately contains NO imports.

This package used to eagerly re-export BotEngine, PositionManager,
DailyResetService and the websocket manager. Two of those import
`backend.services.*`, and `backend/services/__init__.py` imports
`strategy_service`, which imports `backend.core.risk_units`. So merely touching
`backend.core` first ran the package initialiser, which reached sideways into
`backend.services`, which was itself still mid-initialisation:

    ImportError: cannot import name 'StrategyService' from partially
    initialized module 'backend.services.strategy_service' (most likely due to
    a circular import)

This only reproduced when `backend.core` was imported before
`backend.services` — `backend.main` happened to do it in a working order, so
the test suite (which imports the app via conftest) never saw it. A standalone
`from backend.services.binance_service import BinanceService` failed outright.

The fix is structural: no module in this package imports another at package
init time. Import what you need from its own module instead:

    from backend.core.daily_reset import DailyResetService
    from backend.core.risk_units import risk_distance

`tests/backend/test_import_order.py` pins this — a fresh interpreter must be
able to import any backend submodule first, in any order.
"""