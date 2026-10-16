#!/usr/bin/env python3
"""
mimo-entry-replay.py — FUBOT entry-timing replay (panelist: mimo, mandate: entry quality).

Replays the LIVE 3CANDLE pipeline over real Binance USDS-M klines with NO LOOKAHEAD:
  * Detection + evaluation mirror backend/services/signal_service.py:91-110 EXACTLY:
      - ATR(14) on the 1h series via backend.indicators.atr (Wilder)
      - analyze_trend_bias on the 200 1h closes (window ending at the C3 close)
      - detect_three_candles on the 200 5m candles (window ending at the C3 close)
      - evaluate_three_candle_entry(p, atr_1h, 3.0) — the 1h ATR stop floor
      - the same gates: trade_neutral_markets=True (live default), strength gate
        MODERATE on ALIGNED entries, counter-trend block, RSI extremes, R:R>=3
  * No-lookahead rule: an index window "at C3 close" uses only candles whose
    close_time <= C3 open_time + 5m (i.e. the data the bot had when it scanned).
  * Live execution model (strategy_service.py:170,479-521): LIMIT at the zone
    midpoint (floored to the exchange tick), cancelled after `fill_timeout_s`;
    gate 4b voids setups whose stop is already breached at signal time.
  * Fill walk uses per-5m-candle (low, high) only — conservative assumption:
    intra-candle order is unknowable, so a candle that touches both the stop and
    the entry is counted as STOP-FIRST (fills it conservatively).
  * Outcome MFE/MAE (no-fee, in R of the signal's planned risk) are computed from
    the close of the C3 candle over the next `horizon_min` minutes. Deliberately
    UNMODELLING the engine's BE-trailing / exits — this isolates entry quality.
  * Fees stated separately: taker 0.05% / maker 0.02% of notional, converted to R
    via the signal's risk distance.

Outputs tables to stdout (and JSON to /tmp/mimo-entry-results.json).
Read-only: touches only the public Binance REST API. No DB, no orders.
"""
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.indicators.atr import calculate_atr                      # noqa: E402
from backend.indicators.trend import analyze_trend_bias               # noqa: E402
from backend.indicators.three_candle import detect_three_candles, \
    evaluate_three_candle_entry                                      # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT",
           "LINKUSDT", "MATICUSDT", "DOTUSDT", "XRPUSDT"]
DAYS = int(os.environ.get("REPLAY_DAYS", "14"))
FILL_WINDOWS_S = [12, 60, 300, 3600]          # live 12s, then 1min/5min/60min
HORIZON_MIN = 30                              # outcome horizon: the live bot only
                                             # evaluates signals <=60 min old, and
                                             # today's losers resolve in 1-40 min
RR_MIN = 3.0
TAKER_FEE, MAKER_FEE = 0.0005, 0.0002         # Binance USDS-M standard
MIN_CANDLES = 60                              # min 5m candles after a signal to score it

TICKS = {  # PRICE_FILTER tickSize, exchangeInfo 2026-10-15; MATICUSDT delisted (404)
    "BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.01, "BNBUSDT": 0.01,
    "AVAXUSDT": 0.001, "LINKUSDT": 0.001, "DOTUSDT": 0.0001, "XRPUSDT": 0.0001,
}
STRENGTH_RANK = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}
MIN_STRENGTH = "MODERATE"                     # live trade_min_trend_strength default


