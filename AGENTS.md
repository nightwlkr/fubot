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
- **Signal pipeline live**: parallel 1h/5m/15m candle fetch → trend-strength gate (neutral/weak skip) → FVG (1h) / 3-candle (5m) / CHoCH (15m) → trend/volume/R:R/min-risk filters → PENDING/SKIPPED signals (deduped). Overtrading guard: daily cap + per-symbol cooldown. FVG uses the classic imbalance definition (fires ~45-50/symbol on 1h).
- **Trading flow**: `StrategyService.evaluate_signal` gates then executes — paper mode creates simulated fills + Position rows + TRIGGERED signals (deliberate deviation from PHASE3_STEP3, required by PHASE3_STEP7 and the dashboard); live mode places MARKET entry + reduce-only SL/TP and logs orders.
- **PHASE6 validation results (2026-10-11)**:
  - STEP1 E2E trade flow ✅ — `tests/backend/test_e2e_trade_flow.py` drives signal → decision → paper execution → TP hit → trade + daily summary. Caught 3 production bugs (fixed): naive/aware datetime crash in the signal-age check, wins counted toward the -3R stop (`used_r = abs(pnl_r)` → a winning day halted trading; now losses only), and daily-summary None/Decimal arithmetic crash on a day's first trade.
  - STEP2 -3R hard stop ✅ — `tests/backend/test_daily_reset.py`: 3.9R blocks trading, stops the engine, CRITICAL alert, latched until the UTC-midnight reset; warning fires at ≥80% (epsilon fix: 3.0×0.8 float boundary); wins don't count.
  - STEP3 security audit ✅ — `tests/backend/test_security_audit.py`: JWT cookie flags, API keys encrypted-at-rest (never plaintext, decryptable), no secrets in logs, injection payloads rejected, unauthenticated access blocked. HSTS added to nginx.
  - STEP4 WebSocket load ✅ — `scripts/ws_load_test.py`: 50/50 distinct-user clients received both broadcasts; worst latency 5ms; handshake max 21ms. (ConnectionManager is one socket per user by design — the test fans out across N users.)
- **Deployment (STEP5)**: `/opt/fubot` on `contabo01` (system user `fubot`, venv, read-only GitHub deploy key), Postgres 16 role/db with FRESH secrets (the leaked ones are dead), `fubot.service` enabled, nginx site + Let's Encrypt cert (HSTS on, WS upgrade on `/api/`). Deploy = `git pull` + `npm run build` + `systemctl restart fubot`.
- **Frontend (PHASE5)**: built and served; Vite dev proxy upgrades WS (`ws: true`).
- **Tests**: 150 passing.
- **README.md** (STEP6) covers setup, configuration, operation, troubleshooting.

### Open items

