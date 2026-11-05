# Funding-Rate Contrarian Mean-Reversion Backtest — 2026-11-05

Research-only harness: `scripts/funding_contrarian_backtest.py`. No production
code path touched; nothing deployed or committed.

## Question

Does going LONG a USDT-M perp immediately after an extreme **negative** funding
print earn a positive drift over 24–72h (crowded-short squeeze)? This is a
different hypothesis from the funding-as-directional-signal idea already
falsified in `funding-basis-research-2026-11-05.md`. Published anchors:
Stratmill BTCUSDT 2019–2026, funding ≤ −0.02% → +2.87% avg net 24h, 71.7% win
(n=53); Aijmr +0.49–0.51% avg 24h on the negative tail only (asymmetric).

## Method

- **Universe:** BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, LTC, AVAX (USDT).
- **Window:** trailing 365d ending at the last closed 5m boundary
  (funding prints 2025-11-05 → 2026-11-04 UTC; every symbol returned 1,095
  prints = full 8h coverage, no shallow-history symbols).
- **Signal:** funding print with `fundingRate <= threshold`; thresholds
  −0.01% / −0.02% (primary) / −0.03%.
- **Entry:** open of the first 1h bar with `open_time` **strictly greater**
  than `fundingTime` (~1h after settlement; the print is only known at
  settlement, so nothing at or before it is used — no lookahead).
- **Position rule:** one position per symbol per variant; signals during an
  open position are **ignored, not queued**.
- **Exits** (run independently): `24h` fixed, `72h` fixed, `norm72` = exit at
  the first 1h bar open strictly after the next funding print `> −0.01%`,
  capped at 72h.
- **Stops** (run independently): none, or `entry − 2×ATR(14)` on 1h bars at
  the entry bar (lookahead-free `calculate_atr_series`). Intrabar stop-first
  ordering; a bar opening through the stop gap-fills at its adverse open; a
  bar whose open is the scheduled exit fills at its open before its range is
  inspected.
- **Costs:** `backend/core/cost_model.py` taker model — 0.045% fee + 0.01%
  slippage per side = **0.11% round trip of notional**.
- **Funding carry:** for every funding event strictly inside the hold,
  income `+= −fundingRate × notional` (longs receive when rate < 0). Included
  in net; it is small (+0.005% to +0.068% per trade on average).
- **Units:** primary reporting is **% of notional per trade** (gross price
  move + funding carry − fees). R is reported only for stop variants
  (1R = entry-to-stop distance); no-stop variants have no R basis. "Total net
  %" is the sum of per-trade net % on equal notional per trade — no
  compounding. `eod` = trades still open at data end, closed at last close —
  **zero in every variant**.
- **Statistics:** pooled `t` = mean(net%)/SE over per-trade net %, treating
  trades as iid. This is **optimistic**: signals cluster in time (one market
  episode triggers many symbols at once), so effective n is smaller than the
  trade count and t is overstated.

## Results — pooled across 10 symbols (365d)

`thr` = signal threshold; `exit` ∈ {24h, 72h, norm72}; `stop` ∈ {nostop,
atr2}. avgNet% is per-trade % of notional. Cells with n<20 are inconclusive
(flagged in stdout; none of the pooled cells below are <20 except noted).

| thr | exit | stop | n | win% | avgGross% | avgFund% | avgNet% | totNet% | avgNetR | totNetR | t |
|-----|------|------|---|------|-----------|----------|---------|---------|---------|---------|---|
| m1bp | 24h | atr2 | 433 | 36.0 | −0.420 | +0.005 | **−0.524** | −227.03 | −0.126 | −54.63 | −3.10 |
| m1bp | 24h | nostop | 344 | 53.2 | +0.206 | +0.012 | +0.108 | +37.26 | n/a | n/a | +0.54 |
| m1bp | 72h | atr2 | 390 | 24.1 | −0.834 | +0.002 | **−0.941** | −367.18 | −0.246 | −95.78 | −4.80 |
| m1bp | 72h | nostop | 241 | 55.2 | +0.205 | +0.014 | +0.109 | +26.30 | n/a | n/a | +0.27 |
| m1bp | norm72 | atr2 | 455 | 40.0 | −0.310 | +0.006 | **−0.414** | −188.33 | −0.144 | −65.66 | −3.42 |
| m1bp | norm72 | nostop | 379 | 50.4 | +0.093 | +0.012 | −0.005 | −1.76 | n/a | n/a | −0.03 |
| m2bp | 24h | atr2 | 109 | 32.1 | −0.584 | +0.011 | **−0.683** | −74.42 | −0.168 | −18.34 | −2.01 |
| m2bp | 24h | nostop | 91 | 51.6 | +0.385 | +0.022 | +0.297 | +27.02 | n/a | n/a | +0.76 |
| m2bp | 72h | atr2 | 98 | 21.4 | −0.711 | +0.013 | **−0.809** | −79.26 | −0.159 | −15.59 | −1.91 |
| m2bp | 72h | nostop | 71 | 62.0 | **+1.036** | +0.032 | **+0.958** | +68.03 | n/a | n/a | **+1.22** |
| m2bp | norm72 | atr2 | 108 | 41.7 | −0.153 | +0.012 | **−0.251** | −27.10 | −0.076 | −8.24 | −0.88 |
| m2bp | norm72 | nostop | 91 | 51.6 | +0.311 | +0.022 | +0.223 | +20.28 | n/a | n/a | +0.67 |
| m3bp | 24h | atr2 | 32 | 31.2 | −0.815 | +0.017 | **−0.908** | −29.05 | −0.137 | −4.38 | −1.17 |
| m3bp | 24h | nostop | 26 | 50.0 | +0.666 | +0.036 | +0.592 | +15.39 | n/a | n/a | +0.67 |
| m3bp | 72h | atr2 | 28 | 21.4 | −0.721 | +0.030 | **−0.801** | −22.42 | +0.018 | +0.51 | −0.79 |
| m3bp | 72h | nostop | 21 | 47.6 | +0.422 | +0.068 | +0.380 | +7.99 | n/a | n/a | +0.22 |
| m3bp | norm72 | atr2 | 29 | 41.4 | −0.411 | +0.026 | **−0.495** | −14.35 | −0.164 | −4.77 | −0.70 |
| m3bp | norm72 | nostop | 24 | 50.0 | +0.840 | +0.048 | +0.778 | +18.67 | n/a | n/a | +0.99 |