def http_json(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fubot-panel-replay"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def fetch_klines(symbol, interval, days):
    """All klines for `interval` over `days`, deduped by open time, ascending."""
    end = int(time.time() * 1000)
    start = end - days * 86400_000
    limit = 1500 if interval == "5m" else 500
    out, cur = {}, start
    while cur < end:
        data = http_json("https://fapi.binance.com/fapi/v1/klines"
                         f"?symbol={symbol}&interval={interval}&startTime={cur}"
                         f"&endTime={end}&limit={limit}")
        if not data:
            break
        for k in data:
            out[k[0]] = {
                "open_time": k[0], "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                "close_time": k[6],
            }
        cur = data[-1][0] + 1
        if len(data) < limit:
            break
        time.sleep(0.12)
    return [out[t] for t in sorted(out)]


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def main():
    results = {"days": DAYS, "symbols": {}, "signals": []}
    all_signals = []

    for sym in SYMBOLS:
        try:
            k5 = fetch_klines(sym, "5m", DAYS)
            k1h = fetch_klines(sym, "1h", DAYS + 2)
        except Exception as e:
            print(f"[{sym}] FETCH FAILED: {e}")
            results["symbols"][sym] = {"error": str(e)}
            continue
        if len(k5) < 300 or len(k1h) < 220:
            print(f"[{sym}] insufficient data 5m={len(k5)} 1h={len(k1h)}")
            results["symbols"][sym] = {"error": "insufficient"}
            continue

        # integrity: 5m candles must be contiguous (gaps = missed scans)
        gaps = sum(1 for a, b in zip(k5, k5[1:]) if b["open_time"] - a["open_time"] != 300_000)
        last_age_min = (time.time() * 1000 - k5[-1]["open_time"]) / 60000

        # incremental ATR(1h) over the full series: window = k1h[0..j] with
        # k1h[j].close_time <= c3 close. Cost O(n*214) — fine at this scale.
        atr_1h_at = {}
        for j in range(len(k1h)):
            if j + 1 >= 15:
                atr_1h_at[k1h[j]["close_time"]] = calculate_atr(k1h[:j + 1], 14) or 0.0

        # index 5m candles by open_time for slicing
        i5 = {c["open_time"]: n for n, c in enumerate(k5)}
        sigs = []
        for i in range(2, len(k5)):
            c3 = k5[i]
            c3_close_ms = c3["open_time"] + 300_000
            if i + MIN_CANDLES >= len(k5):
                break                                    # not enough future to score
            # ---- exact live data windows at scan time -----------------------
            w5 = [c for c in k5[max(0, i - 199):i + 1] if c["close_time"] <= c3_close_ms]
            w1 = [c for c in k1h if c["close_time"] <= c3_close_ms][-200:]
            if len(w5) < 60 or len(w1) < 200:
                continue
            atr_1h = atr_1h_at.get(w1[-1]["close_time"])
            if atr_1h is None or atr_1h <= 0:
                continue
            bias = analyze_trend_bias([c["close"] for c in w1])
            pats = detect_three_candles(w5, sym)
            # only patterns formed by THIS c3 (index i)
            for p in pats:
                # c3_time carries the raw close_time epoch-ms (binance_service
                # parses k[6] as int) — compare against the C3 open + 5m
                if abs(int(p.c3_time) - c3_close_ms) > 1:
                    continue
                e = evaluate_three_candle_entry(p, atr_1h, RR_MIN)
                if not e["is_valid"]:
                    continue
                direction = "LONG" if p.direction == "BULLISH" else "SHORT"
                # ---- live gates (signal_service.py:134-170) -----------------
                aligned = ((bias.direction == "BULLISH" and direction == "LONG") or
                           (bias.direction == "BEARISH" and direction == "SHORT"))
                skip = None
                if bias.direction == "NEUTRAL":
                    pass                                # trade_neutral_markets=True (live)
                elif aligned and STRENGTH_RANK.get(bias.strength, 2) < STRENGTH_RANK[MIN_STRENGTH]:
                    skip = f"weak {bias.strength}"
                elif bias.direction == "BEARISH" and direction == "LONG":
                    skip = "counter BEARISH"
                elif bias.direction == "BULLISH" and direction == "SHORT":
                    skip = "counter BULLISH"
                elif bias.rsi_zone == "OVERBOUGHT" and direction == "LONG":
                    skip = "RSI OB"
                elif bias.rsi_zone == "OVERSOLD" and direction == "SHORT":
                    skip = "RSI OS"
                if skip:
                    continue                            # live: never becomes PENDING
                sigs.append({
                    "symbol": sym, "dir": direction, "t": c3_close_ms,
                    "zone_min": p.entry_zone_min, "zone_max": p.entry_zone_max,
                    "mid": (p.entry_zone_min + p.entry_zone_max) / 2.0,
                    "sl": e["stop_loss"], "tp": e["take_profit"],
                    "risk": abs(e["entry_price"] - e["stop_loss"]),
                    "c1w_pct": (p.c1_high - p.c1_low) / p.c1_high * 100,
                    "bias": bias.direction, "strength": bias.strength,
                    "atr1h_pct": atr_1h / w1[-1]["close"] * 100,
                    "idx": i,
                })

        # ---- walk forward per signal -------------------------------------
        for s in sigs:
            i = s["idx"]
            risk, mid, sl, tp = s["risk"], s["mid"], s["sl"], s["tp"]
            if risk <= 0:
                continue
            # gate 4b at signal time: stop must not be breached (use next
            # candle's open as the market price the bot would see ~immediately)
            first_px = k5[i + 1]["open"]
            if (s["dir"] == "LONG" and sl >= first_px) or (s["dir"] == "SHORT" and sl <= first_px):
                s["void"] = True

            # ATR mismatch stats (window ends at C3 — no lookahead)
            atr5 = calculate_atr(w5[:200], 14)
            s["atr5_pct"] = (atr5 / mid * 100) if atr5 else None
            s["floor_mult"] = (risk / atr5) if atr5 else None
            s["structural_risk_pct"] = abs(
                (mid - (s["zone_max"] if s["dir"] == "LONG" else s["zone_min"])) / mid * 100)

            # ---- limit fill walk (tick-floored limit) ----------------------
            tick = TICKS[s["symbol"]]
            limit_px = math.floor(mid / tick) * tick
            fill_t = None
            for j in range(i + 1, len(k5)):
                c = k5[j]
                if (s["dir"] == "LONG" and c["low"] <= limit_px) or \
                   (s["dir"] == "SHORT" and c["high"] >= limit_px):
                    fill_t = c["open_time"]
                    break
                if c["open_time"] - s["t"] > max(FILL_WINDOWS_S) * 1000:
                    break
            for w in FILL_WINDOWS_S:
                s[f"fill_{w}"] = int(fill_t is not None and fill_t - s["t"] <= w * 1000)

            # ---- MFE/MAE over horizon from the C3 close --------------------
            # 30-minute horizon (the live bot only evaluates signals <=60 min old)
            mfe = mae = 0.0
            tgt_hit = sl_hit = None
            end_j = min(len(k5), i + 1 + 30)               # 30m horizon, 1candle=1min
            for j in range(i + 1, end_j):
                c = k5[j]
                if s["dir"] == "LONG":
                    adv, fav = mid - c["low"], c["high"] - mid
                else:
                    adv, fav = c["high"] - mid, mid - c["low"]
                mae = max(mae, adv); mfe = max(mfe, fav)
                # stop-before-target: if both in one candle, count the stop
                if tgt_hit is None and fav >= 2 * risk and adv < 2 * risk:
                    tgt_hit = j - i
                if sl_hit is None and adv >= risk:
                    sl_hit = j - i
                    if tgt_hit is None:
                        tgt_hit = -1                      # lost
                    break
            s["mfe_r"] = mfe / risk
            s["mae_r"] = mae / risk
            s["mfe30_r"] = min(s["mfe_r"], 3.0)            # capped-at-TP outcome
            # outcome if the limit DID fill at `limit_px` (entry distance from
            # mid changes R geometry and the walk START point)
            lr = abs(limit_px - sl) if s["dir"] == "LONG" else abs(sl - limit_px)
            if lr > 0:
                mfe_l = mae_l = 0.0
                for j in range(i + 1, end_j):
                    c = k5[j]
                    if s["dir"] == "LONG":
                        adv, fav = limit_px - c["low"], c["high"] - limit_px
                    else:
                        adv, fav = c["high"] - limit_px, limit_px - c["low"]
                    mae_l = max(mae_l, adv); mfe_l = max(mfe_l, fav)
                    if mae_l >= lr:
                        break
                s["mfe_fill_r"] = mfe_l / lr
                s["mfe30_fill_r"] = min(mfe_l / lr, 3.0)
                s["mae_fill_r"] = mae_l / lr
                s["risk_fill_pct"] = lr / limit_px * 100
            s["win30"] = 1 if (tgt_hit is not None and tgt_hit > 0 and s["mfe_r"] >= 2 * 1.0) else 0
            s["hold_min"] = tgt_hit if tgt_hit and tgt_hit > 0 else (sl_hit or 30)
            # extended 240m reach (does the 3R target ever arrive?)
            if i + 1 + HORIZON_MIN <= len(k5):
                ext = 0.0
                for j in range(i + 1, i + 1 + HORIZON_MIN):
                    c = k5[j]
                    fav = (c["high"] - mid) if s["dir"] == "LONG" else (mid - c["low"])
                    ext = max(ext, fav)
                s["ext_mfe_r"] = ext / risk
                s["ext_ok"] = True
            s["sl_first"] = int(sl_hit is not None and (tgt_hit is None or tgt_hit < 0 or sl_hit <= tgt_hit))
            # fee drag in R
            fee_r = (2 * MAKER_FEE if s["fill_12"] else 2 * TAKER_FEE) * mid / risk
            s["fee_r"] = fee_r
            # 5m-ATR alternative stop: sl5 = mid - 1.0*atr5 (mirror of the floor)
            if atr5:
                alt_risk = atr5
                s["alt_reach30"] = int(mfe >= 3 * alt_risk)
                s["alt_r"] = alt_risk / mid * 100
            all_signals.append(s)

        results["symbols"][sym] = {
            "k5": len(k5), "k1h": len(k1h), "gaps_5m": gaps,
            "last_candle_age_min": round(last_age_min, 1),
            "signals": len(sigs),
        }
        print(f"[{sym}] 5m={len(k5)} 1h={len(k1h)} gaps={gaps} "
              f"age={last_age_min:.0f}m signals={len(sigs)}", flush=True)

    # ================= REPORT =================
    live = [s for s in all_signals if not s.get("void")]
    print(f"\n=== 3CANDLE replay, {DAYS}d, 9 symbols "
          f"(MATICUSDT delisted) — {len(all_signals)} gated signals, "
          f"{len(live)} tradeable after gate 4b ===")

    # Table 1: fill windows
    print("\nT1. Midpoint-limit fill rate by window (share of tradeable signals):")
    print(f"{'window':>8} {'filled':>7} {'n':>6}")
    for w in FILL_WINDOWS_S:
        f = sum(s[f"fill_{w}"] for s in live)
        print(f"{str(w)+'s' if w < 60 else str(w//60)+'m' if w < 3600 else '60m':>8} "
              f"{f/len(live)*100:6.1f}% {f:6d}")

    # Table 2: filled vs unfilled follow-through
    print("\nT2. Filled-within-1min vs unfilled (30m horizon, no fees):")
    for label, sel in [("filled<=1m", [s for s in live if s["fill_60"]]),
                       ("unfilled>1m", [s for s in live if not s["fill_60"]])]:
        if not sel:
            print(f"{label:>12} n=0"); continue
        mfe = sum(s["mfe_r"] for s in sel) / len(sel)
        mae = sum(s["mae_r"] for s in sel) / len(sel)
        m30 = sum(s["mfe30_r"] for s in sel) / len(sel)
        slf = sum(s["sl_first"] for s in sel) / len(sel)
        fee = sum(s["fee_r"] for s in sel) / len(sel)
        mf = sum(s.get("mfe_fill_r", 0) for s in sel if "mfe_fill_r" in s) / max(1, sum(1 for s in sel if "mfe_fill_r" in s))
        rf = sum(s.get("risk_fill_pct", 0) for s in sel if "risk_fill_pct" in s) / max(1, sum(1 for s in sel if "risk_fill_pct" in s))
        flag = "" if len(sel) >= 20 else "  [INCONCLUSIVE n<20]"
        print(f"{label:>12} n={len(sel):4d} MFE={mfe:5.2f}R MAE={mae:5.2f}R "
              f"MFE_cap3R={m30:5.2f}R stop-first={slf*100:4.0f}% fee={fee:4.2f}R{flag}")
        print(f"{'':>12}  if filled: MFE={mf:5.2f}R(entry-adjusted) risk%={rf:5.3f}%")

    # Table 3: ATR mismatch per symbol
    print("\nT3. 1h-ATR stop floor vs 5m ATR (per symbol, tradeable signals):")
    print(f"{'symbol':>9} {'n':>4} {'atr1h%':>7} {'atr5m%':>7} {'ratio':>6} "
          f"{'risk%':>6} {'struct%':>8} {'3R reach':>8}")
    for sym in SYMBOLS:
        sel = [s for s in live if s["symbol"] == sym]
        if not sel:
            print(f"{sym:>9} n=0"); continue
        a1 = sum(s["atr1h_pct"] for s in sel) / len(sel)
        a5 = sum(s["atr5_pct"] for s in sel if s["atr5_pct"]) / max(1, sum(1 for s in sel if s["atr5_pct"]))
        rk = sum(s["risk"] / s["mid"] * 100 for s in sel) / len(sel)
        st = sum(s["structural_risk_pct"] for s in sel) / len(sel)
        reach = sum(s["alt_reach30"] for s in sel if "alt_reach30" in s) / max(1, sum(1 for s in sel if "alt_reach30" in s))
        flag = "" if len(sel) >= 20 else "  [<20 INCONCLUSIVE]"
        print(f"{sym:>9} {len(sel):4d} {a1:6.2f}% {a5:6.2f}% {a1/a5 if a5 else 0:5.1f}x "
              f"{rk:5.2f}% {st:7.2f}% {reach*100:7.0f}%{flag}")
    print("  (3R reach = share of signals where MFE >= 3 x the 5m-ATR risk — "
          "i.e. the target WOULD have been reachable with a 5m-ATR stop)")

    # T3b: 240-minute horizon reach (the live winners resolved at 201/228 min)
    print("\nT3b. 240-min-horizon reach: share with MFE >= 3R (planned stop) / >= 3x ATR5m risk:")
    for sym in SYMBOLS:
        sel = [s for s in live if s["symbol"] == sym and s.get("ext_ok")]
        if not sel:
            print(f"  {sym:>9} n=0"); continue
        r1 = sum(1 for s in sel if s["ext_mfe_r"] >= 3.0) / len(sel)
        r5 = sum(1 for s in sel if s["ext_mfe_r"] * s["risk"] / (s["atr5_pct"] / 100 * s["mid"]) >= 3.0) / len(sel)
        flag = "" if len(sel) >= 20 else " [INCONCLUSIVE n<20]"
        print(f"  {sym:>9} n={len(sel):4d} reach3R_1h={r1*100:4.0f}% reach3R_5m={r5*100:4.0f}%{flag}")
    sel = [s for s in live if s.get("ext_ok")]
    r1 = sum(1 for s in sel if s["ext_mfe_r"] >= 3.0) / len(sel)
    r5 = sum(1 for s in sel if s["ext_mfe_r"] * s["risk"] / (s["atr5_pct"] / 100 * s["mid"]) >= 3.0) / len(sel)
    print(f"  {'ALL':>9} n={len(sel):4d} reach3R_1h={r1*100:4.0f}% reach3R_5m={r5*100:4.0f}%"
          + ("" if len(sel) >= 20 else " [INCONCLUSIVE n<20]"))

    # Table 4: quality splits (all tradeable)
    print("\nT4. Quality splits — expectancy proxy E[mfe capped 3R] - P(stop first)x1R - fee:")
    print("  [proxyE is an UPPER-BOUND entry-quality proxy (perfect 3R exit at MFE);")
    print("   live expectancy is lower — today's book is +0.012R/trade]")
    def split(name, keyfn):
        buckets = {}
        for s in live:
            buckets.setdefault(keyfn(s), []).append(s)
        print(f"  {name}:")
        for k in sorted(buckets, key=lambda x: str(x)):
            sel = buckets[k]
            m30 = sum(s["mfe30_r"] for s in sel) / len(sel)
            slf = sum(s["sl_first"] for s in sel) / len(sel)
            fee = sum(s["fee_r"] for s in sel) / len(sel)
            exp = m30 - slf - fee
            flag = "" if len(sel) >= 20 else "  [INCONCLUSIVE n<20]"
            print(f"    {str(k):>12} n={len(sel):4d} mfe3R={m30:5.2f} stop-first={slf*100:4.0f}% "
                  f"fee={fee:4.2f}R proxyE={exp:+6.3f}R{flag}")
    split("trend strength (aligned+neutral)", lambda s: s["strength"])
    split("bias direction", lambda s: s["bias"])
    split("C1 range width", lambda s: "wide>0.3%" if s["c1w_pct"] > 0.3 else "narrow<=0.3%")
    split("1h ATR% quartile", lambda s: "lo" if s["atr1h_pct"] < 0.5 else ("mid" if s["atr1h_pct"] < 0.9 else "hi"))
    split("symbol", lambda s: s["symbol"])

    # Table 5: reclaim / continuation confirmation (entry-quality filter candidates)
    print("\nT5. Confirmation filters (each vs base, on the same signals):")
    def base_stats(sel):
        if not sel:
            return "n=0"
        m30 = sum(s["mfe30_r"] for s in sel) / len(sel)
        slf = sum(s["sl_first"] for s in sel) / len(sel)
        fee = sum(s["fee_r"] for s in sel) / len(sel)
        flag = "" if len(sel) >= 20 else " [INCONCLUSIVE n<20]"
        return (f"n={len(sel):4d} mfe3R={m30:5.2f} stop-first={slf*100:4.0f}% "
                f"proxyE={m30-slf-fee:+6.3f}R{flag}")
    print("  all            " + base_stats(live))
    # (a) no retest of the stop within 30m after fill
    ok = []
    for s in live:
        i = s["idx"]
        first = None
        for j in range(i + 1, min(len(k5), i + 1 + 30)):
            c = k5[j]
            hit = (c["low"] <= s["sl"]) if s["dir"] == "LONG" else (c["high"] >= s["sl"])
            if hit:
                first = j - i
                break
        s["retest30"] = first
        if first is None or first > 10:
            ok.append(s)
    print("  no-stop-retest " + base_stats(ok))
    # (b) C3 close beyond C1 range by > 0.15% (strong breakout)
    ok2 = []
    for s in live:
        i = s["idx"]
        c3c = k5[i]["close"]
        ex = abs(c3c - (s["zone_max"] if s["dir"] == "LONG" else s["zone_min"])) / c3c * 100
        s["break_pct"] = ex
        if ex >= 0.15:
            ok2.append(s)
    print("  strong-break   " + base_stats(ok2))
    ok3 = [s for s in live if s["break_pct"] < 0.15]
    print("  weak-break     " + base_stats(ok3))

    # (c) C3 closes AT its extreme (continuation) vs mid-range (stalling)
    ok4 = []
    for s in live:
        i = s["idx"]
        c3 = k5[i]
        body = abs(c3["close"] - c3["open"])
        rng = max(c3["high"] - c3["low"], 1e-9)
        s["c3_closepos"] = body / rng
        if s["c3_closepos"] >= 0.5:
            ok4.append(s)
    print("  c3-close>=50%  " + base_stats(ok4))
    print("  c3-close<50%   " + base_stats([s for s in live if s["c3_closepos"] < 0.5]))

    # (d) tradeable-with-5m-stop: with a 5m-ATR stop the R unit shrinks, so the
    #     SAME move reaches 3R — what does the entry look like then?
    alt = [s for s in live if "alt_reach30" in s]
    print(f"\nT6. 5m-ATR stop swap (floor=1.0x ATR5m vs 1.0x ATR1h), tradeable n={len(alt)}:")
    a1 = sum(s["atr1h_pct"] for s in alt) / len(alt)
    a5 = sum(s["atr5_pct"] for s in alt) / len(alt)
    reach = sum(s["alt_reach30"] for s in alt) / len(alt)
    mfe5 = sum(min(s["mfe_r"] * s["risk"] / (s["atr5_pct"] / 100 * s["mid"]), 3.0) for s in alt) / len(alt)
    fee5 = sum(s["fee_r"] * s["risk"] / (s["atr5_pct"] / 100 * s["mid"]) for s in alt) / len(alt)
    print(f"  avg risk%: {a1:.2f}% (1h floor, binds on ~all) -> {a5:.2f}% (5m floor); "
          f"3R-target reachable in 30m: {reach*100:.0f}%")
    print(f"  E[min(MFE,3R)] with 5m-ATR R unit: {mfe5:.2f}R (vs {sum(s['mfe30_r'] for s in alt)/len(alt):.2f}R "
          f"with the 1h floor); fee in R grows {sum(s['fee_r'] for s in alt)/len(alt):.3f} -> {fee5:.3f}R")

    # (e) fill-window x stop-unit table (the actionable grid)
    print("\nT7. Fill rate by window x |entry-limited| (limit price below/above C3 close):")
    for w in (12, 300, 3600):
        sel = [s for s in live if s[f"fill_{w}"]]
        print(f"  {w:>5}s filled n={len(sel):4d}")

    with open("/tmp/mimo-entry-results.json", "w") as f:
        json.dump({"summary": results["symbols"],
                   "n_signals": len(all_signals), "n_live": len(live),
                   "fills": {str(w): sum(s[f"fill_{w}"] for s in live) for w in FILL_WINDOWS_S}},
                  f, indent=1)
    print("\nJSON: /tmp/mimo-entry-results.json")


def w5_of(k5, i):
    return k5[max(0, i - 199):i + 1]


if __name__ == "__main__":
    main()
