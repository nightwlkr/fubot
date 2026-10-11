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
- **scripts/** — Automation scripts (create_admin.py, init_db.py, setup_ssl.sh, fubotctl.sh, clear_test_data.py, paper_test_report.py, ws_load_test.py); owned by this doc
- **fubot.service** — systemd unit for the backend; owned by this doc

## Project Overview

**FUBOT** — Binance Futures automated trading bot with React dashboard.
**URL:** https://fubot.bedikian.cc
**Repo:** https://github.com/nightwlkr/fubot (private; `origin`, branch `main`)
**Stack:** FastAPI (Python) · PostgreSQL · React + Vite + TypeScript + Tailwind CSS + Zustand · Binance Futures USDS-M · Nginx
**Strategy:** Crypto Banter 6-pillars (50 SMA filter, FVG entries, structural stops, 3:1+ R:R, -3R daily loss limit)

## Current Status

**Deployed to production** at https://fubot.bedikian.cc (VPS `contabo01`, 2026-10-11) and the **72h paper run is live** (started 23:55 local, ends 2026-10-14 ~23:55 — automated daily checks + final sign-off are scheduled). PHASE6 STEP1/2/3/4 validated, STEP5 deployed, STEP6 README done.

- **Backend (PHASE1–4)**: config, database, encryption, 10 models, 9 routers with 28 endpoints, 8 services, 11 indicator modules, 4 core components. ~4,900 lines of Python.
- **Engine stack wired** (`main.py` `init_bot`): every component shares the `AsyncSessionLocal` FACTORY and opens short-lived sessions per operation (AsyncSession is not task-safe — the scan loop runs concurrently with request handlers).
- **Signal pipeline live**: parallel 1h/5m/15m candle fetch → trend bias → FVG/3-candle (5m) / CHoCH (15m) → trend/volume/R:R filters → PENDING/SKIPPED signals (deduped per symbol+strategy+direction).
- **Trading flow**: `StrategyService.evaluate_signal` gates then executes — paper mode creates simulated fills + Position rows + TRIGGERED signals (deliberate deviation from PHASE3_STEP3, required by PHASE3_STEP7 and the dashboard); live mode places MARKET entry + reduce-only SL/TP and logs orders.
- **PHASE6 validation results (2026-10-11)**:
  - STEP1 E2E trade flow ✅ — `tests/backend/test_e2e_trade_flow.py` drives signal → decision → paper execution → TP hit → trade + daily summary. Caught 3 production bugs (fixed): naive/aware datetime crash in the signal-age check, wins counted toward the -3R stop (`used_r = abs(pnl_r)` → a winning day halted trading; now losses only), and daily-summary None/Decimal arithmetic crash on a day's first trade.
  - STEP2 -3R hard stop ✅ — `tests/backend/test_daily_reset.py`: 3.9R blocks trading, stops the engine, CRITICAL alert, latched until the UTC-midnight reset; warning fires at ≥80% (epsilon fix: 3.0×0.8 float boundary); wins don't count.
  - STEP3 security audit ✅ — `tests/backend/test_security_audit.py`: JWT cookie flags, API keys encrypted-at-rest (never plaintext, decryptable), no secrets in logs, injection payloads rejected, unauthenticated access blocked. HSTS added to nginx.
  - STEP4 WebSocket load ✅ — `scripts/ws_load_test.py`: 50/50 distinct-user clients received both broadcasts; worst latency 5ms; handshake max 21ms. (ConnectionManager is one socket per user by design — the test fans out across N users.)
- **Deployment (STEP5)**: `/opt/fubot` on `contabo01` (system user `fubot`, venv, read-only GitHub deploy key), Postgres 16 role/db with FRESH secrets (the leaked ones are dead), `fubot.service` enabled, nginx site + Let's Encrypt cert (HSTS on, WS upgrade on `/api/`). Deploy = `git pull` + `npm run build` + `systemctl restart fubot`.
- **Frontend (PHASE5)**: built and served; Vite dev proxy upgrades WS (`ws: true`).
- **Tests**: 119 passing.
- **README.md** (STEP6) covers setup, configuration, operation, troubleshooting.

### Open items

- FIXED 2026-10-12 (4th): every refresh signed the user out — authStore.checkAuth (cookie → /me → restore user) existed but was never invoked anywhere, so the in-memory user was always null on reload and the router bounced to /login. App now runs checkAuth() on load with an isRestoring gate ('Restoring session…' instead of the redirect flash); successful restore reconnects the WebSocket.
- FIXED 2026-10-12 (6th): refreshes still signed out — the frontend's JS-cookie copy of `fubot_token` never existed in production: the backend sets an httpOnly cookie with the same name and browsers silently reject same-name JS writes (worked in local dev only because the backend's Secure cookie isn't stored over plain http). `api/token.ts` now keeps a durable localStorage copy; all token access (Bearer header, WS auth, checkAuth) goes through it.
- FIXED 2026-10-12 (7th): still signing out after the localStorage fix — `/api/auth/me` (and every other authenticated endpoint) only read the httpOnly **cookie**, so a missing/failed cookie 401'd `checkAuth` even though the frontend held a valid token in localStorage and sent it as `Authorization: Bearer`. All endpoints now accept the cookie **or** the Bearer header (`auth_service.get_token_from_request`); restore no longer depends on cookie survival. Verified live: `/api/auth/me` + `/api/dashboard/overview` + `/api/bot/status` → 200 with Bearer only, 401 with none.
- FIXED 2026-10-12 (8th): STILL signing out — logs proved the browser got `/me` → **200** yet still landed on `/login`, so it was a **frontend routing race, not auth**. Four defects, all fixed: (1) `token.ts` kept a redundant js-cookie duplicate that went stale vs localStorage (clearToken can't remove the httpOnly cookie) → localStorage is now the sole frontend store; (2) `ProtectedLayoutWithPolling` fired `fetchOverview()` while `isRestoring` was still true → gated on `!isRestoring && user`; (3) the axios 401 interceptor hard-redirected (`window.location.href`) on ANY 401, including transient ones and `/auth/me` itself → now scoped to protected endpoints only; (4) `checkAuth` cleared the token on ANY error (network blip wiped the session) and bailed when `getToken()` was null (a browser holding only the httpOnly cookie was signed out) → now always attempts `/me`, clears only on a genuine 401, and `/me` **echoes the token** so a cookie-only session re-seeds localStorage. Regression tests: `/me` token echo + Bearer-only auth on `/me` and `/dashboard/overview`.
- FIXED 2026-10-12 (9th — THE ROOT CAUSE): a Playwright browser repro (login → hard-refresh → watch) finally isolated it: `/me` returned 200, localStorage held the token, zero 401s, yet the app landed on `/login` via a single `replace:/login`. **`checkAuth` read `res.data.user`, but `/me` returns a FLAT user `{id, username, role, token}` — not nested under `user` like the login response.** So `user` was always `undefined` after a successful restore and the auth guard saw `!user` → redirected to `/login`. This had been the real cause behind every "refresh signs you out" report (4th–8th fixes were necessary hardening but didn't hit this). Also restructured `App.tsx` so `<BrowserRouter>` is **always mounted** (the conditional mount above the restore gate raced the store); `RequireAuth`/`LoginRoute` render "Restoring session…" while `isRestoring` instead of redirecting. Repro now holds the session across hard + repeat refreshes (3/3 stable). 119 tests.
- FIXED 2026-10-12 (10th): navigation menu + Start button dead — the Overview EquityCurve "No equity data yet" empty-state overlay was `absolute inset-0` inside a `Card` that isn't positioned, so it escaped to the nearest ancestor and covered the entire content area, intercepting every pointer event. Nav links and the Start/Stop button appeared unclickable (clicks hit the overlay). Card is now `relative` and the overlay `pointer-events-none`. Browser-verified: all 5 nav links navigate, Start fires `POST /api/bot/start` and flips to Stop.
- FIXED 2026-10-12 (5th): React #310 (rendered more hooks) right after the restoring screen flipped to the dashboard — the auth-restore edit had inserted the isRestoring early return ABOVE the WS-wiring useEffect in App, so App mounted with 2 hooks and re-rendered with 3. Both effects now sit above any conditional return (rule: no hooks after early returns). Note the ErrorBoundary cannot catch App-level hook errors — it only guards page components.
- VERIFIED LIVE 2026-10-12: WSS event stream works end-to-end — one 12s window captured alert (start), bot_status, 10× signal_update, position_closed, alert (TP/SL), pnl_update, alert+bot_status (stop). Frontend toasts + store refresh wired.
- FIXED 2026-10-12 (3rd): Performance page died on the R-distribution chart — /api/performance/strategies returned per-strategy stats while the frontend reads {bucket, count} pairs. The endpoint now buckets closed trades by pnl_r (-3R or worse … 3R+). ErrorBoundary added around the router (PHASE5_STEP10) so a page crash shows a recoverable screen instead of unmounting the app (which made every nav item appear dead). Contract test added. 117 tests.
- FIXED 2026-10-12 (2nd): PositionsTable crashed on a position's first render — PositionItem/PositionResponse lacked mark_price/unrealized_pnl (frontend calls .toFixed on them). Both routers now fetch live mark prices via the public ticker endpoint and compute signed unrealized PnL. Regression test creates an OPEN position and asserts the fields. Minor backlog note: positions.opened_at shows a 2h skew (naive utcnow vs timestamptz session TZ) — cosmetic.
- FIXED 2026-10-12: dashboard crashed right after login — /api/dashboard/overview read nonexistent app.state.bot_* attributes so bot_status lacked scan_count/last_scan (Header called .toLocaleString() on undefined), and TodayStats silently dropped the PnLCard fields (wins/losses/pnl_r/pnl_usdt/trades_count/daily_limit_status). Overview now reads the real engine via app.state.bot_engine.get_status(); schemas match the frontend contract; regression test added. Paper run restarted after the deploy.
- **Binance API keys on the VPS are empty placeholders** — public klines work (signals flow), but equity snapshots and exchange-side position checks need fresh TESTNET keys in `/opt/fubot/.env` + `systemctl restart fubot`.
- The 72h paper run must pass PHASE3_STEP7's criteria before any go-live discussion. **Going live with real money requires explicit user confirmation.**
- Live order path is mock-tested only until real fills are observed.
- BE-trailing has no automated test (needs live 5m BOS); 11 `class Config` → `ConfigDict`; per-router duplicated cookie auth → shared dependency; login rate limiter locks the whole source IP; old dev DB (`fubot` on localhost) still holds pre-audit data — the production DB is the VPS.
- Old Binance/DB/JWT credentials leaked in the old public repo history must stay rotated — never reuse the values from the pre-2026-10-12 `.env`.

### Next

Monitor the 72h paper run (scheduled checks). On pass: go-live decision (user), then PHASE4_STEP4's go-live checklist. Backlog: ConfigDict migration, shared auth dependency, smarter login rate limiting.

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 119 passing (e2e trade flow, -3R stop, security audit, payload contracts included)
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
