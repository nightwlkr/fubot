# Venue comparison: Binance vs Poloniex for a Donchian 1d trend strategy — 2026-11-05

**Question:** user is deciding between Binance and Poloniex USDT-M futures for a Donchian 1d ensemble breakout strategy (~15 perps, ~monthly trades, low turnover). User holds Poloniex VIP2 (futures maker 0.016% / taker 0.050%) and ~$3,600 TRX staked on Poloniex, and states low trust in Binance.

**Method:** (1) live probes of the Poloniex V3 public API (`https://api.poloniex.com`, no auth); (2) `scripts/donchian_backtest_poloniex.py` — a copy of `scripts/donchian_backtest.py` with only the data source, symbol set and cost model changed (Poloniex taker 0.05% + 0.01% slippage = 0.12% RT vs Binance modeled 0.11%); (3) a Binance control re-run on the *same 8 symbols* from the existing `/tmp/donchian_klines` cache so venue and universe effects are separated; (4) public reporting for venue-risk facts. Nothing was committed; no production system touched.

## 1. Poloniex API capability check (gates everything — PASSED)

Base URL `https://api.poloniex.com`, V3 API, symbol form `BTC_USDT_PERP`. Verified live on 2026-11-05.

- **Universe:** `/v3/market/tickers` returns **31 USDT perps** (plus equity/commodity perps like NVDA, XAU, CL). Of the 15 Binance-universe names: **present** BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, LTC, AVAX, SUI, UNI (12). **Absent:** DOT, NEAR, OP. ADA/LINK/SUI/UNI perps were (re)listed 2026-06-17…2026-08-06 per `/v3/market/instruments` `tradableStartTime` — under 4 months of history, useless for a 2y validation.
- **History depth (DAY_1 candles, paginated backward on `eTime`, 500/page):** BTC from 2020-08-04 (~2,289 bars), ETH 2020-09-02, LTC 2021-03-04, DOGE 2021-03-18, BNB 2021-05-07, SOL 2021-09-02, AVAX 2021-11-18, XRP 2022-09-30. **8 symbols have ≥ the full 730d + 70-bar warmup window.** HOUR_4 confirmed working. Endpoint: `GET /v3/market/candles?symbol=…&interval={DAY_1,HOUR_4,…}&sTime=…&eTime=…&limit=500`; row layout `[low, high, open, close, quoteVol, baseVol, ?, openTime, closeTime]` (field order verified against ccxt `parse_ohlcv`). Interval enum is `MINUTE_*/HOUR_*/DAY_*/WEEK_*/MONTH_*`, NOT `1d`/`4h` (those return error 24102).
- **Orders:** `POST /v3/trade/order` supports `type` ∈ {MARKET, LIMIT, **LIMIT_MAKER** (post-only)}, `reduceOnly`, `clOrdId` (client order id), `timeInForce` GTC/IOC/FOK, `posSide`, `mgnMode`, `stpMode`. Confirmed via official Python SDK (`polosdk/futures/rest/private.py`) and ccxt.
- **Funding:** `GET /v3/market/fundingRate` (current) and `/v3/market/fundingRate/history` both live (BTC fR 0.0001, 8h cadence fields `fT`/`nFR`/`nFT`).
- **Rate limits:** market-data endpoints are IP-based; ccxt cost table puts candles at 10 units vs 2/3 for tickers/orderBook — generous for a daily-bar strategy polling a handful of symbols. Trade endpoints are per-UID tiered (General user: place order 50, cancel 100 per window per docs table).
- **Insurance fund:** `GET /v3/market/insurance` live — USDT insurance fund ≈ **$4.67M** (snapshot 2026-11-05).

**Verdict: Poloniex can host and validate this strategy.** Klines are sufficient (≥4y on 8 symbols); missing DOT/NEAR/OP and the four 2026 listings shrink the universe from 15 to 8 deep-history names.

## 2. Donchian 1d backtest on Poloniex data

Same spec as `analysis/donchian-trend-backtest-2026-11-05.md`: lookbacks {10,20,55}, ≥2-of-3 conviction, next-open fills, 20-bar initial stop, 10-bar ratchet trail, no TP, `--min-risk-pct 0.5`, same `sanitize_candles` corrupt-bar rule. Poloniex cost = 0.12% RT (Binance control below uses its own 0.11%).

