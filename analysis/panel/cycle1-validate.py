#!/usr/bin/env python3
"""
fubot-cycle1-validation.py — 5m-path, no-lookahead validation for DECISION.md
(Strategy Review Cycle 1: D2 stop metric, D3 runner-trail, D4 sizing).

Reuses the panelist MiMo's signal detection (analysis/panel/mimo-entry-replay.py,
which mirrors backend/services/signal_service.py:91-170 exactly) and replaces
the outcome layer with an ENGINE-EXACT exit simulation on the 5m path:

  - stop-before-target inside a candle that touches both (conservative, stated)
  - entry = zone MIDPOINT (the live LIMIT level) filled at the next 5m open if
    price trades back into the zone within `SIGNAL_MAX_AGE` (60m live); the
    ±1% zone-void gate and gate-4b stop-breach are applied as in production
  - EXITS evaluated in engine order (position_manager._rest_pass loop):
      1. TP hit                       -> exit TP (fixed target, current design)
      2. runner-trail (D3): arm when favor >= ARM_R, then when
         favor <= peak - TRAIL_R      -> exit TRAIL (one-way, never lowers stop)
      3. BE trail (be_trail_trigger_r): favor >= BE_R -> stop := entry (0R)
      4. structural SL                 -> exit SL
      5. horizon (240m)                 -> mark to last close (TIMEOUT)
  - fees: taker 0.05% per side (entry maker 0.02% — limit), i.e. 0.07% round
    trip by default; stated in the output. R is the signal's OWN planned risk.

Configs (env-togglable):
  STOP_METRIC = 1h | 5m     -> D2 (ATR source for the 3CANDLE stop floor)
  ARM_R / TRAIL_R / BE_R    -> D3 (0/0 disables the runner; BE_R=0.5 today)
  SIGNAL_MAX_AGE            -> fill deadline (60m = live today)

Read-only: public Binance REST only. No DB, no orders, no settings changes.
"""
import json
import math
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

# exact signal-detection mirror from the MiMo panel artifact
sys.path.insert(0, os.path.join(REPO, "analysis", "panel"))
import mimo_entry_replay as mirror  # noqa: F401                                   # noqa: E402

from backend.indicators.atr import calculate_atr                     # noqa: E402

DAYS = int(os.environ.get("REPLAY_DAYS", "30"))
STOP_METRIC = os.environ.get("STOP_METRIC", "5m")                    # "1h" | "5m"
BE_R = float(os.environ.get("BE_R", "0.5"))
ARM_R = float(os.environ.get("ARM_R", "0"))
TRAIL_R = float(os.environ.get("TRAIL_R", "0.75"))
SIGNAL_MAX_AGE_S = int(os.environ.get("SIGNAL_MAX_AGE", "3600"))
HORIZON_MIN = int(os.environ.get("HORIZON_MIN", "240"))
FEE_RT = float(os.environ.get("FEE_RT", "0.0007"))                   # 0.07% round trip
NO_FEES = os.environ.get("NO_FEES", "0") == "1"
TP_MULT = float(os.environ.get("TP_MULT", "3.0"))                    # rr_min used by the evaluator

SYM = [s for s in mirror.SYMBOLS if s != "MATICUSDT"]                # delisted (404)


def simulate(k5, idx_start, direction, entry, stop, tp, risk):
    """Walk the 5m path from the entry bar; engine-ordered exits.

    Everything runs in PROFIT-R space (positive = favourable for the actual
    direction), so LONG and SHORT share one code path:
      best_bar  = highest profit-R touched by the bar
      worst_bar = lowest  profit-R touched by the bar
    The stop level `s` lives in the same space: structural = -1.0, BE = 0.0,
    runner = peak - TRAIL_R (ratchets up, one-way).

    Per-bar conservative ordering (intrabar sequence is unknowable from 5m
    bars, so the WORST case for the strategy is assumed):
      1. stop touched (worst_bar <= s, with s frozen from BEFORE this bar)
         -> exit at s.  Label: SL (s <= -1), BE (s == 0), TRAIL (s > 0)
      2. else TP touched (best_bar >= tp_r) -> exit at tp_r
      3. else update state: peak, BE trigger, runner-trail stop
    A bar that arms BE/runner and then retraces INTO that level is therefore
    NOT counted as a managed exit -- this understates the benefit of trailing
    (stated bias, identical across all configs).
    """
    if risk <= 0:
        return None
    long_side = direction == "LONG"
    tp_r = ((tp - entry) if long_side else (entry - tp)) / risk
    peak = 0.0
    s = -1.0                 # stop level in profit-R (structural -1R)
    be_done = False
    for j in range(idx_start, min(len(k5), idx_start + HORIZON_MIN)):
        c = k5[j]
        hi, lo = c["high"], c["low"]
        best = ((hi - entry) if long_side else (entry - lo)) / risk
        worst = ((lo - entry) if long_side else (entry - hi)) / risk

        # 1. stop exit at the level set BEFORE this bar
        if worst <= s:
            label = "SL" if s <= -1.0 else ("BE" if s == 0.0 else "TRAIL")
            return {"r": s, "exit": label, "bars": j - idx_start}

        # 2. fixed take-profit
        if best >= tp_r:
            return {"r": tp_r, "exit": "TP", "bars": j - idx_start}

        # 3. state updates (after the checks -- conservative)
        peak = max(peak, best)
        if not be_done and BE_R and best >= BE_R:
            be_done = True
            s = max(s, 0.0)
        if ARM_R and TRAIL_R and peak >= ARM_R:
            s = max(s, peak - TRAIL_R)

    last = k5[min(len(k5) - 1, idx_start + HORIZON_MIN - 1)]
    mark = ((last["close"] - entry) if long_side else (entry - last["close"])) / risk
    return {"r": mark, "exit": "TIMEOUT", "bars": HORIZON_MIN}


