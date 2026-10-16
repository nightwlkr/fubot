# DECISION — Strategy Review Cycle 1 (2026-10-16)

Lead-owned. Panel: MiMo-V2.5 (entry), MiniMax-M2.7 (exit), GLM-5.3 (risk), Agnes 2.5 Flash (strategy/validation).
Artifacts: `analysis/panel/{mimo-entry,minimax-exit,glm-risk,agnes-strategies}.md`.

## Round 1 — consensus and conflicts

Agreed by all four: (1) the fixed 3R take-profit truncates the right tail (DOT 7.47R / ETH 4.16R MFE vs ~4.15R/~2.55R exits); (2) FVG is negative and geometrically broken (n=5, -0.397R, 15.6% target); (3) the 1h-range backtest harness is unfit for 5m logic; (4) changes must be validated on a 5m-path replay before deploy.

Conflicts (all four attacked each other with numbers):
- MiMo vs MiniMax: only 5% of signals can physically reach 3R because the stop floor uses the **1h ATR on a 5m pattern** (risk 0.95% vs structural 0.05-0.16%, 15x) — so exit redesign is rearranging a 5% winner cohort until the R unit shrinks. MiniMax's rr_min 3->4 would make it worse.
- GLM: the notional cap (50%) silently cuts the 5% slider to 0.5% ($1.03/trade vs $10.25 intended); 3 same-direction positions = 2.49 effective bets (avg rho +0.744 vs BTC); cap and stop-metric must change in lockstep.
- GLM vs MiMo: a 5m-ATR stop halves risk%, so cap=200% + 5m-ATR must be recomputed together or sizing silently changes again.

## Decisions

| # | Change | Status | Evidence | Notes |
|---|---|---|---|---|
| D1 | Cut FVG (disable the scan branch) | **ADOPTED** | n=5, -0.397R, 0 wins, 15.6% target | Re-enable only after n>=20 with rebuilt geometry |
| D2 | Stop floor uses ATR(5m) for 3CANDLE (keep 1h for FVG->none now) | **ADOPTED** | risk 0.95%->0.51%, 3R reach 5%->18%, E[min(MFE,3R)] 1.00->1.53R (n=1,905) | Requires sizing re-check (D4) |
| D3 | Runner-trail: arm at +1.0R, trail 0.75R (one-way, after BE) | **ADOPTED** | +0.27R->+0.33R/trade on 22 real entries | Keep fixed TP as the far exit; BE trigger 0.5->0.75R |
| D4 | Notional cap 50%->200%, max_positions 5->2 (settings) | **ADOPTED** | cap cut risk 10x; rho-driven effective bets | Sliders, hot-reload; no code change |
| D5 | Limit fill window 12s->600s | **DEFERRED** | fill 26%->76%, adverse selection 27% vs 16% | Needs order-lifecycle design (open restings across scans); redesign next cycle |
| D6 | rr_min 3->4 | **REJECTED** | only 5% reach 3R today | Revisit after D2 |
| D7 | C3 body>=50% quality filter | **DEFERRED** | +0.05-0.10R proxy, split-direction | Needs 60d walk-forward first |
| D8 | Disable MATICUSDT | **ADOPTED** | 404 on fapi (delisted) | Operational |
| D9 | Same-direction exposure guard | **DEFERRED** | rho +0.744, 2.49 effective bets | D4 (max_positions=2) mitigates for now |
| D10 | Time stop / partial scale-out | **REJECTED** | -0.09R / -0.20R on the 22 | Minimax tested both |

## Validation before deploy
5m-path, no-lookahead replay mirroring `scan_symbol` (per Agnes's standard): stop-before-target, fees stated, >=60 days where klines allow, >=20 trades/cell, walk-forward split. Configs: baseline current vs (D2) vs (D3) vs (D2+D3) vs (D2+D3+D4).

## Validation results (CORRECTED — harness fixed 2026-10-16 04:15 +04)

The first harness run had two bugs (exit labels fired in disabled configs; `run_config`
never applied its arm/trail/be parameters to the simulator). Both fixed; the simulator
now passes 8 hand-built path sanity checks (LONG/SHORT trail, BE, SL, TP, stop-first
conservative ordering, one-way ratchet). n≈2,270 filled trades per config, 45 days,
8 symbols, entry = midpoint limit filled ≤60m, stop-before-target, 240m horizon.

**Gross (no fees) vs net (0.07% round trip):**

| config | gross R/trade | net R/trade | win% | exits |
|---|---|---|---|---|
| baseline (1h-ATR stop, TP 3R, BE0.5) | −0.017 | −0.108 | 11.2 | BE 1257, SL 735, TP 211 |
| D2 (5m-ATR stop) | +0.039 | −0.320 | 6.6 | BE 1712, SL 412, TP 150 |
| D3 (runner 1.0/0.75, 1h-ATR) | −0.012 | −0.102 | 31.8 | TRAIL 664, BE 793, TP 43 |
| D2+D3 | **+0.144** | −0.216 | 34.6 | TRAIL 741, BE 1075, TP 46 |
| D2+D3+BE0.75 | +0.099 | −0.261 | 45.7 | TRAIL 975, BE 473, TP 64 |
| TP 2R variants | — | −0.099…−0.322 | — | no improvement |

**Revised verdicts (supersede the round-1 table where they conflict):**
- **No tested configuration is net-positive after fees.** The strategy's gross edge is
  ≈0 to +0.14R/trade depending on config; fees (0.09R at 1% stops, 0.36R at 5m-ATR
  stops) consume all of it. **Fee drag, not exit geometry, is the binding constraint.**
- **D2 (5m-ATR stop) REVERSED → REJECT.** Gross-positive but net-terrible: the tight
  stop multiplies fee drag 4× and floods BE scratches (75% of trades). MiMo's
  "3R reachability" mechanism is real but swamped.
- **D3 (runner trail) REVERSED → DEFER/REJECT.** Net +0.006R vs baseline — noise. It
  also cuts TP hits 211→43 (the 0.75R trail kills trades that would have run to 3R).
  The panel's +0.4R estimate was 22-trade small-sample noise. Not worth migration 007
  + position-manager complexity for a wash.
- **D1 (cut FVG) STANDS** — orthogonal to the above; removes live-measured −0.397R/trade.
- **GLM's settings triple STANDS** — sizing coherence is independent of R-expectancy.
- **D8 (disable MATICUSDT) STANDS** — operational.
- **Real levers the data points to**: (a) fewer, better trades (D7 C3-body filter,
  volatility/session filters — raises gross edge and cuts fee count), (b) fee structure
  (maker exits vs taker), (c) NOT exit-geometry tinkering.

## Monitoring log
- 2026-10-16 01:24 +04: engine OK (`{"status":"ok"}`), 3 OPEN positions all protected (BNB SL 736.41, XRP SL 1.3486, BTC SL 81841.4), 0 trades/2h, 0 journal errors, no naked/unprotected events, no LIMIT-fill cancellations logged yet.
