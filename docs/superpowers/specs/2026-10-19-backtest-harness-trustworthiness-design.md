# Backtest harness trustworthiness

**Date:** 2026-10-19
**Status:** Approved
**Scope:** Research tooling only. No live trading changes, no bot changes, no production settings.

## Problem

`backend/backtest.py` (the production harness behind `scripts/backtest.py`) has **five**
defects that make its output untrustworthy. It reports gross expectancy on 3.5 days
of 5m data, with the trend filter reading a different month than the entries it filters.

### 1. The 1000-bar cap truncates 5m data to 3.5 days

```python
# backend/backtest.py:281
limit = min(days * 24, 1000)
k5m = await binance.get_klines(symbol, "5m", limit)
```

`--days 45` requests 1080 bars; Binance caps a single page at 1000. The same `limit`
is applied to all three intervals, so 5m — the execution series for 3CANDLE, the only
profitable strategy — is **always** ~3.47 days regardless of `--days`. This is the
documented "`--days 45` yields ~3.5 days" caveat that made the early OBFVG result
unvalidated.

### 2. No fee modelling at all

`grep` for fee / slippage / commission / net over `backend/backtest.py` returns nothing.
Expectancy is **gross**. Panel cycle 1 concluded *"no tested config is net-positive after
0.07% fees; fee drag, not exit geometry, is the binding constraint"* — so this harness
rates strategies as profitable that lose money.

### 3. Multi-timeframe alignment is broken

With `limit=1000` applied to all three series:

```
1h  : 1000 bars = 41.7 days
5m  : 1000 bars =  3.5 days
15m : 1000 bars = 10.4 days
```

and alignment maps them **1:1 by index**:

```python
k5m_u  = k5m[:  len(k5m)  * (at_idx + 1) // len(k1h)] or k5m
k15m_u = k15m[: len(k15m) * (at_idx + 1) // len(k1h)] or k15m
```

1h bar 500 (hour 500) aligns to 5m bar 500 (hour 41.7). The trend filter reads data
~40 days older than the entries it is filtering.

### 4. Entries fill on the wrong series

```python
fill = k1h[i + 1]["open"]     # 1h candle open
entry_idx = i + 1             # ...but 3CANDLE signals come from 5m, CHoCH from 15m
```

### 5. The fill model does not match live

Live (`StrategyService._enter_with_limit`) places a LIMIT at the entry-zone **midpoint**,
fills within a signal-age window, then re-validates R:R at the actual fill and can skip.
The harness models none of this.

## Considered and rejected

**Promoting the panel harness instead of fixing this one.** `analysis/panel/cycle1-validate.py`
already has paginated fetch, window pinning, fee modelling and lookahead-free ATR — but it
is env-var driven, has no CLI, and is not the default entry point. Rejected: it would
leave two harnesses with the broken one still reachable.

**Adding new strategies first.** The user's original request. Rejected in favour of
fixing the measuring instrument: any strategy judged on 3.5 days of data with no fee
model and a mismatched fill produces a number that cannot be acted on.

**Adding a persistent kline cache.** Worthwhile but separable; YAGNI until pagination
proves slow in practice.

## Design

### 1. Data layer — one wall-clock window, paginated

Port `analysis/panel/mimo_entry_replay.py::fetch_klines` (paginated via
`startTime`/`endTime`) into `backend/backtest.py`. All three intervals are fetched for
one explicit `[start, end]` and the still-forming last candle of each is dropped.

Series are then joined on `open_time`, never by index arithmetic. This kills defects 1
and 3 together: identical spans make index alignment correct by construction.

### 2. Fill model — mirror live

Replace the `k1h[i + 1]["open"]` fill with the live rule:

- a signal yields an entry **zone**, not a point
- fill = first 5m bar whose range touches the zone **midpoint**, within
  `signal_max_age_minutes` (the same live setting, default 60)
- **R:R re-validated at the actual fill price** against `rr_min` (3.0); skip the
  signal if the fill's R:R falls below it
- skipped and unfilled signals are counted, never silently dropped

Kills defects 4 and 5.

### 3. Cost model — report net

`FEE_RT = 0.0007` (entry maker 0.02% + exit taker 0.05%), matching panel cycle 1.
Every result reports `sum_r` **and** `net_r`, with `net_r` identified as the number to
trust. Kills defect 2.

### 4. Lookahead safety — per-index ATR series

Compute ATR from `candles[0..i]` only, replacing `calculate_atr(k1h_u, 14)` on a slice.

### 5. Acceptance criteria

Each defect gets a test that **fails if the defect returns**:

| Test | Fails when |
|---|---|
| MTF spans agree | 1h/5m/15m windows differ by more than one bar |
| Fee floor | any strategy reports positive `net_r` while gross is negative |
| Data volume | 5m bars for a 45d window fall below a floor (catches the 1000-cap regression) |
| Fill series | a fill is taken from a different interval than its signal |
| Lookahead detection | a deliberately-lookaheaded ATR variant yields **identical** numbers |

The last is the important one: it proves the lookahead test can actually detect
lookahead, which is precisely the bug that inflated OB+FVG expectancy from +0.315R
to +0.402R.

## Invariants

1. All three intervals span the **same** wall-clock window to within one bar.
2. `net_r <= gross_r` always; costs can only reduce.
3. A trade is filled on the **same interval** its signal was detected on.
4. Every input to bar `i`'s decision uses only candles `0..i`.
5. A 45-day window yields at least 12,000 5m bars per symbol, or the run fails loudly.
6. Skipped and unfilled signals are counted, not dropped.

## Out of scope

- Adding any new strategy (the next spec)
- Porting anything from TradingView (the spec after that)
- Any change to live trading, the bot, or production settings
- A persistent kline cache