_KLINE_CACHE: dict = {}


def _cached_klines(sym, interval, days):
    key = (sym, interval, days)
    if key not in _KLINE_CACHE:
        _KLINE_CACHE[key] = mirror.fetch_klines(sym, interval, days)
    return _KLINE_CACHE[key]


def run_config(label, atr_source, arm, trail, be_r):
    """One full replay pass with a given stop metric + exit scheme."""
    # simulate() reads these as module globals -- apply the config explicitly
    global ARM_R, TRAIL_R, BE_R
    ARM_R, TRAIL_R, BE_R = arm, trail, be_r
    totals, cells, n, fills = [], [], 0, 0
    for sym in SYM:
        try:
            k5 = _cached_klines(sym, "5m", DAYS)
            k1h = _cached_klines(sym, "1h", DAYS + 2)
        except Exception as e:
            print(f"[{sym}] fetch failed: {e}", file=sys.stderr)
            continue
        if len(k5) < 300 or len(k1h) < 220:
            continue

        atr1h_at = {}
        for j in range(len(k1h)):
            if j + 1 >= 15:
                atr1h_at[k1h[j]["close_time"]] = calculate_atr(k1h[:j + 1], 14) or 0.0
        atr5_at = {}
        for j in range(len(k5)):
            if j + 1 >= 15:
                atr5_at[k5[j]["close_time"]] = calculate_atr(k5[max(0, j - 199):j + 1], 14) or 0.0

        i5 = {c["open_time"]: n_ for n_, c in enumerate(k5)}
        sigs = []
        for i in range(2, len(k5)):
            c3 = k5[i]
            c3_close_ms = c3["open_time"] + 300_000
            if i + 60 >= len(k5):
                break
            w5 = [c for c in k5[max(0, i - 199):i + 1] if c["close_time"] <= c3_close_ms]
            w1 = [c for c in k1h if c["close_time"] <= c3_close_ms][-200:]
            if len(w5) < 60 or len(w1) < 200:
                continue
            atr_1h = atr1h_at.get(w1[-1]["close_time"])
            if not atr_1h:
                continue
            atr_for_stop = atr_1h if atr_source == "1h" else atr5_at.get(w5[-1]["close_time"])
            if not atr_for_stop:
                continue
            bias = mirror.analyze_trend_bias([c["close"] for c in w1])
            for p in mirror.detect_three_candles(w5, sym):
                if abs(int(p.c3_time) - c3_close_ms) > 1:
                    continue
                e = mirror.evaluate_three_candle_entry(p, atr_for_stop, TP_MULT)
                if not e["is_valid"]:
                    continue
                direction = "LONG" if p.direction == "BULLISH" else "SHORT"
                aligned = ((bias.direction == "BULLISH" and direction == "LONG") or
                           (bias.direction == "BEARISH" and direction == "SHORT"))
                skip = None
                if bias.direction == "NEUTRAL":
                    pass
                elif aligned and mirror.STRENGTH_RANK.get(bias.strength, 2) < mirror.STRENGTH_RANK[mirror.MIN_STRENGTH]:
                    skip = 1
                elif bias.direction == "BEARISH" and direction == "LONG":
                    skip = 1
                elif bias.direction == "BULLISH" and direction == "SHORT":
                    skip = 1
                elif bias.rsi_zone == "OVERBOUGHT" and direction == "LONG":
                    skip = 1
                elif bias.rsi_zone == "OVERSOLD" and direction == "SHORT":
                    skip = 1
                if skip:
                    continue
                sigs.append({
                    "sym": sym, "dir": direction, "t": c3_close_ms, "i": i,
                    "mid": (p.entry_zone_min + p.entry_zone_max) / 2.0,
                    "zmin": p.entry_zone_min, "zmax": p.entry_zone_max,
                    "sl": e["stop_loss"], "tp": e["take_profit"],
                    "risk": abs(e["entry_price"] - e["stop_loss"]),
                })

        # ---- fill walk + outcome ------------------------------------------
        for s in sigs:
            mid, risk, sl, tp = s["mid"], s["risk"], s["sl"], s["tp"]
            if risk <= 0:
                continue
            # zone-void gate: market must be inside +/-1% of the zone at scan
            first_px = k5[s["i"] + 1]["open"]
            zmin, zmax = s["zmin"], s["zmax"]
            if first_px < zmin * 0.99 or first_px > zmax * 1.01:
                continue
            if (s["dir"] == "LONG" and sl >= first_px) or (s["dir"] == "SHORT" and sl <= first_px):
                continue                                   # gate 4b
            # limit fill at the midpoint within SIGNAL_MAX_AGE
            fill_j = None
            for j in range(s["i"] + 1, len(k5)):
                c = k5[j]
                if (s["dir"] == "LONG" and c["low"] <= mid) or (s["dir"] == "SHORT" and c["high"] >= mid):
                    fill_j = j
                    break
                if c["open_time"] - s["t"] > SIGNAL_MAX_AGE_S * 1000:
                    break
            if fill_j is None:
                continue                                    # never filled -> no trade
            fills += 1
            res = simulate(k5, fill_j, s["dir"], mid, sl, tp, risk)
            if not res:
                continue
            n += 1
            # fees: ~FEE_RT of notional per round trip, expressed in R
            # (fee_r = FEE_RT * entry / risk; e.g. 0.0007 * 100 / 1.0 = 0.07R)
            fee_r = 0.0 if NO_FEES else FEE_RT * mid / risk
            res["fee_r"] = fee_r
            res["net_r"] = res["r"] - fee_r
            cells.append(res)
            totals.append(res["net_r"])

    avg = (sum(totals) / len(totals)) if totals else 0.0
    pos = [c for c in cells if c["net_r"] > 0]
    exits = {}
    for c in cells:
        exits[c["exit"]] = exits.get(c["exit"], 0) + 1
    return {"label": label, "n": n, "filled": fills, "avg_net_r": round(avg, 4),
            "wins": len(pos), "win_rate": round(100 * len(pos) / n, 1) if n else 0,
            "total_net_r": round(sum(totals), 1), "exits": exits,
            "avg_fee_r": round(sum(c["fee_r"] for c in cells) / n, 4) if n else 0}


