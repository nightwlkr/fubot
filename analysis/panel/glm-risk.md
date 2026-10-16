# Risk, Sizing & Portfolio — panel analysis (glm-risk)

Production read-only (SQL + Binance klines, 2026-10-15 UTC). Samples: 23 live trades (1 day, 22/23 SHORT — the direction bias is itself evidence), 30 positions (28 with frozen risk), 719×1h / 180×4h bars per symbol, MC n=200k i.i.d. Measured E[trade] = **+0.012R ± 0.21R SE** (net of fees, n=23, barely above the 20-trade bar) — edge unproven; sizing only scales it. Balance $158.66 (latest equity_log) of which $47.2 = 97% is locked margin, not loss.

## 1. Top 3 provable leaks

**L1 — The notional cap is the real risk system; the 5% slider is dead.** `strategy_service.py:226-243`, `risk_service.py:58`
- Intended 5% of $205 = **$10.25/trade**. Measured mean **$1.06** (28 positions, range $0.03–3.38) = 0.53% of balance → **9.7× haircut**. All 28 positions sized within ±8% of the cap ($73–106 notional vs cap $74–105): the cap binds on every trade (median stop 1.09% < the 5%/50% = 1% binding threshold).
- Downstream, everything runs at 1/10 scale: the −3R latch = **−$3.18 (1.6%)**, not −$30.75. The cap WARNING (`strategy_service.py:236`) goes to a uvicorn-suppressed logger (AGENTS.md note) — still silent in practice.
- Honoring math at 5x: cap% = risk%/stop% = 500% at 1% stops; margin fit needs k×cap% ≤ 500% → **at k=3 the max honorable risk is 1.66% (cap 166%)**; 5% is coherent only at k=1 with 100% margin. "5% risk + concurrent positions" is impossible at 5x leverage.

**L2 — The −3R latch is net-of-day and close-time only; it never saw the worst day.** `daily_reset.py:75` (`used_r = max(0, −net)`), checked only before new trades (`bot_engine.py:158`), warning at 2.4R (`daily_reset.py:107-108`)
- Measured: 12 losses = **−6.73R gross**, but net stayed positive all day (min −0.462R) → **used_r maxed at 0.462R = 15% of limit; 0 warnings, 0 trips**, while the +6.52R morning became +0.276R by close (6 full −1R stops in 3h08m, 3 inside 10 min, 17:04–17:14).
- Open positions are invisible until close: 3 concurrent (no direction cap — `strategy_service.py:110-116` counts positions only) can flush −3R straight through a −1R latch state → −6R overshoot = −30% at honored 5%.
- Ledger hole (SQL-verified): **6 closed positions have no trade row**. Two are the intentional DOT cleanup, but **334 XRP ($79.6, 17:10) and 337 BTC ($82.5, 19:19) are live closes** whose R never reached `daily_summary`; both symbols were re-adopted by reconcile as NEW rows (336, 339) with `initial_risk_distance=NULL` → BE-trail skips them (`position_manager.py:351`), and a stop=None close falls back to a fictional 0.01 risk distance (`position_manager.py:552`) → garbage R into the latch.

**L3 — The book is one macro bet; there is no portfolio control.** (measured, 719 1h bars)
- Pairwise ρ̄ among live alts: **0.67 (30d) / 0.80 (7d, current regime)** (a prior 20d window gave 0.74 — consistent). Corr-to-BTC 7d: ETH .90 SOL .89 BNB .89 XRP .88 LINK .86; betas: APT **1.78**, AVAX/LINK 1.57, DOT 1.51, XRP 1.44, SOL 1.38 — a "1%" stop on APT is 1.78× BTC noise.
- N_eff(k same-direction) = k/(1+(k−1)ρ): k=3 → **1.15** (ρ=.80) / 1.30 (ρ=.67); asymptote 1/ρ = **1.25 bets regardless of position count**. A 3-stack carries σ = r·√(3(1+2ρ)) = 2.79r vs 1.73r if independent — 1.6× the risk the count suggests. P(≥3 of 9 alts move with BTC per 1h) = 0.91; ≥5 = 0.72.
- Universe rot: **MATICUSDT is frozen** (30d corr ≈ −0.04; 7d corr = nan = zero variance; 2,804 EXPIRED signals, 0 ever TRIGGERED — orders there cannot fill). **APTUSDT — not in the stated 9 — took 6/23 trades (26%) and $13.73 of $29.67 risk dollars (46%)**: accidental concentration in the highest-beta symbol, the worst diversifier.

## 2. Proposals (exact values → expected effect → validation)

