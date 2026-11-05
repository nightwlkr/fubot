# BBRSI Backtest — 2026-11-05

Candidate strategy evaluated per the strategy-review pipeline: geraked/tradingview
`strategies/BBRSI.pine` (MIT), ported faithfully to `scripts/bbrsi_backtest.py`
(standalone; does not touch `backend/backtest.py` or live code).

## Method

- Fetch: `backend/backtest_data.fetch_klines` / `align_window` / `window_for_days`
  via `BinanceService.get_klines_window` — paginated, one `[start, end)` window,
  still-forming bars dropped. Window = last 45 days ending at last closed 5m bar.
- Indicators: SMA(20) basis; `dev = 2.0 * stdev(close,20)` with Pine `ta.stdev`
  biased (population, /n); RSI(3) Wilder/RMA seeded with SMA of first 3 changes.
- Entries at NEXT bar open (Pine `strategy.entry` default); one position per
  symbol; signals while in position ignored (pyramiding=1).
- SL = `bbBasis ∓ 2.5*bbDev` (Pine `slCoef=1.5` on band distance); TP =
  `fill ± 1.5*(fill − SL)` using the ACTUAL fill price, as Pine does.
- Exits: per-bar OHLC, SL checked before TP when both inside a bar's range;
  gap-through fills at the adverse open. `close_on_basis` variant exits at next
  bar open on `ta.crossover/crossunder(close, bbBasis)` — same fill convention
  as every other strategy order.
- Costs: `backend.core.cost_model.LIVE` round trip = 0.11% notional
  (0.045% taker fee + 0.01% slippage per side), converted to R via
  `round_trip_in_r` — identical math to `backend/backtest.py::fee_in_r`.

## Results — full 45d window, all 7 symbols

### 15m

| symbol | basis_exit trades | basis_exit netR | basis_exit expNetR | no_basis trades | no_basis netR | no_basis expNetR |
|---|---|---|---|---|---|---|
| APEUSDT | 107 | +1.45 | +0.014 | 31 | +2.53 | +0.082 |
| BNBUSDT | 100 | −14.62 | −0.146 | 41 | −4.01 | −0.098 |
| BTCUSDT | 76 | −13.86 | −0.182 | 35 | +5.63 | +0.161 |
| LINKUSDT | 110 | −5.20 | −0.047 | 19 | −0.04 | −0.002 |
| OPUSDT | 93 | +2.16 | +0.023 | 41 | +9.89 | +0.241 |
| SOLUSDT | 103 | −13.73 | −0.133 | 26 | −0.78 | −0.030 |
| UNIUSDT | 115 | −8.83 | −0.077 | 44 | +4.29 | +0.097 |
| **TOTAL** | **704** | **−52.62** | **−0.075** | **237** | **+17.51** | **+0.074** |

- basis_exit: win 64.5%, gross +12.68R, fees −65.30R (0.093R/trade), exits: 548 basis_cross / 146 sl / 9 tp / 1 sl_gap. Median stop 1.64%.
- no_basis_exit: win 46.4%, gross +36.40R, fees −18.90R (0.080R/trade), exits: 126 sl / 110 tp / 1 sl_gap. Median stop 1.95%.

### 1h

| symbol | basis_exit trades | basis_exit netR | basis_exit expNetR | no_basis trades | no_basis netR | no_basis expNetR |
|---|---|---|---|---|---|---|
| APEUSDT | 29 | −0.43 | −0.015 | 8 | +4.30 | +0.538 |
| BNBUSDT | 26 | +1.18 | +0.046 | 7 | +2.61 | +0.373 |
| BTCUSDT | 30 | −3.92 | −0.131 | 9 | −4.67 | −0.518 |
| LINKUSDT | 25 | −4.27 | −0.171 | 6 | +1.33 | +0.222 |
| OPUSDT | 24 | −0.86 | −0.036 | 15 | +2.07 | +0.138 |
| SOLUSDT | 28 | +0.32 | +0.011 | 10 | −2.88 | −0.288 |
| UNIUSDT | 26 | +3.57 | +0.137 | 8 | +1.84 | +0.230 |
| **TOTAL** | **188** | **−4.41** | **−0.023** | **63** | **+4.61** | **+0.073** |

- basis_exit: win 67.0%, gross +4.49R, fees −8.90R (0.047R/trade). Median stop 3.24%.
- no_basis_exit: win 44.4%, gross +7.00R, fees −2.39R (0.038R/trade). Median stop 3.41%.