def main():
    configs = [
        ("baseline (today: 1h-ATR stop, TP only, BE0.5)", "1h", 0, 0.75, 0.5),
        ("D2 only (5m-ATR stop, TP only, BE0.5)",         "5m", 0, 0.75, 0.5),
        ("D3 only (1h-ATR stop, runner 1.0/0.75, BE0.5)", "1h", 1.0, 0.75, 0.5),
        ("D2+D3 (5m-ATR stop, runner 1.0/0.75, BE0.5)",   "5m", 1.0, 0.75, 0.5),
        ("D2+D3+BE0.75",                                  "5m", 1.0, 0.75, 0.75),
    ]
    out = {"days": DAYS, "fee_rt": FEE_RT, "configs": []}
    print(f"days={DAYS} fee_rt={FEE_RT} horizon={HORIZON_MIN}m fill<= {SIGNAL_MAX_AGE_S}s")
    print(f"{'config':52s} {'n':>5s} {'fill':>5s} {'netR':>8s} {'win%':>6s} {'feeR':>6s}  exits")
    for label, metric, arm, trail, be in configs:
        r = run_config(label, metric, arm, trail, be)
        out["configs"].append(r)
        print(f"{label:52s} {r['n']:5d} {r['filled']:5d} {r['avg_net_r']:8.4f} "
              f"{r['win_rate']:6.1f} {r['avg_fee_r']:6.3f}  {r['exits']}")
    with open("/tmp/cycle1-results.json", "w") as f:
        json.dump(out, f, indent=1)
    print("json -> /tmp/cycle1-results.json")


if __name__ == "__main__":
    main()
