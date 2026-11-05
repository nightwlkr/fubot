# Funding-rate & basis strategies — feasibility research (2026-11-05)

Triggered by the conclusion that all price-prediction variants of the
3CANDLE family are falsified (see `three-candle-higher-tf-2026-11-05.md`,
`exit-param-sweep-2026-11-05.md`). Question: is there a structural
(non-predictive) carry return source on Binance USDS-M at ~$200 account
size?

Method: public Binance USDⓈ-M futures endpoints only (no auth), read-only.
Sample sizes stated per figure.

## Findings

### Quarterly delivery basis (the only viable variant)

Binance lists quarterly USDT contracts: `BTCUSDT_261225`,
`ETHUSDT_261225`, `BTCUSDT_270326`, `ETHUSDT_270326` (exchangeInfo,
`contractType` CURRENT/NEXT_QUARTER, status TRADING — verified
2026-11-05).

Spot-check of annualized basis (quarterly price vs perp mark, single
timestamp — not a time series):

| Contract | Days to expiry | Basis | Annualized |
|---|---|---|---|
| BTCUSDT_261225 | 49 | +0.836% | ~6.2% APR |
| ETHUSDT_261225 | 49 | +0.600% | ~4.5% APR |
| BTCUSDT_270326 | 140 | +2.199% | ~5.7% APR |
| ETHUSDT_270326 | 140 | +1.767% | ~4.6% APR |

Cash-and-carry = long quarterly + short perp (or vice versa), converges
at expiry. Gross ~4.5–6% APR on notional. Costs: two legs, one entry +
convergence (no exit trade needed if held to settlement, but roll/close
typically taker ~0.045% × 2 legs × 2 sides ≈ 0.18% one-time) → net
~4–5% APR on deployed notional. On a $200 account fully committed:
**~$8–10/year**. Positive expectancy, structural, uncorrelated with the
price-prediction book — but trivial in absolute terms and it consumes
the entire account as idle collateral with leg-risk (margin calls if the
perp leg moves against you before convergence; isolated margin mandatory).

### Funding carry (long spot + short perp)

Rejected on infrastructure, not economics: FUBOT is futures-only
(`PROD_BASE = fapi.binance.com`, no spot client, API key almost
certainly lacks spot permission). 30-day funding history (n=90 prints
per symbol): BTCUSDT avg +0.0052%/8h (~5.7% APR), ETHUSDT +0.0030%
(~3.2%), SOLUSDT +0.0048% (~5.3%) — and funding goes negative in bear
regimes, where the hedge would require shorting spot (impossible
futures-only). Net after costs on $150 max feasible notional: low
single-digit $/year.

### Funding as a directional signal

Rejected on evidence: edgeornot's 522-coin / 1.8M-payment test found
shorting extreme positive funding avg **−0.33% after costs** (squeeze
tail); long extreme negative funding +0.07% decaying to 0.00%
out-of-sample. At our 0.11% round-trip cost and 1% sizing, nothing
survives.

### Cross-venue basis arb

Rejected on operational complexity: requires a second exchange account
+ API integration + transfer latency; scanner APRs are attractive on
paper but payoff at $200 is noise vs. the operational risk.

## Verdict

The quarterly basis trade is the **only** structural, positive-expectancy
candidate found in this entire research program — and it earns ~$8–10/yr
on the current account. It is a separate sleeve with a different risk
model (no stop, margin-managed, convergence thesis), not a patch to the
3CANDLE engine. Recommendation: **do not build it into FUBOT's risk
engine**; if pursued at all, it belongs in a standalone paper-tracked
experiment. Funding-as-signal and cross-venue arb are dead ends.

## Caveats

- Basis APRs are single-timestamp snapshots, not a historical series —
  realized carry varies with regime; a proper backtest needs quarterly
  klines + funding history over 6–12 months before any deploy decision.
- Quarterly contracts have wide spreads and lower liquidity than perps;
  modeled taker fees may understate real cost.
- $200 account may not support simultaneous perp+quarterly margin
  comfortably at meaningful size.