### 5m (fee-stress)

| symbol | basis_exit trades | basis_exit netR | no_basis trades | no_basis netR |
|---|---|---|---|---|
| APEUSDT | 278 | −1.49 | 77 | −10.46 |
| BNBUSDT | 274 | −64.06 | 76 | −17.15 |
| BTCUSDT | 226 | −70.15 | 75 | −23.83 |
| LINKUSDT | 278 | −25.44 | 94 | +4.85 |
| OPUSDT | 282 | −24.10 | 78 | −15.92 |
| SOLUSDT | 298 | −37.82 | 63 | −4.99 |
| UNIUSDT | 301 | −29.79 | 89 | −10.63 |
| **TOTAL** | **1937** | **−252.85** (expNetR −0.131) | **552** | **−78.14** (expNetR −0.142) |

- basis_exit: win 65.2%, gross +66.62R, fees −319.46R (0.165R/trade). Median stop 0.91%.
- no_basis_exit: win 39.9%, gross −6.50R, fees −71.64R (0.130R/trade). Median stop 1.19%.

## Fee drag

Round-trip cost in R per trade rises as stop distance shrinks — the known
dominant drag. Average fee/trade: 15m 0.080–0.093R, 1h 0.038–0.047R,
5m 0.130–0.165R. On 5m the fee load exceeds gross edge in both variants
(gross −6.5R no-basis; +66.6R basis but −252.9R net). On 15m no-basis, fees
consume ~52% of gross (+36.4R → +17.5R).

## Stop-distance distribution (full window)

| variant | 15m median / p90 | 1h median / p90 | 5m median / p90 |
|---|---|---|---|
| no_basis_exit | 1.95% / 5.41% | 3.41% / 9.47% | 1.19% / 2.99% |
| basis_exit | 1.64% / 4.17% | 3.24% / 8.84% | 0.91% / 2.26% |

Median stops are ~1–3.4% — NOT in the 0.1–0.2% regime where fees would
structurally dominate. Fee drag is real but secondary to edge here; the
binding problem is that gross edge is thin/inconsistent, not that stops are
too tight.

## Train/holdout split (45d → train [−45d,−23d), holdout [−23d,0))

| cell | train netR / expNetR (n) | holdout netR / expNetR (n) |
|---|---|---|
| 15m no_basis_exit | −9.91 / −0.101 (98) | +21.11 / +0.137 (154) |
| 1h no_basis_exit | +8.46 / +0.256 (33) | −6.92 / −0.154 (45) |
| 15m basis_exit | −31.02 / −0.083 (372) | −22.13 / −0.067 (330) |
| 1h basis_exit | +0.33 / +0.003 (98) | −5.68 / −0.065 (87) |

The sign flips between disjoint halves in every cell — no stable edge in
either direction.

## Verdict

- **No net-positive cell survives scrutiny.** The best-looking cell
  (15m no_basis_exit, +17.51R / expNetR +0.074 over 237 trades) is
  (a) concentrated: OPUSDT +9.89, BTCUSDT +5.63, UNIUSDT +4.29 supply all
  the profit while BNBUSDT loses −4.01 and LINKUSDT/SOLUSDT are ~flat;
  (b) non-stationary: train half −0.101 expNetR vs holdout +0.137;
  (c) the Pine-published default (`closeBbBasis=true`) is net-negative on
  every timeframe tested — the profitable variant deviates from defaults.
- 5m is unambiguously dead (−252.9R / −78.1R net, fee-dominated).
- Kill criterion check: median stop distance is 0.9–3.4%, well above the
  0.1–0.2% fee-dominated regime — fees are not the primary killer; absent
  consistent gross edge is.
- **Not worth wiring into the live scanner.** BBRSI does not clear the bar
  the current strategy fails; it fails differently (no consistent gross
  edge rather than fee-dominated tight stops). If revisited, the only
  defensible next step would be a longer-window (90–180d) disjoint-slice
  study of the 15m no-basis variant before any live consideration.

## Reproduce

```
/usr/local/bin/python3.13 scripts/bbrsi_backtest.py --days 45 --interval 15m
/usr/local/bin/python3.13 scripts/bbrsi_backtest.py --days 45 --interval 1h
/usr/local/bin/python3.13 scripts/bbrsi_backtest.py --days 45 --interval 5m
# split: --days 23 (holdout) / --days 22 --end-days-ago 23 (train)
```