| Window | Venue / universe | Trades | Win% | Net R | expNetR | PF | MaxDD (R) |
|---|---|---|---|---|---|---|---|
| last ~730d | **Poloniex, 8 syms** | 181 | 37.0 | +26.73 | **+0.148** | 1.52 | −14.59 |
| last ~730d | Binance, same 8 syms (control) | 139 | 43.9 | +21.80 | **+0.157** | 1.72 | −8.15 |
| last ~730d | Binance, full 15 syms (published) | 295 | 41.4 | +26.72 | +0.091 | 1.40 | −20.09 |
| prior 730d (ending 730d ago) | **Poloniex, 8 syms** | 173 | 35.8 | +21.54 | **+0.125** | 1.45 | −16.07 |
| prior 730d | Binance, same 8 syms (control) | 135 | 38.5 | +19.04 | **+0.141** | 1.58 | −14.24 |
| prior 730d | Binance, full 15 syms (published) | 255 | 42.4 | +39.92 | +0.157 | 1.70 | −21.08 |

Per-symbol Poloniex recent window: XRP +1.018 expNetR (n=20, 20.36R of the 26.73 net — extreme tail concentration), DOGE +0.218 (n=18, inconclusive), ETH +0.093; BTC −0.017, SOL −0.042, AVAX +0.018, BNB +0.023, LTC +0.037. Prior window: AVAX +0.483, SOL +0.411, DOGE +0.356 carry it; XRP −0.355 on only 13 trades (listed 2022-09-30, so ~2 months of warmup missing — inconclusive). **The Poloniex-vs-Binance expNetR gap on identical symbols is ≤0.016 in both windows — venue/data differences do not change the strategy verdict; the edge (such as it is) lives in the signal, not the feed.**

**Data-quality note:** Poloniex and Binance daily closes agree to ~0.04% median but diverge up to ~12% on individual days — spot-checked the worst (BTC 2025-03-02: Binance close 84,156 vs Poloniex 94,244). Several of the largest divergences are days where the *Binance* bar looks like a known bad tick (e.g. Binance 2025-02-10 `high=119252` vs ~97–100k everywhere else); Poloniex is the thinner venue but its daily bars did not show obvious corruption in this sample (1 dropped bar across 8 symbols vs 9 on Binance). Trade counts differ (181 vs 139) because entry/exit timing shifts on slightly different OHLC — expected.

## 3. Venue risk comparison