- FIXED 2026-10-12 (4th): every refresh signed the user out — authStore.checkAuth (cookie → /me → restore user) existed but was never invoked anywhere, so the in-memory user was always null on reload and the router bounced to /login. App now runs checkAuth() on load with an isRestoring gate ('Restoring session…' instead of the redirect flash); successful restore reconnects the WebSocket.
- FIXED 2026-10-12 (6th): refreshes still signed out — the frontend's JS-cookie copy of `fubot_token` never existed in production: the backend sets an httpOnly cookie with the same name and browsers silently reject same-name JS writes (worked in local dev only because the backend's Secure cookie isn't stored over plain http). `api/token.ts` now keeps a durable localStorage copy; all token access (Bearer header, WS auth, checkAuth) goes through it.
- FIXED 2026-10-12 (7th): still signing out after the localStorage fix — `/api/auth/me` (and every other authenticated endpoint) only read the httpOnly **cookie**, so a missing/failed cookie 401'd `checkAuth` even though the frontend held a valid token in localStorage and sent it as `Authorization: Bearer`. All endpoints now accept the cookie **or** the Bearer header (`auth_service.get_token_from_request`); restore no longer depends on cookie survival. Verified live: `/api/auth/me` + `/api/dashboard/overview` + `/api/bot/status` → 200 with Bearer only, 401 with none.
- FIXED 2026-10-12 (8th): STILL signing out — logs proved the browser got `/me` → **200** yet still landed on `/login`, so it was a **frontend routing race, not auth**. Four defects, all fixed: (1) `token.ts` kept a redundant js-cookie duplicate that went stale vs localStorage (clearToken can't remove the httpOnly cookie) → localStorage is now the sole frontend store; (2) `ProtectedLayoutWithPolling` fired `fetchOverview()` while `isRestoring` was still true → gated on `!isRestoring && user`; (3) the axios 401 interceptor hard-redirected (`window.location.href`) on ANY 401, including transient ones and `/auth/me` itself → now scoped to protected endpoints only; (4) `checkAuth` cleared the token on ANY error (network blip wiped the session) and bailed when `getToken()` was null (a browser holding only the httpOnly cookie was signed out) → now always attempts `/me`, clears only on a genuine 401, and `/me` **echoes the token** so a cookie-only session re-seeds localStorage. Regression tests: `/me` token echo + Bearer-only auth on `/me` and `/dashboard/overview`.
- FIXED 2026-10-12 (9th — THE ROOT CAUSE): a Playwright browser repro (login → hard-refresh → watch) finally isolated it: `/me` returned 200, localStorage held the token, zero 401s, yet the app landed on `/login` via a single `replace:/login`. **`checkAuth` read `res.data.user`, but `/me` returns a FLAT user `{id, username, role, token}` — not nested under `user` like the login response.** So `user` was always `undefined` after a successful restore and the auth guard saw `!user` → redirected to `/login`. This had been the real cause behind every "refresh signs you out" report (4th–8th fixes were necessary hardening but didn't hit this). Also restructured `App.tsx` so `<BrowserRouter>` is **always mounted** (the conditional mount above the restore gate raced the store); `RequireAuth`/`LoginRoute` render "Restoring session…" while `isRestoring` instead of redirecting. Repro now holds the session across hard + repeat refreshes (3/3 stable). 119 tests.
- FIXED 2026-10-12 (10th): navigation menu + Start button dead — the Overview EquityCurve "No equity data yet" empty-state overlay was `absolute inset-0` inside a `Card` that isn't positioned, so it escaped to the nearest ancestor and covered the entire content area, intercepting every pointer event. Nav links and the Start/Stop button appeared unclickable (clicks hit the overlay). Card is now `relative` and the overlay `pointer-events-none`. Browser-verified: all 5 nav links navigate, Start fires `POST /api/bot/start` and flips to Stop.
- FIXED 2026-10-12 (11th): Performance page crashed in a render loop — `TypeError: getSeries is not a function`. Code called `chart.getSeries()`, but lightweight-charts v5's `createChart()` returns a `ChartApi` that has **no** `getSeries()` method (it lives on a *pane* API). A fake type augmentation (`src/types/lightweight-charts.d.ts`) declared `getSeries` on `IChartApiBase`, so `tsc` accepted the broken call. Fixed by holding the series reference from `addSeries()` in Performance.tsx (all 3 charts) + Overview.tsx equity curve, and **deleting the misleading `.d.ts`** so the bug can't return. Browser-verified: Performance + Overview render all sections (equity/monthly/R-distribution/stats), 0 JS errors across all 5 pages. 119 tests.
- FIXED 2026-10-12 (12th — paper run loss root cause): analyzed the 256-trade paper history before resetting. Losses were driven by a **position-sizing bug**: `calc_position_size` computed `qty = risk_usd / price_diff * entry_price` — the trailing `* entry_price` made the realized SL loss `risk_usd × entry_price`, oversizing low-priced coins. LINKUSDT (~13 entry) got `qty=667,100` and a single **-16.5R / -$22k** stop that wiped the day (daily hit -19.83R, tripping the -3R stop). Secondary: wins were clamped tiny (88/98 wins at 0–0.1R) while losses were unbounded → expectancy -0.074R/trade. Fixed: `qty = risk_usd / price_diff` (SL loss now exactly risk% of balance) + new `cap_position_notional()` backstop (≤50% of balance notional). Updated the 3 tests that encoded the buggy formula. Verified: LINKUSDT scenario qty 706 → 52.9 (loss@SL = 1.75 = exactly 1%), capped to 6.54. 126 tests. Full history backed up to `backup_history_20261012.json` (gitignored) then reset; bot restarted fresh in paper mode sizing against the real 174.64 USDT balance (Futures-enabled mainnet key now loaded from the Account table).
- FIXED 2026-10-12 (13th — the -2R trades): the post-reset run still produced exact -2R losses (XRPUSDT: entry≈stop, exit 2× the razor-thin risk). Full trading-logic review found 5 bugs, all fixed: (1) `_persist_signals` dedup left `signal.db_id=None` → orphan positions with `signal_id=None`; duplicates now marked SKIPPED so they're never executed. (2) **Entry filled at live market price**, which could sit ~at the stop → realized risk degenerated to ~0 and any noise = -2R; entries now fill at the signal's entry-zone **midpoint** (deterministic risk; the alert/plan already reported this midpoint). (3) `pnl_r` was recomputed from the **live** `stop_loss`, which BE-trailing mutates toward entry → shrinks the denominator and inflates |R|; added `positions.initial_risk_distance` (frozen at open, Alembic `003_initial_risk`) and `_close_position` uses it. (4) exit_reason on breakeven stop-outs is correct (did hit the trailed stop) — no change. (5) No min-risk guard; added — skip if entry-to-SL < 0.05% of entry (`min_risk_pct`, relative so it scales). Regression tests added; 130 tests. Reset again (backup `backup_history_20261012_v2.json`), bot restarted; live positions verified: SL on correct side + `initial_risk_distance` populated.
- Binance balance wiring (2026-10-12): two gaps fixed so paper trades size against real capital — (1) the Settings-saved API key was stored encrypted in `Account` but never loaded into `BinanceService` (`main.py` built it env-only); `load_binance_credentials()` now decrypts the active Account at startup (`DB key > env key`). (2) New `paper_use_real_balance` setting (default TRUE) makes paper sizing fetch real USDT `availableBalance` (toggleable in Settings). Also had to flip `BINANCE_TESTNET=false` (bot was pointed at testnet, rejecting the mainnet key) and the user enabled **Futures** permission on the key (was the cause of the `-2015 Invalid API-key` — reading+spot alone don't grant `/fapi/*`). Live balance now reads 174.64 USDT.
- FEATURED 2026-10-12 (real-time market data): the bot was REST-polling position TP/SL every 30s — a lot can happen in 30s. A fully-implemented `BinanceWebSocketManager` existed but was never wired in. Now: `main.py` connects it at startup (`wss://fstream.binance.com/stream` — the bare host 404s the SUBSCRIBE protocol; the combined `/stream` endpoint wraps messages as `{stream, data}` matching `_handle_message`). `PositionManager` accepts `market_data_ws` and, on each REST pass, syncs **bookTicker** subscriptions to open-position symbols; a tick (~100ms bid/ask) updates the latest price and immediately runs the TP/SL check (per-symbol asyncio.Lock prevents double-close). The 30s REST loop remains as fallback/reconciliation and is the only place BE-trailing runs (needs 5m klines). Verified live: closes now register at ~-1R (LINKUSDT `-1.00`, SOLUSDT `-1.09`) instead of the old -2R. 133 tests (incl. 3 WS-tick regressions). Note: app-level `logger.info/warning` are suppressed under uvicorn's logging config — use `journalctl` + a functional check, not log lines, to verify app behavior.
- FIXED 2026-10-12 (14th — 3-candle razor stops): analyzed the fresh run (2 trades, both SL_HIT -2.09R). The 3-candle strategy placed its stop at the sweep wick (c2.low) — on adjacent 5m candles that's often a few pips from entry (observed 0.07%-0.3% risk), so price noise hit it instantly → immediate -1R. `evaluate_three_candle_entry` now enforces a **1× ATR(14) minimum stop distance** (the `atr` param was passed but unused): structural stop kept if already beyond the floor, else widened to entry ∓ atr. Verified live: LINKUSDT risk 0.0095 → 0.105 (0.79%); new positions show 0.38-0.53% stops. Regression test added. 134 tests. Also diagnosed the silent strategies: **FVG never fires** — even the standard FVG definition (`c2.low > c1.close`) yields 0 gaps on liquid 5m crypto (ranges always overlap); it needs a higher timeframe to ever trigger. **CHoCH** is a genuinely rare reversal condition (recent low breaks prior high) — selective by design, not broken. Net: the bot is effectively single-strategy (3-candle) today.
- FIXED 2026-10-13 (SKIPPED signals blocked all trading): user reported no trades all day despite the bot scanning. Manual no-DB scan found 54 candidates, but the running bot produced ZERO signals in 44 scans/43 min. Root cause: dedup matched `status IN (PENDING, SKIPPED)` — a correctly-rejected signal (counter-trend, weak) permanently reserved the (symbol, strategy, direction) slot, so a flipped market couldn't re-enter. Live: 43 stale SKIPPED rows blocking everything. Fix: dedup matches PENDING only (a rejected signal doesn't block future attempts); `_expire_stale_pending` also expires old SKIPPED rows. Regression test (PENDING → SKIPPED → scan → fresh PENDING). 149 tests. Verified live: after expiring 43 rows + restart, bot shows 14 fresh PENDING signals + 3 open positions.
- MONITORING 2026-10-14: user decided to keep the current scalper strategy as-is and monitor performance. Live paper results (3 days): 10-12 +12.05R, 10-13 -1.01R, 10-14 +2.52R. Testnet dry-run verified: entry orders fill, SL/TP placed via Algo Order API, close-all works. Next: monitor the 72h paper run for stable expectancy before considering go-live.
- FEATURED 2026-10-14 (manual close-all / kill switch): stopping the bot left open positions with no TP/SL management (dangerous in live mode). Added `PositionManager.close_all_positions()` (closes every OPEN position at market — real MARKET orders in live mode), `POST /bot/close-all` endpoint, and an Overview-page "Close All Positions (N)" button (with confirm dialog). Verified live: 3 testnet positions closed at market, trades recorded with MANUAL_CLOSE reason. 150 tests.
- VERIFIED 2026-10-14 (testnet dry-run): user provided a new testnet key (****gL7Z, $5000 test USDT) that works. Flipped `BINANCE_TESTNET=true` on VPS, started bot in testnet-live mode. The dry run revealed and fixed 3 critical issues: (1) order quantities weren't rounded to LOT_SIZE step (400 Bad Request); (2) SL/TP stop prices weren't rounded to PRICE_FILTER tick size; (3) Binance migrated conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET) to the **Algo Order API** (`POST /fapi/v1/algoOrder` with `algoType=CONDITIONAL` and `triggerPrice` — not `stopPrice`) on 2025-12-09. All fixed. Verified live: LINKUSDT entry order filled at 12.880, USDT balance $4366, no errors in journal. The live order path is now validated end-to-end on testnet with fake money.


- FEATURED 2026-10-13 (Settings UI for filters/guards): the backend returned the new filter/guard settings but the Settings page only rendered the original 5 sliders + paper balance. Added two sections: **Strategy Filters** (Allow Neutral Markets toggle, Volume Filter toggle, Min Trend Strength select) and **Overtrading Guards** (Max Daily Trades slider 1-50 w/ 0=no cap, Per-Symbol Cooldown slider 0-1440m w/ 0=no cooldown). All bound to the existing /settings/trading GET/PUT. Browser-verified: all 7 controls render, save round-trip persists, 0 JS errors.
- FEATURED 2026-10-13 (filter relaxation, backtest-backed): user wanted more trades (~1/day was too few). Backtest (9 symbols, 45d) isolated the **volume-confirmation filter** as the blocker that reduced profitability without adding quality: ON = 428 trades/+30.65R/+0.072 exp; OFF = 664 trades/+54.62R/+0.082 exp; + allow neutral = 697 trades/+81.27R/+0.117 exp. Applied: `volume_filter_enabled` default FALSE + `trade_neutral_markets` default TRUE (both settings, toggleable without redeploy). Counter-trend + RSI + strength gates UNCHANGED. Backtest CLI gains `--no-volume`. Bot restarted; scans climbing (7+), 0 errors. Note: the bot only runs when started via POST /bot/start (restart does NOT auto-start) — a service restart leaves it stopped until explicitly started.
- FEATURED 2026-10-13 (Trading P&L curve): added `GET /performance/pnl` (cumulative realized pnl_r + pnl_usdt per closed trade, ascending) + a `PnLChart` on the Performance page (green line, USD/R toggle, range buttons, caption noting it excludes collateral drift). Rationale: the equity curve tracks total account value (moves with BTC/ETH collateral — looked like +$37 while actual trading profit was +$1.95); this chart shows the honest strategy performance. Contract tests (ascending, shape) added. 147 tests. Browser-verified: chart painted (194k pixels), USD/R toggle works, 0 JS errors.
- FIXED 2026-10-12 (19th — Equity Curve blank): the dashboard overview endpoint returns `equity_curve` DESCENDING (newest first, `order_by(recorded_at.desc())`), but lightweight-charts `setData()` requires ASCENDING time order — feeding it descending data threw 'Value is null' (out-of-order time) and left the Equity Curve (7D) canvas blank. Frontend now sorts ascending before setData. (The Performance-page equity chart uses an ASC endpoint and was unaffected.) Browser-verified: canvas painted (157k pixels), 0 'Value is null' errors.
- FIXED 2026-10-12 (18th — USD display on positions + mislabeled PnL): Overview Open Positions showed only `PnL (R)` and labeled the backend's `unrealized_pnl` (which is USD: `(mark-entry)×qty`) as `R`. Fixed: renamed to `PnL (USD)` + added a `Value (USD)` column (qty × entry = position notional). Trade Log already had correct R + USDT columns; added the same `Value (USD)` column + CSV export field. Browser-verified: both pages show USD values ($86.44/$86.60), 0 JS errors.
- FIXED 2026-10-12 (17th — stale-PENDING deadlock blocked all trading): user reported only 1 trade + 6 stuck PENDING signals with 0 open positions. Root cause: a PENDING signal whose price never returned to its entry zone was never re-evaluated (dedup blocks fresh signals for the same symbol+strategy+direction) and never expired (only `evaluate_signal` expires, and it wasn't called again) — the stale row sat as PENDING for 3+ hours, blocking fresh signals for those symbols. Fix: `_expire_stale_pending()` runs at the start of `scan_symbol` — any PENDING signal older than `signal_max_age_minutes` (60) is marked EXPIRED, freeing the slot. Also clarified the balance question: `USDT availableBalance` (172.37) is the futures account's total USDT-margined margin (includes BTC/ETH collateral), not free USDT — sizing off it is correct for USDS-M. Regression test (backdate PENDING 2h → scan → EXPIRED + fresh PENDING). 145 tests. Verified live: after clearing 6 stale rows + restart, bot generates fresh PENDING signals and triggers trades.
- FIXED 2026-10-12 (16th — backend hang: WS reconnect task leak): user reported 'Restoring session' hanging + login spinning. Backend accepted /health (200) but timed out on /auth/login; process was at 1.5GB memory / 1h36m CPU with a tight 'Concurrent call to receive() is not allowed' loop in journalctl. Root cause: `BinanceWebSocketManager._listen()`'s reconnect path called `connect()`, which spawns a NEW `_listen()` task while the old loop kept running → two concurrent receive() on one socket → error → reconnect → third listener → exponential task+memory leak that starved the event loop so request handlers never ran. Fix: split `connect()` into `_open_socket()` (no task) and have `_listen()` reconnect INLINE; `connect()` cancels any prior listener. Regression tests (single listener after reconnects; close cancels listener). Verified: memory 1.5GB → 100MB, login 0.3s, 0 receive() errors, browser repro (login → hard refresh → refresh) holds session with all 5 pages error-free. 144 tests.
- FIXED 2026-10-12 (15th — strategy quality pass, 5 improvements): (0a) daily-loss latch never cleared — `check_and_reset()` had zero callers; now date-based and self-clears from `check_and_enforce_limit` (a -3R trip no longer blocks trading until restart). (0b) alert toasts rendered empty — backend sends `{level, message}` top-level, frontend read `msg.data`; frontend fixed + payload-shape test. (1) **Trend-strength gate** — skip NEUTRAL markets (`trade_neutral_markets`, default false) and ALIGNED entries below `trade_min_trend_strength` (default MODERATE); scoped to aligned so counter-trend/RSI checks stay authoritative. (2) **Overtrading guard** — daily cap (`max_daily_trades`, default 12) + per-symbol cooldown (`symbol_cooldown_minutes`, default 240, via `_minutes_since_last_trade`); the old run traded 101×/day. (3) **FVG on 1h with the classic imbalance definition** (`c3.low > c1.high` gap) — was 0 detections (required zero adjacent-candle overlap, impossible on crypto), now 45-50 per symbol / 19 live signals. (4) **Backtest harness** (`backend/backtest.py` + `scripts/backtest.py`) — replays the scan pipeline over history, no lookahead; BTC 30d default filters: 42 trades, 54.8% win, **+0.200 expectancy/trade**; A/B flags for configs. 142 tests. Deployed; live signals show both strategies firing + all new gates active (neutral/weak skips observed).
- FIXED 2026-10-12 (5th): React #310 (rendered more hooks) right after the restoring screen flipped to the dashboard — the auth-restore edit had inserted the isRestoring early return ABOVE the WS-wiring useEffect in App, so App mounted with 2 hooks and re-rendered with 3. Both effects now sit above any conditional return (rule: no hooks after early returns). Note the ErrorBoundary cannot catch App-level hook errors — it only guards page components.
- VERIFIED LIVE 2026-10-12: WSS event stream works end-to-end — one 12s window captured alert (start), bot_status, 10× signal_update, position_closed, alert (TP/SL), pnl_update, alert+bot_status (stop). Frontend toasts + store refresh wired.
- FIXED 2026-10-12 (3rd): Performance page died on the R-distribution chart — /api/performance/strategies returned per-strategy stats while the frontend reads {bucket, count} pairs. The endpoint now buckets closed trades by pnl_r (-3R or worse … 3R+). ErrorBoundary added around the router (PHASE5_STEP10) so a page crash shows a recoverable screen instead of unmounting the app (which made every nav item appear dead). Contract test added. 117 tests.
- FIXED 2026-10-12 (2nd): PositionsTable crashed on a position's first render — PositionItem/PositionResponse lacked mark_price/unrealized_pnl (frontend calls .toFixed on them). Both routers now fetch live mark prices via the public ticker endpoint and compute signed unrealized PnL. Regression test creates an OPEN position and asserts the fields. Minor backlog note: positions.opened_at shows a 2h skew (naive utcnow vs timestamptz session TZ) — cosmetic.
- FIXED 2026-10-12: dashboard crashed right after login — /api/dashboard/overview read nonexistent app.state.bot_* attributes so bot_status lacked scan_count/last_scan (Header called .toLocaleString() on undefined), and TodayStats silently dropped the PnLCard fields (wins/losses/pnl_r/pnl_usdt/trades_count/daily_limit_status). Overview now reads the real engine via app.state.bot_engine.get_status(); schemas match the frontend contract; regression test added. Paper run restarted after the deploy.
- **Binance API key is live** (2026-10-12): a Futures-enabled mainnet key is saved encrypted in the `Account` table and loaded into the engine at startup (`load_binance_credentials`). Balance reads work (174.64 USDT). `BINANCE_TESTNET=false`. Paper trades size against this real balance (`paper_use_real_balance`, default on).
- The 72h paper run must pass PHASE3_STEP7's criteria before any go-live discussion. **Going live with real money requires explicit user confirmation.** (Run restarted 2026-10-12 ~14:30 after the sizing fix + history reset — the prior 256-trade history was invalidated by the sizing bug and is backed up in `backup_history_20261012.json`, gitignored.)
- Live order path is mock-tested only until real fills are observed.
- BE-trailing has no automated test (needs live 5m BOS); 11 `class Config` → `ConfigDict`; per-router duplicated cookie auth → shared dependency; login rate limiter locks the whole source IP; old dev DB (`fubot` on localhost) still holds pre-audit data — the production DB is the VPS.
- Old Binance/DB/JWT credentials leaked in the old public repo history must stay rotated — never reuse the values from the pre-2026-10-12 `.env`.

### Next

Monitor the 72h paper run (scheduled checks). On pass: go-live decision (user), then PHASE4_STEP4's go-live checklist. Backlog: ConfigDict migration, shared auth dependency, smarter login rate limiting.

## Verification

- `PYTHONPATH=. pytest tests/backend/ -q` — 150 passing (e2e trade flow, -3R stop, security audit, payload contracts included)
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