Bold = notable cells. Raw signal counts at −0.02%: SOL 33, AVAX 57, ADA 25,
XRP 13, ETH 4, LINK 2, BNB 1, DOGE 1, BTC 0, LTC 0 (136 prints; fewer trades
after the one-position rule).

## Per-symbol breakdown — best cell (m2bp / 72h / nostop, net % / n)

| symbol | net% | n |
|--------|------|---|
| AVAXUSDT | +9.96 | 27 |
| ADAUSDT | +19.31 | 14 |
| XRPUSDT | +18.80 | 7 |
| ETHUSDT | +28.85 | 4 |
| SOLUSDT | −6.14 | 15 |
| LINKUSDT | −0.72 | 2 |
| DOGEUSDT | +1.47 | 1 |
| BNBUSDT | −3.49 | 1 |

BTC and LTC produced zero −0.02% signals. Every symbol cell except AVAX has
n<20 → **inconclusive per the evidence standard**. The pooled +0.958%/trade
is carried by AVAX/ADA/XRP/ETH; SOL is negative.

Full 18-variant × 10-symbol breakdown is in the run log (stdout of
`scripts/funding_contrarian_backtest.py --days 365`).

## Findings

1. **The ATR(2×) stop destroys the strategy.** Every `atr2` cell is net
   negative (avgNet −0.25% to −0.94%/trade; avgNetR −0.076 to −0.246; the m1bp
   cells are significantly negative, t ≈ −2 to −5). The stop converts the
   post-print drift into realized stop-outs — consistent with the repo's
   repeated finding that tight/structural stops on mean-reversion entries eat
   the edge (same mechanism as `nwersiasf-backtest-2026-11-05.md`).
2. **No-stop variants show a positive gross drift concentrated in the deeper
   tail and in alts.** m2bp 72h nostop: +1.036% avg gross, +0.958% net,
   62% win — directionally consistent with the literature. But **t = +1.22**,
   far below significance, and it is the best of 18 variants (selection
   bias), with trades clustered in shared market episodes (effective n << 71)
   and per-symbol n almost all <20.
3. **Funding carry is negligible** (+0.01–0.07%/trade average). The measured
   effect is price drift, not the carry itself — so it does not rescue the
   thesis that "funding is a signal"; it is a coincident marker of crowded
   positioning.
4. **The −0.01% threshold does not work** (m1bp nostop cells ≈ +0.11%/trade,
   t ≤ +0.54; norm72 m1bp is −0.005%/trade). Matches Aijmr's asymmetry:
   only the deep negative tail carries any signal, and even there it is not
   established.
5. **m3bp (−0.03%) is too rare to conclude** (21–32 trades pooled, all cells
   n<35, per-symbol n ≤ 18).

## Verdict

**Inconclusive — weak positive drift, not a validated edge.** The best cell
(m2bp / 72h / no stop) nets +0.958% of notional per trade over 71 trades
(t = +1.22, best-of-18), concentrated in four alt symbols with n<20 each.
Every stopped variant is net negative. Per the evidence standard this does
**not** support deploying anything, and it does not meet the bar for even a
paper sleeve without a pre-registered forward test. If pursued, the honest
next step is a paper-tracked forward run of the single m2bp/72h/nostop spec
(no variant shopping) — but note it needs a stop-less risk wrapper this repo
does not currently have, and ~3 signals/week across the universe implies a
very thin trade flow.

## Caveats

- Single 365d window, no disjoint holdout; 2025-11→2026-11 includes one broad
  risk regime. A second disjoint year would be needed before any conclusion.
- t-stats treat trades as iid; cross-symbol/time clustering overstates
  significance (stated, not corrected).
- Mean returns are fat-tailed: a handful of large alt bounces likely drive
  the m2bp 72h cell; median per-trade net was not separately reported.
- Fills assume taker at the 1h bar open with 0.11% RT modeled cost; no
  queue/slippage-beyond-model analysis (same limitation as other harnesses).
- Funding income assumes the position is held through each settlement inside
  the hold; boundary prints exactly at entry/exit timestamps are excluded
  (conservative).
