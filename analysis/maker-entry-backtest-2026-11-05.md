# Maker-Edge (post-only) Entry Backtest — 2026-11-05

**Question**: live entries try GTX post-only at the zone edge but fall back to
GTC taker on reject (`backend/services/binance_service.py:672-726`,
`strategy_service.py:377-396`). Removing the fallback saves ~0.027% of notional
on the entry side — does that fee saving survive the fills a resting edge order
forfeits?

**Harness change**: `backend/backtest.py` gained `backtest_entry_mode`
(`"midpoint"` default | `"maker_edge"`) plus `fill_at_edge()`. Maker-edge
semantics: entry price = zone edge (LONG → `zone_min`, SHORT → `zone_max`),
fill only when a subsequent bar's range trades through the edge by
`backtest_entry_penetration_bps` (0 = touch), unfilled inside the existing
`signal_max_age_minutes` window counts `no_maker_fill`. Filled trades are
charged `CostModel.for_fill_mode("hybrid")` (maker entry + taker exit,
0.083% round trip vs 0.110% taker/taker). The pre-fill filter gate uses the
edge price in maker mode (the price the order actually rests at). CLI:
`scripts/backtest.py --entry-mode maker_edge`.

**Window/config**: identical to `backtest-45d-2026-11-05.md` — last 45 days,
7 symbols (APE, BNB, BTC, LINK, OP, SOL, UNI), 3CANDLE only, atr_stop_mult 1.5,
min_risk_pct 0.005, rr_min 3.0, MODERATE, neutral allowed, no volume filter,
regime filter on, touch-fill midpoint baseline re-run in the same process on
the same fetched candles (published D2 expNetR was -0.025; this re-run gives
-0.027 — same window family, minor fetch-boundary difference).

## Results

| variant | signals | closed | grossR | netR | expNetR | fill rate* |
|---------|---------|--------|--------|------|---------|------------|
| midpoint (taker) | 452 | 451 | +29.71 | **-12.23** | **-0.027** | 75.0% (452/603) |
| maker_edge 0bp | 446 | 445 | +41.72 | **+9.60** | **+0.022** | 68.6% (446/650) |
| maker_edge 1bp | 430 | 429 | +31.54 | **+0.84** | **+0.002** | 64.9% (430/663) |

\* fill rate = signals reaching the fill attempt that filled
(signals / (signals + no_fill/no_maker_fill); no `rr_at_fill` skips occurred).

### Per-symbol netR / expNetR

| symbol | midpoint netR (exp) | maker0 netR (exp) | maker1bp netR (exp) |
|--------|--------------------:|------------------:|--------------------:|
| APEUSDT | -2.99 (-0.041) | +4.99 (+0.071) | +2.14 (+0.033) |
| BNBUSDT | -12.80 (-0.229) | -2.52 (-0.048) | -4.17 (-0.080) |
| BTCUSDT | -11.19 (-0.200) | -9.37 (-0.199) | -8.79 (-0.204) |
| LINKUSDT | +9.50 (+0.117) | +8.22 (+0.101) | +5.83 (+0.075) |
| OPUSDT | -2.67 (-0.047) | +0.59 (+0.011) | -0.67 (-0.013) |
| SOLUSDT | -0.29 (-0.004) | +1.40 (+0.017) | +0.21 (+0.003) |
| UNIUSDT | +8.21 (+0.161) | +6.29 (+0.105) | +6.29 (+0.105) |

Fee drag per closed trade (netR − grossR, averaged): midpoint 0.093R,
maker0 0.072R, maker1bp 0.072R — the hybrid model saves ~0.021R/trade, close to
the predicted 0.027% notional saving at typical stop distances.

Skip deltas: `no_fill` 151 (midpoint) → `no_maker_fill` 204 (maker0) → 233
(maker1bp); `min_risk` rejections rose 42 → 68 → 75 because the edge price
changes the pre-fill risk distance.

## Verdict

**The fee saving does not robustly survive.** At 0bp touch-fill the maker-edge
variant looks better (+0.022 vs -0.027 expNetR), but two caveats deflate that:

1. **The gain is not the fee saving.** Gross R rose +12.0R (+0.027 expR) —
   edge fills get a better entry price than the midpoint and the edge-priced
   filter gate admits a slightly different candidate set. The pure fee effect
   is only ~0.021R/trade. Most of the headline improvement is entry-price
   selection, which the touch-fill model flatters.
2. **At 1bp trade-through the edge evaporates**: expNetR +0.002, netR +0.84 —
   statistically indistinguishable from the midpoint baseline's -0.027 given
   per-trade SD ≈ 1R and n ≈ 430. OHLC touch-fill cannot prove queue position
   on a resting post-only order; 0bp is the optimistic bound and 1bp the
   conservative one, and the truth for a far-edge GTX order likely sits
   between — i.e. near zero net benefit.

**Recommendation**: do NOT remove the GTC fallback on this evidence. The
maker-edge variant is not net-negative, but its apparent edge is fill-model
optimism plus entry-price selection, not fee savings; a single 45d window
with no holdout cannot separate +0.002 from -0.027. If pursued, it needs a
forward paper run of GTX-at-edge without fallback (fill-rate telemetry exists
in the live order logs) or a queue-aware fill model.

## Reproduce

```
PYTHONPATH=. python3.13 scripts/backtest.py --symbols <7 symbols> --days 45 \
  --three-candle --no-fvg --no-choch --no-obfvg \
  --atr-stop-mult 1.5 --min-risk-pct 0.005 --rr-min 3.0 \
  --strength MODERATE [--entry-mode maker_edge] [--entry-penetration-bps 1]
```
