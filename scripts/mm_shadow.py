#!/usr/bin/env python3
"""Market-making shadow/paper data collector (read-only, public data only).

Phase-0 measurement tool for a future Binance USDⓈ-M market-making bot.
Subscribes to public bookTicker + aggTrade streams and polls public depth,
simulates conservative hypothetical maker quotes, and records:

  - top-of-book spread stats (p50/p90 bps)
  - hypothetical fills (trade-through only — never assume queue position)
  - markouts at +1s/+5s/+30s/+300s (adverse-selection measure)
  - completed FIFO round-trips per offset bucket, net of maker fees

Nothing here touches the backend, the DB, or any signed endpoint.

Output:
  - JSONL events appended to /tmp/mm_shadow_<YYYY-MM-DD>.jsonl
  - rolling per-symbol/per-offset summary every 60s on stdout

Usage:
    python scripts/mm_shadow.py [--hours N] [--offsets 3,5,8] [--depth-interval 2.0]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import signal
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# Binance split USDⓈ-M futures WS into routed tiers on 2026-04-23: a socket
# bound to one tier silently delivers ZERO frames for streams in the other
# tier. bookTicker is /public-tier; aggTrade is /market-tier. Each tier needs
# its own connection.
WS_PUBLIC_URL = "wss://fstream.binance.com/public/stream?streams="
WS_MARKET_URL = "wss://fstream.binance.com/market/stream?streams="
DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT",
]
DEFAULT_OFFSETS_BPS = [3, 5, 8]
# Maker round-trip fee assumption: 0.018% taker-free maker each side ->
# ~0.036% = 3.6 bps per round trip. Hardcoded per task spec.
MAKER_RT_BPS = 3.6
MARKOUT_HORIZONS_S = (1.0, 5.0, 30.0, 300.0)
DEPTH_LIMIT = 20
SUMMARY_INTERVAL_S = 60.0
MARKOUT_TICK_S = 0.25

log = logging.getLogger("mm_shadow")


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

@dataclass
class BookState:
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    ts_ms: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid and self.ask else 0.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m else 0.0


@dataclass
class Fill:
    ts: float
    symbol: str
    side: str          # "bid" or "ask"
    offset_bps: int
    price: float
    mid_at_fill: float
    markouts: dict = field(default_factory=dict)  # horizon_s -> signed bps
    done: bool = False


@dataclass
class BucketState:
    """Per (symbol, offset) hypothetical quote state."""
    offset: int = 0
    bid_price: float = 0.0
    ask_price: float = 0.0
    open_bid: collections.deque = field(default_factory=collections.deque)   # FIFO fills
    open_ask: collections.deque = field(default_factory=collections.deque)
    fills: int = 0
    trips: int = 0
    gross_bps_sum: float = 0.0
    net_bps_sum: float = 0.0


class SymbolState:
    def __init__(self, symbol: str, tick_size: float, offsets: list[int]):
        self.symbol = symbol
        self.tick_size = tick_size
        self.book = BookState()
        self.buckets: dict[int, BucketState] = {
            o: BucketState(offset=o) for o in offsets
        }
        self.spreads: collections.deque = collections.deque(maxlen=20000)
        self.pending_markouts: list[tuple[Fill, float]] = []  # (fill, due_ts)


# --------------------------------------------------------------------------
# JSONL persistence
# --------------------------------------------------------------------------

class JsonlWriter:
    def __init__(self, path: Path):
        self._fh = path.open("a", buffering=1)  # line buffered

    def write(self, event: dict):
        event["recv_ts"] = time.time()
        try:
            self._fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            log.warning(f"JSONL write failed: {e}")

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Main collector
# --------------------------------------------------------------------------

class ShadowCollector:
    def __init__(self, symbols: list[str], offsets: list[int], depth_interval: float):
        self.symbols = symbols
        self.offsets = offsets
        self.depth_interval = depth_interval
        self.states: dict[str, SymbolState] = {}
        self.session: aiohttp.ClientSession | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._depth_seq = 0
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.out_path = Path(f"/tmp/mm_shadow_{date}.jsonl")
        self.writer = JsonlWriter(self.out_path)

    # ----- setup ---------------------------------------------------------

    async def setup(self):
        self.session = aiohttp.ClientSession()
        # Fetch tick sizes once (public, unsigned).
        async with self.session.get(EXCHANGE_INFO_URL) as resp:
            info = await resp.json()
        # PRICE_FILTER carries tickSize; filters[0] is not guaranteed to be it.
        tick_map = {}
        for s in info["symbols"]:
            if s["symbol"] not in self.symbols:
                continue
            for f in s.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    tick_map[s["symbol"]] = float(f["tickSize"])
                    break
        for sym in self.symbols:
            tick = tick_map.get(sym, 0.0)
            self.states[sym] = SymbolState(sym, tick, self.offsets)
            log.info(f"{sym}: tick={tick}")

    async def teardown(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self.session and not self.session.closed:
            await self.session.close()
        self.writer.close()

    # ----- websocket ------------------------------------------------------

    def _ws_urls(self) -> list[str]:
        """One combined-stream URL per routed tier."""
        public = "/".join(f"{s.lower()}@bookTicker" for s in self.symbols)
        market = "/".join(f"{s.lower()}@aggTrade" for s in self.symbols)
        return [WS_PUBLIC_URL + public, WS_MARKET_URL + market]

    async def run_ws(self, url: str):
        """One connection per tier with reconnect + backoff; streams live in
        the URL so a fresh connection re-subscribes automatically."""
        delay = 1.0
        max_delay = 30.0
        while self._running:
            ws = None
            try:
                ws = await self.session.ws_connect(url, heartbeat=30)
                delay = 1.0
                log.info(f"WS connected: {url.split('?')[0]}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            self._handle_ws(json.loads(msg.data))
                        except Exception as e:
                            log.warning(f"WS message error: {e}")
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise ConnectionError("WS error frame")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                        break
            except asyncio.CancelledError:
                if ws and not ws.closed:
                    await ws.close()
                raise
            except Exception as e:
                log.warning(f"WS disconnected ({url.split('?')[0]}): {e}; "
                            f"retry in {delay:.0f}s")
            finally:
                if ws and not ws.closed:
                    try:
                        await ws.close()
                    except Exception:
                        pass
            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    def _handle_ws(self, data: dict):
        stream = data.get("stream", "")
        payload = data.get("data", {})
        if not stream or not payload:
            return
        symbol = payload.get("s", "").upper()
        st = self.states.get(symbol)
        if st is None:
            return
        if stream.endswith("@bookTicker"):
            self._on_book(st, payload)
        elif stream.endswith("@aggTrade"):
            self._on_trade(st, payload)

    # ----- handlers --------------------------------------------------------

    def _on_book(self, st: SymbolState, p: dict):
        bid = float(p.get("b", 0) or 0)
        ask = float(p.get("a", 0) or 0)
        if not bid or not ask:
            return
        st.book = BookState(
            bid=bid, ask=ask,
            bid_qty=float(p.get("B", 0) or 0),
            ask_qty=float(p.get("A", 0) or 0),
            ts_ms=int(p.get("T", 0) or p.get("E", 0) or 0),
        )
        spr = st.book.spread_bps
        if spr > 0:
            st.spreads.append(spr)
        self._refresh_quotes(st)

    def _refresh_quotes(self, st: SymbolState):
        """Recompute hypothetical quote prices from current mid."""
        mid = st.book.mid
        if not mid:
            return
        for off, b in st.buckets.items():
            b.bid_price = mid * (1 - off / 1e4)
            b.ask_price = mid * (1 + off / 1e4)

    def _on_trade(self, st: SymbolState, p: dict):
        price = float(p.get("p", 0) or 0)
        if not price:
            return
        now = time.time()
        mid = st.book.mid
        for off, b in st.buckets.items():
            if not b.bid_price or not b.ask_price:
                continue
            # Conservative trade-through fill: price must pierce the quote
            # by at least one tick. Queue position is never assumed.
            if price <= b.bid_price - st.tick_size:
                self._record_fill(st, b, "bid", off, price, mid, now)
            if price >= b.ask_price + st.tick_size:
                self._record_fill(st, b, "ask", off, price, mid, now)

    def _record_fill(self, st: SymbolState, b: BucketState, side: str,
                     off: int, price: float, mid: float, now: float):
        fill = Fill(ts=now, symbol=st.symbol, side=side, offset_bps=off,
                    price=price, mid_at_fill=mid)
        b.fills += 1
        ev = {"type": "fill", "symbol": st.symbol, "side": side,
              "offset_bps": off, "price": price, "mid": mid}
        self.writer.write(ev)
        # Schedule markouts.
        for h in MARKOUT_HORIZONS_S:
            st.pending_markouts.append((fill, now + h))
        # FIFO pairing for completed round-trips.
        if side == "bid":
            b.open_bid.append(fill)
        else:
            b.open_ask.append(fill)
        self._try_pair(st, b)

    def _try_pair(self, st: SymbolState, b: BucketState):
        """Pair oldest unmatched bid with oldest unmatched ask -> trip."""
        while b.open_bid and b.open_ask:
            buy = b.open_bid.popleft()
            sell = b.open_ask.popleft()
            # buy low, sell high => gross capture in bps of buy price
            gross_bps = (sell.price - buy.price) / buy.price * 1e4
            net_bps = gross_bps - MAKER_RT_BPS
            b.trips += 1
            b.gross_bps_sum += gross_bps
            b.net_bps_sum += net_bps
            ev = {"type": "trip", "symbol": st.symbol, "offset_bps": b.offset,
                  "buy_price": buy.price, "sell_price": sell.price,
                  "buy_ts": buy.ts, "sell_ts": sell.ts,
                  "gross_bps": round(gross_bps, 4), "net_bps": round(net_bps, 4)}
            self.writer.write(ev)

    # ----- markouts ---------------------------------------------------------

    async def markout_loop(self):
        """Every MARKOUT_TICK_S, resolve due markouts against latest mid."""
        while self._running:
            now = time.time()
            for st in self.states.values():
                if not st.pending_markouts:
                    continue
                mid = st.book.mid
                still = []
                for fill, due in st.pending_markouts:
                    if now >= due:
                        horizon = round(due - fill.ts, 3)
                        if mid and fill.price:
                            sign = 1.0 if fill.side == "bid" else -1.0
                            mo = (mid - fill.price) / fill.price * 1e4 * sign
                            fill.markouts[horizon] = round(mo, 4)
                            self.writer.write({
                                "type": "markout", "symbol": st.symbol,
                                "side": fill.side, "offset_bps": fill.offset_bps,
                                "price": fill.price, "horizon_s": horizon,
                                "mid": mid, "markout_bps": round(mo, 4),
                            })
                        fill.done = True
                    else:
                        still.append((fill, due))
                # keep entries only while horizons remain
                st.pending_markouts = [x for x in still
                                       if len(x[0].markouts) < len(MARKOUT_HORIZONS_S)]
            await asyncio.sleep(MARKOUT_TICK_S)

    # ----- depth ------------------------------------------------------------

    async def depth_loop(self, session: aiohttp.ClientSession):
        """Poll REST depth politely (default every 2s per symbol, sequential)."""
        sem = asyncio.Semaphore(4)
        while self._running:
            t0 = time.time()
            self._depth_seq += 1
            for sym in self.symbols:
                if not self._running:
                    break
                await self._fetch_depth(session, sym, sem)
            # pace so the whole sweep ~= depth_interval
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.1, self.depth_interval - elapsed))

    async def _fetch_depth(self, session: aiohttp.ClientSession, sym: str, sem):
        async with sem:
            try:
                async with session.get(
                    DEPTH_URL, params={"symbol": sym, "limit": DEPTH_LIMIT}
                ) as resp:
                    data = await resp.json()
                bids = [(float(p), float(q)) for p, q in data.get("bids", [])[:5]]
                asks = [(float(p), float(q)) for p, q in data.get("asks", [])[:5]]
                # top-of-book consistency check vs WS book
                st = self.states.get(sym)
                top_bid = bids[0][0] if bids else 0.0
                top_ask = asks[0][0] if asks else 0.0
                self.writer.write({
                    "type": "depth", "symbol": sym, "seq": self._depth_seq,
                    "top_bid": top_bid, "top_ask": top_ask,
                    "bids": bids, "asks": asks,
                })
            except Exception as e:
                log.warning(f"depth fetch {sym}: {e}")

    # ----- summary ------------------------------------------------------------

    def _pct(self, vals, q):
        if not vals:
            return 0.0
        s = sorted(vals)
        i = min(len(s) - 1, int(q * (len(s) - 1)))
        return s[i]

    def print_summary(self, final: bool = False):
        title = "=== FINAL SUMMARY ===" if final else "=== 60s SUMMARY ==="
        log.info(title)
        header = (f"{'symbol':<9} {'off':>3} {'spr50':>7} {'spr90':>7} "
                  f"{'fills':>6} {'trips':>6} {'avgNet':>8} {'cumNet':>9}")
        print(header)
        for sym in self.symbols:
            st = self.states[sym]
            spr50 = self._pct(list(st.spreads), 0.5)
            spr90 = self._pct(list(st.spreads), 0.9)
            # clear window spreads after reporting (rolling 60s window)
            st.spreads.clear()
            for off in self.offsets:
                b = st.buckets[off]
                avg_net = b.net_bps_sum / b.trips if b.trips else 0.0
                print(f"{sym:<9} {off:>3} {spr50:>7.2f} {spr90:>7.2f} "
                      f"{b.fills:>6} {b.trips:>6} {avg_net:>8.3f} {b.net_bps_sum:>9.2f}")
            print(f"{'':<9} {'':>3} {'':>7} {'':>7} {'':>6} {'':>6} {'':>8} {'':>9}")
        print(f"JSONL: {self.out_path}")
        sys.stdout.flush()

    async def summary_loop(self):
        while self._running:
            await asyncio.sleep(SUMMARY_INTERVAL_S)
            if self._running:
                try:
                    self.print_summary()
                except Exception as e:
                    log.warning(f"summary error: {e}")


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

async def amain(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    collector = ShadowCollector(
        symbols=[s.upper() for s in args.symbols.split(",")],
        offsets=[int(x) for x in args.offsets.split(",")],
        depth_interval=args.depth_interval,
    )
    await collector.setup()

    stop = asyncio.Event()

    def _sig(*_):
        log.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig)
        except NotImplementedError:
            pass

    collector._running = True
    tasks = [
        *[asyncio.create_task(collector.run_ws(url))
          for url in collector._ws_urls()],
        asyncio.create_task(collector.markout_loop()),
        asyncio.create_task(collector.depth_loop(collector.session)),
        asyncio.create_task(collector.summary_loop()),
    ]
    collector._tasks = tasks

    hours = args.hours
    if hours and hours > 0:
        try:
            await asyncio.wait_for(stop.wait(), timeout=hours * 3600)
        except asyncio.TimeoutError:
            log.info(f"--hours {hours} elapsed")
    else:
        await stop.wait()

    collector._running = False
    collector.print_summary(final=True)
    await collector.teardown()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="MM shadow data collector (public data only)")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="run duration in hours (0 = until Ctrl-C)")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="comma-separated symbols")
    ap.add_argument("--offsets", default=",".join(str(o) for o in DEFAULT_OFFSETS_BPS),
                    help="comma-separated quote offsets in bps")
    ap.add_argument("--depth-interval", type=float, default=2.0,
                    help="seconds between full depth sweeps (per sweep, not per symbol)")
    args = ap.parse_args()
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
