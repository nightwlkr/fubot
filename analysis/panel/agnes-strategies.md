# Agnes — Strategy Mix & Validation Panel Review
## FUBOT Live Analysis | 2026-10-15

### 1. Top 3 Provable Findings

**Finding A — Backtest exit logic is fundamentally misaligned with live behavior.**
`backend/backtest.py:212-224` checks TP/SL only on **1h candle ranges**, but 3CANDLE signals fire on 5m patterns and fill at next 1h open. Live evidence: DOT trade MFE reached 7.47R but engine exited at ~4.15R (fixed 3R TP + BE trail). The backtest would have missed the entire runner profile — winners are systematically underestimated and losers overestimated (1h wicks hit stops that 5m price never touched). **Result: backtest expectancy is a lower-bound pessimistic bias, not a reliability guarantee.**

**Finding B — FVG expectancy is deeply negative on live data (−0.397R, n=5, 0W/5L).**
`backend/indicators/fvg.py:98-127`: FVG stop = `zone_min − atr_1h` (below the gap). On liquid crypto 1h, gaps are wide and ATR(14) is large → stop is 15.6% below entry on at least one trade, while TP is fixed 3R above. The risk-reward asymmetry guarantees failure unless win rate > 75%, which never occurs on mean-reverting gap fills. **This is not a tuning problem — the strategy geometry is inverted for this market.**

**Finding C — 3CANDLE is the only strategy with positive expectancy, but the sample is tiny (n=17, +0.133R/trade).**
`backend/indicators/three_candle.py:119-157`: 3CANDLE works because the structural sweep wick + 1×ATR floor produces tight stops (~0.4-0.8%) and the market naturally retraces into the zone. However, 17 trades is not enough to distinguish edge from variance. The live day's +0.012R overall expectancy is dragged down by FVG; removing FVG would raise it to ~+0.13R/trade.

---

### 2. Proposals

**Keep 3CANDLE, expand sample, add volatility filter:**
- Current rule: `three_candle.py:57-61` bullish sweep + `three_candle.py:87-90` bearish sweep, ATR floor 1×.
- Proposal: add `min_volatility` gate — skip if `atr_1h / entry_price < 0.003` (0.3%). Low-volatility sweeps get whipsawed; the ATR floor helps but doesn't exclude the setup.
- Evidence needed: ≥60 trades across ≥60 days on BTC+ETH+SOL before relying on expectancy.

**Drop FVG entirely.**
- Rule to cut: `signal_service.py:99-104` (FVG detection + evaluation path).
- Rationale: −0.397R/trade on 5 trades, zero wins, geometric mismatch (wide stop + fixed 3R target). No amount of parameter tuning fixes the inverted risk profile.
- Replacement hypothesis: **1h liquidity-sweep continuation** — same sweep logic as 3CANDLE but on 1h timeframe, entering on pullback to the sweep candle's midpoint, stop at sweep extreme + 1×1hATR. Hypothesis: fewer but higher-quality signals, aligned with the structural-trend bias already computed.
- Evidence needed: 60-day replay on BTC, ETH showing ≥+0.10R/trade, n≥30.

**Do not build CHoCH — keep dormant but document why.**
- `choch.py:34-129` requires two sequential pivot breaks — on 15m with `left_bars=5, right_bars=5`, this fires roughly once per week per symbol. Zero trades in 23 live trades is statistically consistent with the design, not a bug.
- If built later: require `rr_ratio ≥ 4.0` (wider structure = wider risk, need bigger target), and only in STRONG trend bias.

**Build: Volatility-Regime Filter (additive, not standalone).**
- Hypothesis: 3CANDLE expectancy drops in high-vol regimes (ATR% > 2% on 1h) because sweeps are noise, not liquidity grabs.
- Rule: skip 3CANDLE signals when `atr_1h / close > 0.02`.
- Evidence: walk-forward split — compare 3CANDLE expectancy in low vs high ATR% buckets across 60 days.

---

### 3. Validation Standard (must-fix for any future backtest)

A trustworthy backtest for this bot must:
1. **Simulate at 5m or 1m path**, not 1h — entries are 5m-pattern-driven, exits are price-based (BE trail, TP at 3R). 1h-range checking misses intrabar hits.
2. **No lookahead**: signal at index `i` fills at `candles[i+1].open`; all subsequent candles must be from the future relative to signal time.
3. **Per-candle exit check**: at every 5m candle after entry, check if `high >= TP` or `low <= SL` (for LONG); same mirror for SHORT. Track BE-trail trigger at +0.5R.
4. **Fees + funding**: 0.02% maker / 0.04% taker per side + funding every 8h (estimate 0.01%/cycle for crypto futures). Net drain ≈ 0.06% per round trip.
5. **Walk-forward split**: train on days 1–N, validate on days N+1–N+M. Minimum 60 days of history, minimum 30 closed trades per strategy per symbol to claim statistical significance (bootstrap 95% CI on expectancy should not cross zero).
6. **Multiple-comparison caution**: testing 3 strategies × 9 symbols × multiple filter configs inflates false-positive rate. Apply Bonferroni correction or require out-of-sample replication.

---

### 4. What I Would Cut Today

**Cut FVG immediately.** It is the only strategy with negative expectancy on live data (−0.397R, 0/5 wins). The stop geometry (`zone_min − atr_1h`) produces asymmetric risk that no filter can fix — the gap is the reward zone, but the stop sits beyond it, meaning the trade risks more than it can plausibly earn before the gap fills. Removing FVG raises the bot's overall expectancy from +0.012R to ~+0.13R/trade (3CANDLE-only) with zero additional risk.

---

### Other Panelists — What I Expect Them to Get Wrong

1. **Entry Quality**: Will over-index on R:R ratio as a quality signal. R:R ≥ 3 is a gate, not a guarantee — FVG passes it but loses every time. The real signal is *directional conviction* (volume profile, trend strength), not reward distance.
2. **Exit Design**: Will defend the fixed 3R TP as discipline. But DOT showed 7.47R MFE — the engine leaves ~3.3R on the table on winners. A trailing stop (beyond BE) or partial-close at 3R with runner would materially improve expectancy. The question is whether the improvement offsets the increase in average hold time (margin drag).
3. **Risk/Portfolio**: Will recommend adding more symbols or raising leverage. With max_positions=5 and −3R daily limit, the constraint is *signal quality*, not position count. Adding weak strategies (FVG) or loose filters raises trade frequency without raising edge — the current bottleneck is the 3CANDLE signal pipeline, not capital allocation.
