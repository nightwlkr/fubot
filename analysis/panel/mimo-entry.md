# mimo — ENTRY TIMING & QUALITY (round 1)

Method: `analysis/panel/mimo-entry-replay.py` (run from repo root, `REPLAY_DAYS=30`).
Replays the exact live 3CANDLE pipeline (`signal_service.py:91-110`: incremental ATR(14)@1h,
`analyze_trend_bias` on the same 200×1h window, `detect_three_candles` on the same 200×5m window,
`evaluate_three_candle_entry(p, atr_1h, 3.0)`, gates at `signal_service.py:134-170` with live
defaults trade_neutral=on / strength=MODERATE) over Binance USDS-M klines, 30d, 8 tradeable symbols
(MATICUSDT returns 404 — delisted; 0 gaps in every series). No lookahead: index windows end at the
C3 close; fill walk assumes STOP-FIRST inside any candle touching both entry and stop; MFE/MAE in R
from the C3 close over 30m (live `signal_max_age_minutes`=60); extended reach over 240m.
Fees stated: maker 0.02% / taker 0.05% per side. **n = 1,905 gated signals (63–252/symbol); every
cell ≥20 except where marked.**

## 1. Top 3 provable leaks

1. **The 12s limit window fills only the bad trades — adverse selection.**
   `strategy_service.py:496` (`limit_fill_timeout_s = 12.0`). Fill rate at the midpoint limit:
   **12s: 26.4% · 5m: 41.4% · 60m: 75.9%** (n=1,905). Filled ≤1m (n=502) vs unfilled >1m (n=1,403):
   stop-first **27% vs 16%**, 30m MFE(capped 3R) **0.81R vs 1.07R**. The 74% cancelled setups did
   *better* than the ones traded. The 12s window admits exactly the retests that fall back into the
   zone (continuation) and rejects every pullback entry.
2. **The 1h-ATR stop floor is ~1.4–2.3× the 5m ATR and makes the 3R target unreachable.**
   `signal_service.py:106` passes `atr_1h` into the **5m** evaluator; `three_candle.py:9,131` floors
   the stop at 1.0× it. Measured ratio ATR1h/ATR5m: **1.4× (BTC) … 2.3× (DOT)**; the floor binds on
   essentially every signal — mean planned risk **0.95%** vs a mean structural C1-midpoint→wick risk
   of **0.05–0.16%** (15×). Share reaching the fixed 3R target within 240m: **5%** (4–6% per symbol,
   n=1,905). The trade's only real winner path (run past TP — DOT 7.47R, ETH 4.16R) is priced at 5%
   while 19% die at the stop within 30m.
3. **Midpoint is a bad level, but NOT the way it looks.** The midpoint of a 5m C1 range sits only
   ~0.05–0.16% below the C3 breakout, so it is nearly a breakout entry: 26.4% fill within 12s, and
   those fills are 3× more likely to be immediately re-tested (leak 1). Raising the level deepens
   adverse selection; the measured fix is to **lower the level and widen the window** (fill at 60m:
   75.9%; entries that wait for a pullback show 1.31R entry-adjusted 30m MFE vs 0.94R for instant
   fills, n=1,403/502).

## 2. Proposals (validate with the replay script, then a paper A/B)

1. **Widen the fill window 12s → 300–900s** (`strategy_service.py:496`). Expected: fills 26%→41–55%
   with pullback-priority; per-fill stop-first 27%→~16–20%. Validate: run both windows in paper for
   2 weeks, compare fill rate, stop-first %, and 30m MFE per fill; replay cells are already n=1,905.
2. **Swap the stop floor to the 5m ATR** — `signal_service.py:106`: pass `calculate_atr(k5m, 14)`
   (keep the 1h ATR for FVG). Expected: risk 0.95%→0.51%, target reach 5%→18% within 240m, E[min(MFE,3R)] 1.00R→1.53R (T6); fees in R rise 0.11→0.18. Risk/$ roughly doubles at fixed
   notional cap — coordinate with the risk panelist (cap binds today anyway).
3. **Filter: skip when C3 closes mid-range** (body/range < 0.5): proxyE +0.711R→+0.664R reversed
   — i.e. require C3 close ≥50% of its own range (14d: +0.846 vs +0.740; 30d: +0.711 vs +0.664,
   n=1,171/734). Cheap, zero extra data. Also: prefer wide C1 (>0.3%: proxyE +0.82 vs +0.66, n=372/1,533).

## 3. Risks / failure modes

- MFE/MAE proxyE is an UPPER bound (perfect 3R exit at MFE); live expectancy is lower (+0.012R/trade
  today) — relative comparisons hold, absolute numbers don't.
- Wider windows increase exposure to gap risk and to the ±1% zone gate (`strategy_service.py:135-143`)
  voiding setups; the gate-4b stop-breach check must stay (it caught 0 here but the ±1% tolerance
  admitted the naked-position cases).
- 5m-ATR stops are ~0.5% → more stop-outs per R unit; without an exit change the win rate at 3R stays
  ~5–18%, so expectancy depends on the exit panel's truncation.
- 30d covers one regime (bear-drift); per-symbol cells are 63–252 — symbols are not individually
  significant for filter splits.

## 4. What to cut

- The **12s window** entirely (it is the adverse-selection mechanism, not discipline).
- **CHoCH** (fires so rarely it adds no signals; the panel's strategy-mix colleague should confirm)
  and any further FVG tuning — FVG is n=5 live and −0.397R; disable until n≥20.
- Any new confirmation filter not backed by a ≥20-cell in T4/T5 — session filters and spread filters
  showed no measurable edge here and would add latency to a system whose edge is entry-price
  discipline.