**P1 — Triple: `risk_percent` 5→1.5, `max_position_notional_pct` 50→150, `max_positions` 3 (keep).**
- $3.08/trade at stops ≥1% (cap binds only below 1%: a 0.75% stop → $2.31 eff). Worst-case margin 3×1.5B notional = 4.5B/5 = **90% of balance** (fits 5x, $20 spare). Latch becomes −$9.23 (4.5%).
- Worst-case day: −3R net latch (−$9.23) + same-direction flush overshoot −2R (−$6.15) = **−$15.4 (7.5%)** — vs today's de facto ≈ −$5 (2.5%), vs slider-honored 5%: −$31…−51 (15–25%).
- Alternative (keep slider at 5, cap 200, k=2 → delivers 2% at 1% stops) keeps the slider dishonest (delivered risk varies with stop width); rejected.
- Validation: re-run the positions SQL (notional vs cap; risk_usd ≈ $3.08); zero margin rejects in journalctl; cap-WARNINGs only on sub-1% stops.

**P2 — Exposure rule (fits the existing gate): keep per-symbol 1 (`strategy_service.py:104-108`); add `max_same_direction_positions=2` beside the max_positions count (`:110-116`, same count pattern); add a total risk-at-risk gate: skip a new entry when Σ(open initial_risk×qty)/balance > 3%.**
- Effect: worst same-direction macro flush −2R (−3%) not −3R; N_eff 1.11 for the pair — honest at ρ̄=0.80. With 22/23 signals SHORT this cuts trade count ~1/3 — that is the point.
- Validation: force 2 same-direction opens → assert SKIP reason; SQL sum of open risk ≤ 3%.

**P3 — Bound the day: `max_daily_trades` 0→12 (it is OFF; 23 ran yesterday) + a gross-loss stop at 4.5R (1.5×limit) as an in-memory accumulator in DailyResetService (no migration).**
- On the measured day the gross stop trips 17:14 (cum gross 4.67R) → saves the last 2 full losses (−2.06R = −$2.2 today, −$6.4 at P1 sizing). MC (measured dist): net-latch trips 34.5% of days at 23 trades vs 21.5% at 12 — the cap alone halves bleed exposure.
- Streak profile at the design's 15–20% WR (q=0.65 full loss, +3R wins): per 100 trades expect 9.6 runs ≥3 losses, 4.1 ≥5, 1.7 ≥7; P(3 consecutive latched days) = 2.7% at p_trip=0.30 → ~54% at least once per 30 days = −13.5% at P1 sizing. Keep −3R (it bounds the day); only risk% bounds the streak.
- Validation: extend `test_daily_reset` with a gross-accumulator test; journalctl trip alert.

**P4 — Hygiene: disable MATICUSDT; `min_risk_pct` 0.0005→0.005 (0.5%) + stop ≥ 1×ATR14 for ALL strategies; decide APT in/out explicitly.**
- Fee arithmetic (taker 0.045%×2 = 0.09% of notional, `position_manager.py:537`): 0.08R at the 1.09% median stop, **0.23R at 0.39% (pos 297), 2.25R at 0.04% (pos 337: $0.03 risk vs $0.074 fee)**. The gate checks the planned distance (`strategy_service.py:202-211`) but the fill recompute re-freezes the actual distance (`:453`) → razor stops pass.
- Validation: no new positions with stop_pct < 0.5 (same SQL); MATIC scan count 0.

## 3. Risks / failure modes
- P1 scales a possibly-negative edge: post-BE-regime E ≈ −0.125R (n=7, inconclusive but operative) → MC E[day] ≈ −1.9R (−$5.9) with P(latch trip)=0.67. **Gate any further risk increase on ≥100 trades net-of-fees positive.**
- 90% worst-case margin: a 3rd entry can be margin-rejected in a flush (pre-fill reject = missed trade, not naked risk; `_flatten_if_unprotected` covers the rest).
- A gross latch stops some good days too (MC: E[day | tripped at gross-3R] = +0.24R) — accept at 4.5R; it is a stop, not an optimizer.
- Correlations are regime-dependent (0.67→0.80 in one week; →1 in crashes). i.i.d. MC understates clustering (6 losses/3h measured) — treat worst-day figures as floors.
- The L2 close/adopt cycle (no-trade closes → NULL-risk re-adopts) needs a regression test before any latch/R accounting is trusted.

## 4. What I'd cut
- The 50–1000% notional slider range: coherent ceiling at L=5, k=3 is 166%; above that it over-margins or re-binds. Replace with a derived readout: effective risk = min(risk%, cap×stop%).
- MATICUSDT (frozen, 2,804 wasted signals, cannot fill) and the 9-way-diversification illusion (N_eff ≤ 1.3 at k=3): add no symbols until pairwise ρ̄ < 0.5.
- The dead leverage trio — `config.py:33-34`, `symbols.max_leverage`, `positions.leverage` default 10 — none reach the exchange (`set_leverage` `binance_service.py:297`, zero callers). Wire it or delete it; the account silently depends on the manually-set 5x.
- Defer vol-parity per-symbol sizing (ATR plumbing through all strategies) until the entry panel proves edge at ≥100 trades.

**Recommended triple: risk 1.5% / cap 150% / max_positions 3 (+ same-direction ≤2, daily trades 12, gross stop 4.5R). Per-trade risk $3.08; worst realistic day −$9.23 (4.5%); absolute worst day −$15.4 (7.5%); peak margin 90%.**
