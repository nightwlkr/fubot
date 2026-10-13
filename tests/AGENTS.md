# tests/ — Backend Test Suite

## Purpose

pytest suite for backend units and API smoke — indicators, encryption, auth, services, WS manager, and full-request tests against the real app and dev database.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/tests/`
Status: 148 passing, 0 xfail (2026-10-12 — payload-contract + WS-tick + ATR-stop + backtest + binance-ws-leak + stale-signal + pnl-curve + filter-relaxation regressions added)

## Local Contracts

- Layout: `tests/backend/` with `conftest.py` + per-area test files
  - `test_indicators.py` — SMA/RSI (original)
  - `test_indicators_more.py` — ATR, FVG, BOS, CHoCH, pivots, structural, volume, 3-candle, trend
  - `test_encryption.py`, `test_auth_service.py`, `test_services.py`, `test_ws_manager.py`
  - `test_binance_paths.py` — REST path building via `httpx.MockTransport` (no network)
  - `test_signal_service.py` — full scan pipeline with mocked Binance + real DB persistence
  - `test_api_smoke.py` — real app + real DB: login flow, auth guard, route-prefix regression, bot engine wiring, empty states
  - `test_e2e_trade_flow.py` — PHASE6_STEP1: signal → decision → paper execution → TP hit → trade + daily summary
  - `test_daily_reset.py` — PHASE6_STEP2: -3R hard stop, 80% warning, wins-don't-count, midnight reset
  - `test_security_audit.py` — PHASE6_STEP3: cookie flags, encrypted-at-rest keys, no secrets in logs, injection resistance
  - payload-contract regressions: overview bot_status/today PnLCard fields, position mark_price/unrealized_pnl, strategies R-buckets
- Run: `PYTHONPATH=. pytest tests/backend/ -q` using the system Python 3.13 pytest (`/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest`)
- `conftest.py` puts the repo root on `sys.path` and provides a `db_session` fixture (rolled-back async session via `AsyncSessionLocal`). No custom `event_loop` fixture — pytest-asyncio 0.24 deprecates it, and a session-scoped loop coupled every async test to one loop.
- `test_api_smoke.py` uses a FUNCTION-scoped client (fresh app lifespan per test) so the engine's DB session never spans TestClient portal lifecycles; its `_run` helper restores the thread's current event loop after `asyncio.run` (asyncio.run leaves `set_event_loop(None)`, which breaks the next async test under pytest-randomly)
- Known-defect tests use `@pytest.mark.xfail(strict=True)` with a precise reason — when the defect is fixed, the strict xfail fails the suite and forces the marker's removal

## Work Guidance

- Import backend modules inside test functions or after the `sys.path` fix in `conftest.py`
- Expected values come from the definitions (SMA mean over the window, Wilder RSI/ATR, position-size math) — never snapshot the implementation's own output
- Keep pure-unit tests DB-free; `test_signal_service.py` and `test_api_smoke.py` own the DB-touching cases (dedicated engines / fresh lifespans, self-cleaning)
- Uncovered today: `position_manager` BE-trailing (needs live 5m BOS), `binance_ws`, routers' filter paths with data. Add per-module files as those get exercised (see backend/AGENTS.md Open Gaps)

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 148 passed
- A failing test blocks completion of the change that broke it

## Child DOX Index

No child AGENTS.md — test files are flat artifacts owned by this doc.
