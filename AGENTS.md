# DOX framework

- DOX is a highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

- Coding tasks: use cheap/free models (MiniMax, Mimo, Cline with local Ollama, or other free-tier providers). GPT-4o is NOT used for coding — only for code review.
- Code review: GPT-4o is acceptable and worth the cost for review quality.
- Planning/coordinator tasks: any available model.
- Trading strategy decisions: require explicit user confirmation before implementing.

## Child DOX Index

- **backend/** — FastAPI trading bot backend (models, routers, schemas, services, indicators, core engine) — see `backend/AGENTS.md`
- **frontend/** — React + Vite + TypeScript trading dashboard — see `frontend/AGENTS.md`
- **alembic/** — SQLAlchemy async migrations, configured with 2 revisions — see `alembic/AGENTS.md`
- **nginx/** — Production reverse-proxy and SSL config — see `nginx/AGENTS.md`
- **FUBOT_STEPS/** — 42-step build guide (PHASE1–PHASE6); each step is a markdown file — see `FUBOT_STEPS/AGENTS.md`
- **tests/** — pytest suite for backend units — see `tests/AGENTS.md`
- **scripts/** — Automation scripts (create_admin.py, init_db.py, setup_ssl.sh, fubotctl.sh); owned by this doc
- **fubot.service** — systemd unit for the backend; owned by this doc

## Project Overview

**FUBOT** — Binance Futures automated trading bot with React dashboard.
**URL:** https://fubot.bedikian.cc
**Repo:** https://github.com/nightwlkr/fubot (private; `origin`, branch `main`)
**Stack:** FastAPI (Python) · PostgreSQL · React + Vite + TypeScript + Tailwind CSS + Zustand · Binance Futures USDS-M · Nginx
**Strategy:** Crypto Banter 6-pillars (50 SMA filter, FVG entries, structural stops, 3:1+ R:R, -3R daily loss limit)

## Current Status

PHASE1–5 implemented and integration-tested; the 2026-10-11 audit's integration gaps are **closed** (engine wired, WebSocket live, signal pipeline real, Binance paths fixed, R:R math corrected). PHASE6 (validation + deployment) not started.

- **Backend (PHASE1–4)**: config, database, encryption, 10 models, 9 routers with 28 endpoints, 8 services, 11 indicator modules, 4 core components. ~4,900 lines of Python.
- **Engine stack wired** (`main.py` `init_bot`): BotEngine + PositionManager + DailyReset + services all share the `AsyncSessionLocal` FACTORY and open short-lived sessions per operation (AsyncSession is not task-safe — the scan loop runs concurrently with request handlers).
- **Signal pipeline live**: `SignalService.scan_symbol` fetches 1h/5m/15m candles in parallel, runs trend bias → FVG/3-candle (5m) / CHoCH (15m) → trend/volume/R:R filters, and persists PENDING/SKIPPED signals (deduped per symbol+strategy+direction). Verified against Binance testnet: 11 signals persisted in one scan with correct filter reasons.
- **WebSocket live**: `/ws` mounted with auth handshake (`auth` message → `auth_ok`, close codes 4001/4002/4003, receive-loop disconnect handling). AlertService methods are async and await `ws_manager.broadcast`.
- **Trading flow**: `StrategyService.evaluate_signal` gates (position/max-positions/age/price-deviation/R:R) then executes — paper mode creates simulated-fill orders + OPEN Position records + signal TRIGGERED (deliberate deviation from PHASE3_STEP3's "no records in paper": PHASE3_STEP7's 72h paper run and the dashboard need them); live mode places MARKET entry + reduce-only STOP_MARKET SL + TAKE_PROFIT_MARKET TP via Binance and logs every order to the `orders` table.
- **Database**: Alembic applied locally (`002_seed_data` head), role `fubot` provisioned, 10 tables + seeds verified.
- **Frontend (PHASE5)**: complete and building; all 25 endpoints match the backend OpenAPI contract.
- **Tests**: 102 passing across indicators, encryption, auth, services, signal pipeline (real DB), Binance paths (httpx MockTransport), WS manager, and API smoke (real DB + real app lifespan).

### Audit follow-up (2026-10-11, second pass)

Fixed in this pass:
- `binance_service.py`: all REST paths now carry the `/fapi` prefix; `get_order_book` uses `/fapi/v1/depth`; SL/TP orders send `reduceOnly=true`.
- Inverted R:R math fixed in `fvg.evaluate_fvg_entry`, `choch.detect_choch`, `three_candle.evaluate_three_candle_entry` (reward/risk, was risk/reward); `fvg.check_fvg_filled` + zone bounds corrected (numeric `zone_min < zone_max`; bullish fills when price returns DOWN into the gap). Step specs PHASE2_STEP2/STEP3 corrected to match.
- BotEngine/PositionManager/DailyReset wired in `main.py` lifespan; `/ws` route mounted; `WebSocketRouter` double-accept removed (accept happens once in the endpoint); keep-alive sleep loop replaced with a receive loop.
- `AlertService` rewritten async with the engine-facing API (`bot_started`, `trade_executed`, `tp_hit`, `sl_hit`, `be_trailing`, `daily_limit_warning/hit`, `error`, `new_signal`).
- `bot_engine.get_status` now awaits `daily_reset.get_daily_stats()` (was an un-awaited coroutine).
- OrderService gained `get_current_position` + `orders`-table logging for live orders.

Still open:
- **Live order path is untested against real exchange fills** — unit-tested with mocks only; PHASE3_STEP7's 72h paper run and PHASE4_STEP4's go-live checklist are the validation gates.
- BE-trailing (`PositionManager._check_be_trailing`) has no automated test; it depends on live 5m BOS detection.
- 11 Pydantic models still use `class Config` — convert to `ConfigDict`.
- `routers/*` each re-implement cookie auth — extract one shared `get_current_user` dependency.
- Login rate limiter locks the whole source IP (blocks valid logins too); `settings.value` is `JSON` not the spec'd `JSONB`; seed ships 10 symbols vs the 7 in PHASE1_STEP2.
- Backend cookie is `secure=True` — over plain-http dev the backend's own cookie is dropped; the frontend's JS cookie (from the response token) is what makes dev work. Consider `secure` only when `FUBOT_ENV == "production"`.
- No root `README.md` (PHASE6_STEP6). Dev server runs on port 8001 (a stale process holds 8000).

### Next

PHASE6 in order: 72h paper run (PHASE3_STEP7) with the dashboard live, -3R daily-limit validation (PHASE6_STEP2), end-to-end trade-flow test (PHASE6_STEP1), security audit (PHASE6_STEP3, add HSTS), WebSocket load test (PHASE6_STEP4), deploy (PHASE6_STEP5), `README.md` (PHASE6_STEP6).

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 102 passing (0 xfail — pinned defects were fixed)
- `PYTHONPATH=. python -c "import backend.main"` — backend imports cleanly
- `cd frontend && npm run build` — dashboard builds to `frontend/dist/`
- `cd frontend && npm run lint` — oxlint; warnings tolerated, errors block
- `alembic history` / `alembic current` — migration chain and applied head
- Python dependencies are installed in system Python 3.13 (`/Library/Frameworks/Python.framework/Versions/3.13/bin`); there is no project virtualenv

## Key Project Files

- `FUBOT_PRODUCT_PLAN.md` — Full product specification; read before any feature work
- `FUBOT_STEPS/` — 42 sequential build steps organized in 6 phases
  - PHASE1 (7 steps): Project foundation + FastAPI backend scaffold
  - PHASE2 (8 steps): Database models + migrations
  - PHASE3 (7 steps): Core trading services + Binance integration
  - PHASE4 (4 steps): Trading strategy engine + indicators
  - PHASE5 (10 steps): Frontend React dashboard
  - PHASE6 (6 steps): Deployment, Nginx, SSL, hardening
