# tests/ — Backend Test Suite

## Purpose

pytest suite for backend units and API smoke — indicators, encryption, auth, services, WS manager, and full-request tests against the real app and dev database.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/tests/`
Status: 78 passing + 4 strict-xfail (2026-10-11 audit)

## Local Contracts

- Layout: `tests/backend/` with `conftest.py` + per-area test files
  - `test_indicators.py` — SMA/RSI (original)
  - `test_indicators_more.py` — ATR, FVG, BOS, CHoCH, pivots, structural, volume, 3-candle, trend
  - `test_encryption.py`, `test_auth_service.py`, `test_services.py`, `test_ws_manager.py`
  - `test_api_smoke.py` — real app + real DB: login flow, auth guard, route-prefix regression, empty states
- Run: `PYTHONPATH=. pytest tests/backend/ -q` using the system Python 3.13 pytest (`/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest`)
- `conftest.py` puts the repo root on `sys.path`, provides a session event loop, and a `db_session` fixture (rolled-back async session via `AsyncSessionLocal`)
- pytest config lives in root `pyproject.toml`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`
- `test_api_smoke.py` creates its own user via a dedicated engine (per-operation) so the DB pool never crosses event loops with the TestClient portal; it cleans up after itself
- Known-defect tests use `@pytest.mark.xfail(strict=True)` with a precise reason — when the defect is fixed, the strict xfail fails the suite and forces the marker's removal

## Work Guidance

- Import backend modules inside test functions or after the `sys.path` fix in `conftest.py`
- Expected values come from the definitions (SMA mean over the window, Wilder RSI/ATR, position-size math) — never snapshot the implementation's own output
- Keep pure-unit tests DB-free; the smoke file owns the DB-touching cases
- Uncovered today: `bot_engine` loop, `position_manager`, `binance_service` (needs httpx MockTransport), `binance_ws`, routers' filter paths with data. Add per-module files as those get wired (see backend/AGENTS.md Open Gaps)

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 78 passed, 4 xfailed
- A failing test blocks completion of the change that broke it

## Child DOX Index

No child AGENTS.md — test files are flat artifacts owned by this doc.
