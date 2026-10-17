"""Position manager — track open positions, BE trailing, TP/SL monitoring.

DB access: session factory + short-lived sessions. Positions are snapshotted
per monitoring pass; every mutation re-fetches the row in its own session so a
cancelled pass can never leave the shared state dirty.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import update

from backend.models.position import Position
from backend.models.trade import Trade
from backend.models.signal import Signal
from backend.services.binance_service import BinanceService
from backend.core.daily_reset import DailyResetService
from backend.core.websocket_manager import ws_manager
from backend.services.alert_service import AlertService

logger = logging.getLogger(__name__)


class PositionManager:
    """Tracks open positions, monitors price, trails stops to BE at new BOS, closes on TP/SL."""

    def __init__(
        self,
        binance: BinanceService,
        order_service,
        daily_reset: DailyResetService,
        alert_service: AlertService,
        settings: dict,
        session_factory: Optional[Callable] = None,
        market_data_ws=None,
    ):
        self.session_factory = session_factory
        self.binance = binance
        self.orders = order_service
        self.daily_reset = daily_reset
        self.alerts = alert_service
        self.settings = settings
        # Real-time Binance market-data stream (bookTicker). When present, TP/SL
        # checks fire on every tick (sub-second) instead of the 30s REST poll.
        # The REST loop still runs as a fallback/reconciliation pass.
        self.market_data_ws = market_data_ws
        self._latest_price: dict[str, float] = {}   # symbol -> last bookTicker mid
        self._ws_symbols: set[str] = set()          # symbols currently subscribed
        self._check_locks: dict[str, asyncio.Lock] = {}  # per-symbol close lock
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Open-position snapshot shared by the high-frequency paths. The WS
        # bookTicker handler fires per message (10-100+/sec) and must NOT run a
        # DB query per tick — see _on_tick. Refreshed by the 30s _check_all_positions
        # pass and after every close.
        self._open_positions_cache: list[dict] = []
        # "paper" | "live" — set by BotEngine.start(). In live mode, closing a
        # position must also place a market order on the exchange (manual close)
        # or leave the exchange SL/TP to fire (auto TP/SL); the exchange SL/TP
        # must always be cancelled when the position closes.
        self._mode: str = "paper"

    @property
    def mode(self) -> str:
        return self._mode

    async def start(self):
        """Start the position manager loop."""
        self._running = True
        await self._reconcile_exchange_positions()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        """Stop the position manager loop."""
        self._running = False
        if self.market_data_ws:
            for symbol in list(self._ws_symbols):
                try:
                    await self.market_data_ws.unsubscribe(
                        f"{symbol.lower()}@bookTicker")
                except Exception:
                    pass
            self._ws_symbols.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _reconcile_exchange_positions(self) -> None:
        """Sync DB positions with actual exchange positions (live mode, on start).

        If the exchange holds a position the DB has no OPEN row for (e.g. an
        orphan exchange SL fired and opened a new position while the bot was
        stopped), record it as an OPEN position so the manager monitors it —
        otherwise it's an untracked exposure that can't be closed or trailed.
        """
        if self._mode != "live" or self.session_factory is None:
            return
        try:
            exchange_positions = await self.binance.get_all_positions()
        except Exception as e:
            logger.warning(f"Position reconcile: could not fetch exchange positions: {e}")
            return

        # current DB OPEN positions per symbol
        open_positions = await self._get_open_positions()
        db_open = {p["symbol"]: p for p in open_positions}

        # Reverse direction: the DB says OPEN but the exchange holds nothing —
        # the position was closed outside the bot (its exchange SL/TP fired
        # while the engine was down, or a manual close on Binance). Without
        # this the dashboard shows a phantom position with self-computed PnL
        # forever. Guard: ignore rows younger than 120s so a just-opened
        # position whose positionRisk row hasn't appeared yet isn't misjudged.
        exchange_symbols = {ep["symbol"] for ep in (exchange_positions or [])}
        for p in open_positions:
            if p["symbol"] in exchange_symbols:
                continue
            opened_at = p.get("opened_at")
            if opened_at is not None:
                age = (datetime.now(timezone.utc) - opened_at).total_seconds() \
                    if opened_at.tzinfo else 120.0
                if age < 120:
                    continue
            logger.warning(
                f"[{p['symbol']}] Reconcile: DB position {p['id']} OPEN but the "
                f"exchange holds no position — closing as EXTERNAL_CLOSE")
            exit_price = self._latest_price.get(p["symbol"], 0.0)
            if exit_price <= 0:
                try:
                    ticker = await self.binance.get_ticker(p["symbol"])
                    exit_price = float(ticker.get("price", 0) or 0)
                except Exception:
                    exit_price = 0.0
            if exit_price <= 0:
                exit_price = p["entry_price"]
            await self._close_position(p, "EXTERNAL_CLOSE", exit_price)

        for ep in exchange_positions or []:
            # get_all_positions() returns TRANSFORMED dicts (symbol/side/quantity/
            # entryPrice/leverage) — NOT the raw positionRisk keys. Reading
            # "positionAmt" here silently skipped every position, so an
            # exchange-only position (e.g. one left naked by a rejected BE stop)
            # was never recorded and stayed invisible to the dashboard.
            symbol = ep.get("symbol")
            qty = float(ep.get("quantity", 0) or 0)
            if qty <= 0 or symbol in db_open:
                continue  # nothing held, or already tracked
            side = ep.get("side") or "LONG"
            entry = float(ep.get("entryPrice", 0) or 0)
            leverage = int(ep.get("leverage", 1) or 1)

            # Adopt the exchange's own resting SL/TP so the engine can monitor
            # and manage the position instead of leaving it unmanaged.
            stop_loss = None
            take_profit = None
            try:
                for algo in await self.binance.get_open_algo_orders(symbol):
                    trig = float(algo.get("triggerPrice") or 0)
                    if trig <= 0:
                        continue
                    if algo.get("orderType") == "STOP_MARKET":
                        stop_loss = trig
                    elif algo.get("orderType") == "TAKE_PROFIT_MARKET":
                        take_profit = trig
            except Exception as e:
                logger.debug(f"[{symbol}] Reconcile: could not read resting algo orders: {e}")

            logger.warning(
                f"[{symbol}] Reconcile: exchange holds {side} {qty} @ {entry} "
                f"but no DB position — recording as OPEN "
                f"(SL={stop_loss}, TP={take_profit})")
            if stop_loss is None and take_profit is None:
                logger.error(
                    f"[{symbol}] Reconcile: untracked position has NO resting SL/TP "
                    f"on the exchange — it is unprotected")
            try:
                async with self.session_factory() as session:
                    session.add(Position(
                        symbol=symbol, side=side, entry_price=entry,
                        quantity=qty, leverage=leverage,
                        stop_loss=stop_loss, take_profit=take_profit,
                        initial_risk_distance=None, status="OPEN",
                        mode=self._mode,
                    ))
                    await session.commit()
            except Exception:
                logger.exception(f"[{symbol}] Reconcile: failed to record position")

    async def _loop(self):
        """Main loop — runs every 30 seconds."""
        reconcile_counter = 0
        while self._running:
            try:
                await self._check_all_positions()
                await self._broadcast_pnl_update()
                
                # Reconcile with exchange every 5 minutes (10 passes × 30s)
                reconcile_counter += 1
                if reconcile_counter >= 10:
                    reconcile_counter = 0
                    if self._mode == "live" and self.binance:
                        await self._reconcile_exchange_positions()
            except Exception as e:
                logger.error(f"PositionManager error: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _broadcast_pnl_update(self):
        """PHASE4_STEP2: pnl_update every pass (open positions + today's PnL)."""
        try:
            positions = await self._get_open_positions()
            stats = await self.daily_reset.get_daily_stats()
            await ws_manager.broadcast({"type": "pnl_update", "data": {
                "open_count": len(positions),
                "total_pnl_r": stats["total_pnl_r"],
                "total_pnl_usdt": stats["total_pnl_usdt"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }})
        except Exception as e:
            logger.debug(f"pnl_update broadcast failed: {e}")

    async def _get_open_positions(self) -> list[dict]:
        """Snapshot all open positions as plain dicts and refresh the cache.

        Every caller goes through here so `_open_positions_cache` cannot drift.
        """
        if self.session_factory is None:
            return []
        async with self.session_factory() as session:
            result = await session.execute(Position.__table__.select().where(Position.status == "OPEN"))
            rows = result.mappings().all()
            positions = [
                {
                    "id": r["id"],
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "entry_price": float(r["entry_price"]),
                    "quantity": float(r["quantity"]),
                    "stop_loss": float(r["stop_loss"]) if r["stop_loss"] is not None else None,
                    "take_profit": float(r["take_profit"]) if r["take_profit"] is not None else None,
                    "signal_id": r["signal_id"],
                    "opened_at": r["opened_at"],
                    "initial_risk_distance": (
                        float(r["initial_risk_distance"])
                        if r["initial_risk_distance"] is not None else None),
                }
                for r in rows
            ]
        # Cache for the high-frequency WS tick path (never queries the DB).
        self._open_positions_cache = positions
        return positions

    def _cached_open_positions(self) -> list[dict]:
        """Open positions from the last cache refresh. No DB access.

        Used by the bookTicker handler, which fires 10-100+/sec. Falls back to
        an empty list when the cache has never been populated (before the first
        30s pass) — the REST pass still catches those, so a missed tick window
        degrades latency, never correctness.
        """
        return self._open_positions_cache

    async def _sync_ws_subscriptions(self, open_positions: list[dict]):
        """Keep bookTicker subscriptions in sync with open-position symbols."""
        if not self.market_data_ws:
            return
        desired = {p["symbol"] for p in open_positions}
        # Subscribe new
        for symbol in desired - self._ws_symbols:
            try:
                await self.market_data_ws.watch_book_ticker(
                    symbol, self._make_tick_handler(symbol))
                self._ws_symbols.add(symbol)
                logger.info(f"[WS] Subscribed to {symbol}@bookTicker")
            except Exception as e:
                logger.warning(f"[WS] Subscribe {symbol} failed: {e}")
        # Unsubscribe closed
        for symbol in list(self._ws_symbols - desired):
            try:
                await self.market_data_ws.unsubscribe(f"{symbol.lower()}@bookTicker")
            except Exception:
                pass
            self._ws_symbols.discard(symbol)
            self._latest_price.pop(symbol, None)

    def _make_tick_handler(self, symbol: str):
        """bookTicker callback — record price and immediately check TP/SL."""
        async def handler(data: dict):
            if not self._running:
                return
            bid = float(data.get("bid_price", 0) or 0)
            ask = float(data.get("ask_price", 0) or 0)
            if bid <= 0 or ask <= 0:
                return
            mid = (bid + ask) / 2
            self._latest_price[symbol] = mid
            await self._on_tick(symbol, mid)
        return handler

    async def _on_tick(self, symbol: str, price: float):
        """React to a real-time tick: close the position if TP/SL is crossed.

        Uses a per-symbol lock so a burst of ticks can't double-close.
        BE-trailing is NOT done here (it needs 5m klines) — the REST loop
        still handles that plus reconciliation.

        NO DB READ HERE. bookTicker fires 10-100+ times/second and a local
        SELECT completes in ~1ms, so a per-tick `SELECT * FROM positions` was
        issuing hundreds of queries/second on the same pool the scan loop and
        the 30s pass need — the same event-loop starvation class as the old WS
        task leak (login hangs, missed scans, "the bot went dead"), and with no
        rate-limit signal to point at it. We read a snapshot cached by the 30s
        `_check_all_positions` pass instead; `_refresh_open_cache()` is called
        there and after any close.
        """
        lock = self._check_locks.setdefault(symbol, asyncio.Lock())
        if lock.locked():
            return  # a check for this symbol is already in flight
        async with lock:
            positions = self._cached_open_positions()
            for position in positions:
                if position["symbol"] != symbol:
                    continue
                tp_hit, sl_hit = self._check_tp_sl_hit(position, price)
                if tp_hit:
                    await self._close_position(position, "TP_HIT", price)
                elif sl_hit:
                    await self._close_position(position, "SL_HIT", price)

    async def _check_all_positions(self):
        """Check all open positions: price, BE trailing, TP/SL hits.

        REST fallback/reconciliation pass — also the only place BE-trailing
        runs (it needs 5m klines). When the WS stream is live it has usually
        already closed TP/SL hits; _close_position is idempotent via the
        status==OPEN guard.
        """
        open_positions = await self._get_open_positions()
        # Refresh the cache even if a later step raises, so the WS tick path is
        # never left reading a stale snapshot.
        self._open_positions_cache = open_positions
        await self._sync_ws_subscriptions(open_positions)

        for position in open_positions:
            symbol = position["symbol"]
            # Prefer the freshest WS price; fall back to REST ticker.
            current_price = self._latest_price.get(symbol, 0.0)
            if current_price <= 0:
                try:
                    ticker = await self.binance.get_ticker(symbol)
                    raw_price = ticker.get("price", 0) if isinstance(ticker, dict) else 0
                    current_price = float(raw_price) if raw_price else 0.0
                except Exception:
                    continue
            if current_price <= 0:
                continue

            # For TP/SL checks, use MARK PRICE — Binance triggers stops on
            # CONTRACT_PRICE (mark price for perpetuals). Checking against
            # last-trade price means the exchange fills the stop before our
            # price check sees the crossing, leaving the engine to discover
            # the close via the 5-min reconcile (EXTERNAL_CLOSE).
            try:
                mark_data = await self.binance.get_mark_price(symbol)
                mark_price = float(mark_data.get("markPrice", 0) or 0)
            except Exception:
                mark_price = current_price
            if mark_price <= 0:
                mark_price = current_price

            # Check BE trailing (REST-only; needs klines)
            await self._check_be_trailing(position, current_price)

            # Check profit lock trailing (locks in gains on deep-profit trades)
            await self._check_profit_trailing(position, current_price)

            # Check stale trades (close profitable trades that aren't reaching TP)
            await self._check_stale_trade(position)

            # Check if TP or SL was hit — use MARK PRICE to match Binance's trigger
            tp_hit, sl_hit = self._check_tp_sl_hit(position, mark_price)
            if tp_hit:
                await self._close_position(position, "TP_HIT", current_price)
            elif sl_hit:
                await self._close_position(position, "SL_HIT", current_price)

    async def _check_be_trailing(self, position: dict, current_price: float):
        """Move the stop to break-even once the trade is `be_trail_trigger_r` R
        in profit (default +0.5R, price-based).

        Previously this required a "new BOS after entry" (a 5m structural break).
        The 2026-10-15 audit showed that at the strategy's ~80% stop-out rate an
        un-trailed structural stop costs a full -1R on every failure, flipping
        expectancy negative (0.8×(-1R) + 0.2×3R = -0.2R/trade) — the BE stop was
        what made the same entries profitable (~+0.6R/trade). A price-based
        trigger is always reachable, unlike a structural break.

        Rules: one-way (stop only moves toward BE), only after entry is in
        profit by the trigger distance, and never before there is measurable
        risk (initial_risk_distance).
        """
        be_price = position["entry_price"]
        risk = position.get("initial_risk_distance")
        if risk is None or float(risk) <= 0:
            return  # cannot size the trigger without the frozen risk
        trigger_r = float(self.settings.get("be_trail_trigger_r", 0.5))
        trigger = float(risk) * trigger_r
        side = position["side"]

        if side == "LONG":
            if current_price < be_price + trigger:
                return  # not yet +trigger_r in profit
            if position["stop_loss"] is not None and position["stop_loss"] >= be_price:
                return  # already at/beyond BE
        else:  # SHORT
            if current_price > be_price - trigger:
                return
            if position["stop_loss"] is not None and position["stop_loss"] <= be_price:
                return

        logger.info(
            f"[{position['symbol']}] {side} is +{trigger_r}R in profit "
            f"(price {current_price}, trigger {trigger:.6f}) — trailing stop to BE")
        await self._trail_stop_to_be(position["id"], be_price)

    async def _check_profit_trailing(self, position: dict, current_price: float):
        """Trail the stop to lock in profit once the trade is deep enough in profit.

        Unlike BE-trailing (which just moves to entry), this trails the stop
        AHEAD of entry so a reversal locks in a real gain. Trigger and trail
        distance are configurable (profit_lock_trigger_r, profit_lock_trail_r).

        Rules: one-way (stop only moves in the profitable direction), only after
        the trigger threshold, never before measurable risk.
        """
        risk = position.get("initial_risk_distance")
        if risk is None or float(risk) <= 0:
            return
        trigger_r = float(self.settings.get("profit_lock_trigger_r", 1.0))
        trail_r = float(self.settings.get("profit_lock_trail_r", 0.5))
        trigger = float(risk) * trigger_r
        trail = float(risk) * trail_r
        side = position["side"]
        entry = float(position["entry_price"])

        if side == "LONG":
            profit = current_price - entry
            if profit < trigger:
                return  # not yet at the profit-lock threshold
            new_stop = current_price - trail
            # One-way: only move stop UP for a LONG
            if position["stop_loss"] is not None and float(position["stop_loss"]) >= new_stop:
                return
        else:  # SHORT
            profit = entry - current_price
            if profit < trigger:
                return
            new_stop = current_price + trail
            # One-way: only move stop DOWN for a SHORT
            if position["stop_loss"] is not None and float(position["stop_loss"]) <= new_stop:
                return

        locked_r = (new_stop - entry) / float(risk) if side == "LONG" else (entry - new_stop) / float(risk)
        logger.info(
            f"[{position['symbol']}] {side} profit lock: +{profit/float(risk):.2f}R, "
            f"trailing stop to {new_stop:.6f} (locks +{locked_r:.2f}R)")
        await self._trail_stop_to_be(position["id"], new_stop)

    async def _check_stale_trade(self, position: dict):
        """Close stale profitable trades that aren't reaching TP.

        A trade sitting at +0.3R for hours is just tying up margin and a
        position slot. If the trade is in profit but hasn't hit TP after
        `stale_trade_hours`, close it and bank whatever profit exists.
        """
        stale_hours = float(self.settings.get("stale_trade_hours", 24.0))
        if stale_hours <= 0:
            return  # disabled

        opened_at = position.get("opened_at")
        if opened_at is None:
            return
        if opened_at.tzinfo is None:
            from datetime import timezone as tz
            opened_at = opened_at.replace(tzinfo=tz.utc)

        from datetime import datetime, timezone
        age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
        if age_hours < stale_hours:
            return

        # Only close if in profit (don't close losers early — they should hit their SL)
        entry = float(position["entry_price"])
        side = position["side"]
        risk = position.get("initial_risk_distance")

        # Get current price
        current_price = None
        if self.binance is not None:
            try:
                ticker = await self.binance.get_ticker(position["symbol"])
                current_price = float(ticker.get("price", 0))
            except Exception:
                return

        if current_price is None or current_price <= 0:
            return

        if side == "LONG":
            in_profit = current_price > entry
        else:
            in_profit = current_price < entry

        if not in_profit:
            return  # let losers run to their SL

        logger.info(
            f"[{position['symbol']}] {side} stale {age_hours:.1f}h in profit — "
            f"closing to bank gains (price {current_price}, entry {entry})")
        await self._close_position(position, "STALE_EXIT", current_price)

    async def _trail_stop_to_be(self, position_id: int, new_stop: float):
        """Move stop loss to break-even (entry price). One-way, idempotent.

        In live mode this also modifies the exchange STOP_MARKET order so the
        stop actually protects the position — previously BE-trailing only moved
        the DB stop, leaving the exchange stop at the original (wider) level.
        """
        if self.session_factory is None:
            return
        symbol = None
        side = None
        qty = None
        async with self.session_factory() as session:
            position = await session.get(Position, position_id)
            if not position or position.status != "OPEN":
                return
            if position.side == "LONG" and position.stop_loss is not None and float(position.stop_loss) >= new_stop:
                return  # already at/beyond BE
            if position.side == "SHORT" and position.stop_loss is not None and float(position.stop_loss) <= new_stop:
                return
            old_stop = position.stop_loss
            position.stop_loss = new_stop
            symbol = position.symbol
            side = position.side
            qty = float(position.quantity)
            await session.commit()

        logger.info(f"[{symbol}] Trailing stop to BE: {old_stop} → {new_stop}")

        # LIVE mode: place the new stop FIRST, then cancel the old one — and if
        # the placement fails (e.g. Binance -2021 "order would immediately
        # trigger" when the BE level is already at market), REVERT the DB stop.
        # The DB stop must never be tighter than the exchange's: otherwise the
        # engine's own price check trips, "closes" the position in the DB, and
        # leaves it open and unprotected on the exchange (observed live).
        if self._mode == "live" and symbol and qty:
            try:
                new_order = await self.binance.modify_position_stop(symbol, side, new_stop, qty)
            except Exception as e:
                logger.error(f"[{symbol}] Failed to trail exchange stop to BE: {e}")
                await self._restore_stop(position_id, old_stop)
                return
            new_algo_id = new_order.get("orderId") if isinstance(new_order, dict) else None
            await self._cancel_active_sl(position_id, symbol,
                                         keep_algo_id=new_algo_id,
                                         keep_trigger=new_stop)

        if self.alerts:
            await self.alerts.be_trailing(symbol)

    async def _restore_stop(self, position_id: int, old_stop) -> None:
        """Revert a DB stop change after a failed exchange stop placement.

        Keeps DB and exchange in agreement — a DB stop with no matching exchange
        stop is what let the engine report an exit that never happened.
        """
        if self.session_factory is None or old_stop is None:
            return
        try:
            async with self.session_factory() as session:
                row = await session.get(Position, position_id)
                if row and row.status == "OPEN":
                    row.stop_loss = old_stop
                    symbol = row.symbol
                    await session.commit()
                    logger.warning(
                        f"[{symbol}] DB stop reverted to {old_stop} — exchange "
                        f"stop unchanged (BE trail not applied)")
        except Exception:
            logger.exception("Failed to revert stop for position %s", position_id)

    async def _cancel_active_sl(self, position_id: int, symbol: str,
                                 keep_algo_id=None, keep_trigger=None) -> None:
        """Cancel the symbol's resting STOP_MARKET algo orders (before re-placing).

        WHY THIS SWEEPS THE EXCHANGE (not the orders table): the trail path
        (`modify_position_stop`) posts a new STOP_MARKET but never writes its
        `algoId` back to the `orders` table, so the table only ever knows about
        the ORIGINAL entry SL. Cancelling from the table therefore cancelled a
        dead order and left every trailed stop armed — stacking stops until the
        widest one (the original entry stop) closed the position before any
        profit-lock stop could. Sweeping `openAlgoOrders` — the same sweep used
        on close — cancels every resting stop regardless of how it got there.

        `keep_algo_id` / `keep_trigger` identify the stop just placed by this
        call; neither may be cancelled (place-then-cancel would otherwise kill
        the new stop too). The stop is matched on BOTH the algoId AND the
        trigger price, because the id returned by the place response has not
        always matched the id in the list response — matching on id alone made
        the sweep cancel the NEW stop and keep the stale one, so the DB
        recorded a profit lock the exchange never had (observed live on
        DOTUSDT: DB stop 1.18366, exchange stop 1.1522, and the one-way guard
        then blocked the re-trail, so the position would have ridden to a full
        loss).

        FAIL-SAFE: if we cannot identify the new stop by EITHER id or price,
        the sweep is skipped entirely. Sweeping blind would cancel the stop we
        just placed along with the stale ones, leaving the position NAKED on the
        exchange — strictly worse than the extra stops this sweep exists to
        remove. Stacked reduce-only stops can only close the position early;
        no stop at all lets it run unbounded.
        """
        if self.session_factory is None or self.binance is None:
            return

        keep_id = str(keep_algo_id) if keep_algo_id is not None else None
        keep_px = None
        if keep_trigger is not None:
            try:
                keep_px = float(keep_trigger)
            except (TypeError, ValueError):
                keep_px = None
        if keep_id is None and keep_px is None:
            logger.warning(
                f"[{symbol}] Stop sweep skipped — the exchange gave us neither an "
                f"algoId nor a trigger price for the stop just placed, so it "
                f"cannot be distinguished from the stale ones. Leaving all resting "
                f"stops armed rather than risk cancelling the live one.")
            return

        # The exchange stores the tick-ROUNDED trigger, so compare with a
        # relative tolerance rather than for exact equality.
        px_tol = abs(keep_px) * 0.001 if keep_px else 0.0

        try:
            for algo in await self.binance.get_open_algo_orders(symbol):
                if str(algo.get("orderType")) != "STOP_MARKET":
                    continue
                algo_id = algo.get("algoId")
                if algo_id is None:
                    continue
                if keep_id is not None and str(algo_id) == keep_id:
                    continue
                if keep_px is not None:
                    trig = float(algo.get("triggerPrice") or 0)
                    if trig > 0 and abs(trig - keep_px) <= px_tol:
                        continue  # this is the stop we just placed
                try:
                    await self.binance.cancel_algo_order(symbol, int(algo_id))
                    logger.info(
                        f"[{symbol}] Cancelled stale SL algo order {algo_id} "
                        f"(trigger {algo.get('triggerPrice')}) before re-placing")
                except Exception as e:
                    logger.debug(f"[{symbol}] Cancel stale SL {algo_id} (likely filled): {e}")
        except Exception:
            logger.exception("Failed to cancel active SL for position %s", position_id)

    def _check_tp_sl_hit(self, position: dict, current_price: float) -> tuple[bool, bool]:
        """Check if TP or SL was hit."""
        if position["side"] == "LONG":
            tp_hit = current_price >= position["take_profit"] if position["take_profit"] else False
            sl_hit = current_price <= position["stop_loss"] if position["stop_loss"] else False
        else:  # SHORT
            tp_hit = current_price <= position["take_profit"] if position["take_profit"] else False
            sl_hit = current_price >= position["stop_loss"] if position["stop_loss"] else False

        return tp_hit, sl_hit

    async def _close_position(self, position: dict, reason: str, exit_price: float):
        """Close position: record trade, update DB, log P&L.
        
        For live positions, fetches actual realized PnL from Binance (includes
        fees and real fill prices). Paper positions use synthetic PnL.
        """
        if self.session_factory is None:
            return

        async with self.session_factory() as session:
            row = await session.get(Position, position["id"])
            if not row or row.status != "OPEN":
                return  # already closed elsewhere

            symbol = row.symbol
            entry = float(row.entry_price)
            qty = float(row.quantity)
            side = row.side

            # Calculate realized P&L
            # LIVE: try to fetch actual realized PnL from Binance (includes fees)
            # PAPER: synthetic PnL (no fees)
            pnl_usdt = None
            if self._mode == "live" and self.binance is not None:
                try:
                    # Scope income to THIS position's lifetime — the 7-day
                    # default window would also sum an earlier position's PnL
                    # on the same symbol.
                    opened_at = row.opened_at
                    start_ms = None
                    if opened_at is not None:
                        if opened_at.tzinfo is None:
                            opened_at = opened_at.replace(tzinfo=timezone.utc)
                        start_ms = int(opened_at.timestamp() * 1000) - 60_000
                    income = await self.binance.get_position_income(
                        row.symbol, start_time=start_ms)
                    # Only trust the exchange PnL if income events were found.
                    # A manual close queries immediately — Binance may not have
                    # recorded the income yet, returning totalPnl=0 with 0 events.
                    # Previously that 0 was treated as valid, recording $0 profit
                    # on profitable manual closes.
                    if income.get("events", 0) > 0:
                        pnl_usdt = income.get("totalPnl", 0)
                        logger.info(
                            "[%s] Using exchange PnL: %.4f USDT (fees included)",
                            row.symbol, pnl_usdt
                        )
                    else:
                        logger.debug(
                            "[%s] No income events yet — using synthetic PnL", row.symbol)
                except Exception as exc:
                    logger.debug("get_position_income failed, using synthetic PnL: %s", exc)
            
            # Fallback: synthetic PnL (paper mode, or live fetch failed)
            if pnl_usdt is None:
                if side == "LONG":
                    pnl_usdt = (exit_price - entry) * qty
                else:
                    pnl_usdt = (entry - exit_price) * qty

                # Estimate fees for paper mode (realistic simulation)
                # Binance taker fee with BNB discount: 0.045% per side (entry + exit)
                # Standard rate is 0.05%, but BNB holders get 10% discount
                if self._mode == "paper":
                    notional_entry = entry * qty
                    notional_exit = exit_price * qty
                    fee_rate = 0.00045  # 0.045% taker fee (BNB discount)
                    fees = (notional_entry + notional_exit) * fee_rate
                    pnl_usdt -= fees
                    logger.debug(
                        "[%s] Paper PnL after fees: %.4f (raw: %.4f, fees: %.4f)",
                        row.symbol, pnl_usdt, pnl_usdt + fees, fees
                    )

            # Calculate P&L in R units — against the FROZEN initial risk
            # (entry-to-original-SL), NOT the live stop_loss. BE-trailing moves
            # stop_loss toward entry; using it would shrink the denominator and
            # inflate |R| (a breakeven stop-out would read as a huge R move).
            if row.initial_risk_distance is not None and float(row.initial_risk_distance) > 0:
                risk_distance = float(row.initial_risk_distance)
            else:
                risk_distance = abs(entry - float(row.stop_loss)) if row.stop_loss else 0.01
            risk_amount = risk_distance * qty
            pnl_r = pnl_usdt / risk_amount if risk_amount > 0 else 0.0

            # Get strategy type from signal
            strategy_type = "UNKNOWN"
            if row.signal_id:
                sig = await session.get(Signal, row.signal_id)
                if sig:
                    strategy_type = sig.strategy_type

            trade = Trade(
                position_id=row.id,
                symbol=row.symbol,
                side=side,
                entry_price=entry,
                exit_price=exit_price,
                quantity=qty,
                pnl_usdt=pnl_usdt,
                pnl_r=pnl_r,
                exit_reason=reason,
                strategy_type=strategy_type,
                mode=self._mode,
                closed_at=datetime.now(timezone.utc),
            )
            session.add(trade)

            # ATOMIC CLOSE CLAIM. The in-memory `row.status != "OPEN"` check
            # above is only a fast path: between it and this write we await two
            # HTTP/DB round-trips (get_position_income, session.get(Signal)), so
            # the WS tick path and the 30s REST / 5-min reconcile paths can both
            # observe OPEN and both proceed. That produced duplicate Trade rows
            # and DOUBLED the daily R total, which can trip the -3R hard stop on
            # phantom R and halt trading for the day.
            #
            # This conditional UPDATE is the real mutual exclusion: only one
            # transaction can transition OPEN -> CLOSED. The loser sees
            # rowcount == 0 and rolls back, taking its Trade with it.
            claimed = await session.execute(
                update(Position)
                .where(Position.id == position["id"], Position.status == "OPEN")
                .values(status="CLOSED", closed_at=datetime.now(timezone.utc))
            )
            if claimed.rowcount == 0:
                await session.rollback()
                logger.warning(
                    "[%s] Position %s was already closed by another path — "
                    "discarding duplicate close (%s)", symbol, position["id"], reason)
                return

            await session.commit()
            side = row.side
            qty = float(row.quantity)

        # LIVE-mode exchange hygiene. In live mode the exchange holds resting
        # reduce-only SL/TP orders for this position. When it closes we must:
        #   - always cancel the resting SL/TP (an orphan SL is a phantom-position
        #     bomb: once restarted it fires and opens a NEW opposite position)
        #   - place a MARKET close ONLY when the exchange doesn't already know
        #     the position is gone (TP_HIT/SL_HIT/EXTERNAL_CLOSE: an exchange
        #     order or an outside close already flattened it — cancelling the
        #     resting orders suffices; a redundant market close would OPEN an
        #     opposite position. MANUAL_CLOSE/CLOSED_LOOP/force: we must
        #     actively close on the exchange).
        if self._mode == "live" and reason in ("TP_HIT", "SL_HIT"):
            await self._cancel_exchange_orders(position["id"], position["symbol"])
            # The engine's own price check can fire without the exchange's
            # resting order having filled (observed live: the BE stop placement
            # was rejected by Binance, yet the DB stop tripped the engine's
            # check — the position stayed open and NAKED on the exchange).
            # Verify and flatten instead of assuming the exchange closed it.
            await self._ensure_flat(position["symbol"])
        elif self._mode == "live" and reason == "EXTERNAL_CLOSE":
            await self._cancel_exchange_orders(position["id"], position["symbol"])
        elif self._mode == "live":
            # ORDER MATTERS: close on the exchange FIRST, cancel protection
            # second. The previous order (cancel, then market-close) left a
            # window where a failed close produced an open exchange position
            # with zero SL/TP — naked, while the DB already said CLOSED.
            # _market_close swallows its own exceptions, so it reports whether
            # it actually flattened; if it did not, restore a protective stop
            # rather than leaving the position unprotected.
            closed = await self._market_close(symbol, side, qty)
            await self._cancel_exchange_orders(position["id"], position["symbol"])
            if not closed:
                logger.error(
                    "[%s] Market close FAILED and protection has now been "
                    "cancelled — restoring an emergency stop so the position is "
                    "not left naked", symbol)
                await self._restore_emergency_stop(symbol, side, qty)

        # Update daily summary + alert outside the session
        await self.daily_reset.update_daily_summary(pnl_r, pnl_usdt)
        await ws_manager.broadcast({"type": "position_closed", "data": {
            "position_id": position["id"], "symbol": position["symbol"],
            "pnl_r": pnl_r, "exit_reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat()}})

        if self.alerts:
            if reason == "TP_HIT":
                await self.alerts.tp_hit(symbol, pnl_r, pnl_usdt)
            elif reason == "SL_HIT":
                await self.alerts.sl_hit(symbol, pnl_r, pnl_usdt)

        logger.info(f"[{symbol}] Position closed: {reason}, PnL: {pnl_usdt:.2f} USDT ({pnl_r:.2f}R)")

    async def _cancel_exchange_orders(self, position_id: int, symbol: str | None = None) -> None:
        """Cancel all resting exchange orders linked to a position (live mode).

        Looks up the SL/TP orders recorded in the `orders` table (by position_id)
        and cancels them on Binance, then sweeps openAlgoOrders for the symbol to
        catch any resting order NOT recorded there (BE-trail stops, manual
        re-placements). Best-effort — an already-filled order raises -2011 which
        is benign. Prevents orphan SL/TP orders from firing after the position
        closes.
        """
        if self.session_factory is None:
            return
        try:
            from backend.models.order import Order as OrderModel
            from sqlalchemy import select
            async with self.session_factory() as session:
                rows = (await session.execute(
                    select(OrderModel).where(
                        OrderModel.position_id == position_id,
                        OrderModel.order_type.in_(["STOP_MARKET", "TAKE_PROFIT_MARKET"]),
                    ))).scalars().all()
            if symbol is None and rows:
                symbol = rows[0].symbol
            for row in rows:
                oid = row.binance_order_id
                if not oid:
                    continue
                try:
                    # SL/TP are ALGO (conditional) orders — cancel via the algo
                    # endpoint (the regular /fapi/v1/order DELETE silently fails
                    # on them, leaving orphan stops that fire after close).
                    await self.binance.cancel_algo_order(row.symbol, int(oid))
                    logger.info(f"[{row.symbol}] Cancelled exchange algo order {oid} ({row.order_type})")
                except Exception as e:
                    # -2011/-4048 = unknown order (already filled/cancelled) — benign
                    logger.debug(f"[{row.symbol}] Cancel algo order {oid} failed (likely filled): {e}")

            # Sweep: cancel ANY resting algo order still open on this symbol.
            # Orders placed outside the orders-table logging (BE-trail stops,
            # manual re-placements) are invisible to the position-linked pass
            # above and would survive as orphans that can close a FUTURE
            # position prematurely (observed: a BE-trail stop and a manually
            # re-placed SL outlived their closed positions). The engine only
            # ever holds one position per symbol, so at close time every
            # resting algo order on the symbol is an orphan.
            if symbol:
                try:
                    for algo in await self.binance.get_open_algo_orders(symbol):
                        algo_id = algo.get("algoId")
                        if not algo_id:
                            continue
                        try:
                            await self.binance.cancel_algo_order(symbol, int(algo_id))
                            logger.info(
                                f"[{symbol}] Sweep-cancelled resting algo order {algo_id} "
                                f"({algo.get('orderType')}) after position close")
                        except Exception as e:
                            logger.debug(f"[{symbol}] Sweep cancel {algo_id} failed: {e}")
                except Exception:
                    logger.exception("Algo-order sweep failed after position close")
        except Exception:
            logger.exception("Failed to cancel exchange orders for position %s", position_id)

    async def _market_close(self, symbol: str, side: str, qty: float) -> bool:
        """Place a MARKET order to close a live position (manual/closed-loop exit).

        Opposite side of the position: LONG -> SELL, SHORT -> BUY.

        Returns True when the close order was successfully placed. Callers rely
        on this: an earlier version returned None and swallowed every failure,
        so the caller could not tell a successful flatten from a failed one and
        went on to cancel the position's protection.
        """
        close_side = "SELL" if side == "LONG" else "BUY"
        try:
            await self.binance.place_market_order(symbol, close_side, qty)
            logger.info(f"[{symbol}] Live market close placed: {close_side} {qty}")
            return True
        except Exception as e:
            logger.error(f"[{symbol}] Live market close failed: {e}")
            return False

    async def _restore_emergency_stop(self, symbol: str, side: str, qty: float) -> None:
        """Last-resort protection: re-arm a stop on a position we failed to close.

        Used when the market close failed AND we have already cancelled the
        position's resting SL/TP. A position with no stop on the exchange is
        unbounded — it is better to close prematurely at a poor price than to
        let it run. The stop is placed a conservative distance from the last
        known price, and the failure is surfaced loudly so the operator can act.
        """
        try:
            price = self._latest_price.get(symbol, 0.0)
            if price <= 0:
                ticker = await self.binance.get_ticker(symbol)
                price = float((ticker or {}).get("price", 0) or 0)
            if price <= 0:
                raise RuntimeError("no price available to place an emergency stop")

            # 1% away from the last known price, on the losing side.
            stop_price = price * 0.99 if side == "LONG" else price * 1.01
            await self.binance.modify_position_stop(symbol, side, stop_price, qty)
            logger.error(
                "[%s] EMERGENCY STOP re-armed at %.6g after a failed market close "
                "— this position was left unprotected", symbol, stop_price)
            if self.alerts:
                await self.alerts.error(
                    f"{symbol}: market close FAILED, emergency stop placed at "
                    f"{stop_price:.6g}. Manual intervention required.")
        except Exception as e:
            logger.critical(
                "[%s] CRITICAL — market close failed AND the emergency stop could "
                "not be placed (%s). The position is NAKED on the exchange.",
                symbol, e)
            if self.alerts:
                try:
                    await self.alerts.error(
                        f"{symbol}: CLOSE FAILED and no stop could be placed — "
                        f"position is NAKED. Intervene immediately.")
                except Exception:
                    pass

    async def _ensure_flat(self, symbol: str) -> None:
        """Close any exchange position left after an engine-detected exit.

        The engine detects TP/SL crosses from its own price feed; that does NOT
        guarantee the exchange's resting order filled (a rejected BE stop, or a
        wick the exchange trigger didn't see). Without this check the DB is
        closed while the exchange keeps an unprotected position — observed live
        on XRPUSDT (left naked from 11:40 to 13:09).
        """
        try:
            pos = await self.binance.get_position(symbol)
        except Exception as e:
            logger.error(f"[{symbol}] Post-exit verification failed: {e}")
            return
        qty = float((pos or {}).get("quantity", 0) or 0)
        if qty > 0:
            logger.warning(
                f"[{symbol}] Exchange still holds {pos.get('side')} {qty} after a "
                f"detected exit — market-closing to avoid naked exposure")
            await self._market_close(symbol, pos.get("side") or "LONG", qty)

    async def close_all_positions(self, reason: str = "MANUAL_CLOSE") -> int:
        """Close ALL open positions at market price (manual stop / kill switch).

        In live mode this places real MARKET orders to close each position on
        the exchange. In paper mode it records the close as usual. Returns the
        number of positions closed.
        """
        if self.session_factory is None:
            return 0

        positions = await self._get_open_positions()
        closed = 0
        for pos in positions:
            symbol = pos["symbol"]
            try:
                # fetch current market price
                ticker = await self.binance.get_ticker(symbol)
                price = float(ticker.get("price", 0))
                if price <= 0:
                    continue

                # _close_position handles live-mode market close + SL/TP cancel
                await self._close_position(pos, reason, price)
                closed += 1
            except Exception as e:
                logger.error(f"[{symbol}] close_all failed: {e}")

        if self.alerts and closed > 0:
            await self.alerts.signal(
                f"🔒 Closed {closed} position(s) ({reason})",
                level="INFO",
            )
        return closed

    async def close_position(self, position_id: int, reason: str = "MANUAL_CLOSE") -> bool:
        """Close a SINGLE position at market price.

        In live mode this places a real MARKET order on the exchange and cancels
        the SL/TP. In paper mode it records the close. Returns True if closed.
        """
        if self.session_factory is None:
            return False

        # Fetch the position
        async with self.session_factory() as session:
            row = await session.get(Position, position_id)
            if not row or row.status != "OPEN":
                logger.warning(f"Position {position_id} not found or already closed")
                return False
            pos = {
                "id": row.id,
                "symbol": row.symbol,
                "side": row.side,
                "entry_price": float(row.entry_price),
                "quantity": float(row.quantity),
                "stop_loss": float(row.stop_loss) if row.stop_loss else None,
                "take_profit": float(row.take_profit) if row.take_profit else None,
            }

        symbol = pos["symbol"]
        try:
            # Fetch current market price
            ticker = await self.binance.get_ticker(symbol)
            price = float(ticker.get("price", 0))
            if price <= 0:
                logger.error(f"[{symbol}] Could not fetch price for close")
                return False

            # _close_position handles live-mode market close + SL/TP cancel
            await self._close_position(pos, reason, price)
            logger.info(f"[{symbol}] Position {position_id} closed manually at {price}")
            return True
        except Exception as e:
            logger.error(f"[{symbol}] close_position failed: {e}")
            return False