### Binance
- **2019-05:** hot-wallet breach, ~7,000 BTC (~$40M) via API-key/2FA phishing; covered in full by SAFU; deposits/withdrawals frozen ~1 week for audit. ([Binance blog recap](https://www.binance.com/en/blog/all/security-incident-recap-336904059293999104), [Decrypt](https://decrypt.co/6930/binance-hack-security-breach))
- **2022-11:** did NOT halt withdrawals during the FTX bank run — processed them; ~$6B left in 72h. Monthly Merkle-tree proof-of-reserves published since Nov 2022. ([The Verge](https://www.theverge.com/2022/11/11/23453560/ftx-bankruptcy-coinbase-binance-crypto-proof-of-reserves), [CoinGecko](https://www.coingecko.com/research/publications/exchange-withdrawal-ftx-collapse))
- **2026-10:** a further security incident is disclosed on the same Binance blog — "Losses … are covered by SAFU and company funds, and user funds are safe. An updated Proof of Reserves will be published once remediation is complete" (update dated 2026-10-30). Recent, unresolved optics; no user-fund loss claimed.
- Scale: largest exchange by volume; deep liquidity; the user's existing FUBOT deployment already runs on it.

### Poloniex
- **2014-03:** ~12.3% of BTC stolen via a withdrawal race-condition bug; exchange was undercollateralized and socialized the loss across balances; nearly killed the exchange. ([BitcoinTalk postmortem](https://bitcointalk.org/index.php?topic=499580), [CoinDesk](https://www.coindesk.com/markets/2014/03/05/poloniex-loses-123-of-its-bitcoins-in-latest-bitcoin-exchange-hack))
- **2023-11-10:** hot-wallet exploit, ~$126–132M across ETH/TRON/BTC (private-key compromise per CertiK/PeckShield); withdrawals frozen ~7 weeks, resumed gradually through Jan 2024; Sun/HTX covered losses and airdropped users. Part of a ~$240M cluster hitting HTX and the Heco bridge 12 days later — shared-infrastructure concentration risk. ([Decrypt](https://decrypt.co/205465/justin-sun-owned-crypto-exchange-poloniex-hacked-60m), [CertiK](https://www.certik.com/blog/poloniex-incident-analysis), [CoinDesk](https://www.coindesk.com/business/2023/12/13/htx-poloniex-assets-are-100-safe-says-justin-sun-after-200m-hack), [DL News](https://www.dlnews.com/articles/people-culture/justin-sun-promises-epic-airdrop-poloniex-htx-user/))
- **Now:** HTX-owned; monthly Merkle-tree PoR live — latest snapshot 2026-10-01 shows **100.47% total reserve ratio** (per-asset 100.0–104.6%). ([poloniex.com PoR page](https://www.poloniex.com/topic/security/proof-of-reserves)) USDT insurance fund ~$4.67M. Caveat reported by Protos (2026-01): Poloniex PoR covers only ~11,100 BTC while a large WBTC-TRC20 position sits at HTX — PoR scope is narrower than HTX's.
- **Availability:** Armenia is NOT on Poloniex's prohibited list for spot or futures ([prohibited countries](https://poloniex.zendesk.com/hc/en-us/articles/4404183719191-Prohibited-Countries)). Binance Futures availability in Armenia is murkier — community reports list Armenia among restricted futures jurisdictions (Aug 2025), though Binance's own country selector shows Armenia eligible for some promotions; **verify in-app before committing**.

### Honest assessment for a $3–5k account in 2026

Counterparty risk here is not about hack *count* (both have been hacked; Binance's was smaller and reimbursed from SAFU, Poloniex's 2023 loss was ~3× larger and required an HTX bailout + weeks of frozen withdrawals). What matters at this size:

- **Binance is the stronger counterparty on every structural metric**: ~10× the volume, monthly PoR for 3 years, SAFU + deep insurance fund, and — decisively for this user — the existing bot, monitoring, and forensic tooling already run against it. The "I don't trust Binance" concern is legitimate but the 2022 bank-run test is the relevant one and Binance passed it (honored withdrawals). The Oct 2026 incident is a fresh black eye but claims full user-fund coverage.
- **Poloniex is viable but weaker**: thinner books (wider spreads/slippage on alts — matters little for monthly Donchian entries but matters for stop-market exits in a crash), a 2023 bailout history, HTX concentration risk, and PoR that covers spot balances — futures account treatment is less transparent. The $3,600 TRX stake is itself a concentration bet on the same venue.
- **Fees are a non-factor for this strategy.** ~20 round trips/yr × $3,000 notional: Binance taker 0.045%+0.01% slip vs Poloniex taker 0.050%+0.01% slip → delta ≈ **$6/year**. Even at 100 trades/yr it's ~$30/yr. Venue choice should be made on counterparty risk, liquidity and operational fit — not fees.

**Bottom line:** for a small account, either venue's hack risk is dominated by "exchange becomes insolvent/unusable," and Binance is better capitalized and more proven under stress. Poloniex is acceptable if the user specifically wants the VIP2/TRX-stake economics or Binance access is actually restricted in Armenia — but that restriction needs in-app verification first, because if Binance futures are genuinely unavailable in Armenia, the decision is already made for them.

## Caveats

- Poloniex backtest uses only 8 of the 15 planned symbols (DOT/NEAR/OP don't exist; ADA/LINK/SUI/UNI lack history) — pooled numbers are not directly comparable to the 15-symbol published runs; the 8-symbol Binance control is the apples-to-apples comparison.
- Poloniex XRP prior-window cell is inconclusive (13 trades, missing warmup).
- Both backtests share the known harness caveats from `donchian-trend-backtest-2026-11-05.md` (tail-driven expectancy, OHLC-only fills, no portfolio slots).
- Fee figures are list prices; realized Poloniex fees depend on the user's actual VIP tier and BNB-style discounts (TRX staking may add fee discounts not modeled).
