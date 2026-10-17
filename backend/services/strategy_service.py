"""
Strategy Service — evaluates incoming signals and routes them to the Order Service.

Owns the decision layer (FUBOT_STEPS PHASE3_STEP3): open-position guard, max
positions, signal age, price-deviation gate, risk validation, then execution —
simulated fills in paper mode, real Binance orders + DB records in live mode.

NOTE: paper mode DOES create Position/signal-TRIGGERED records (with simulated
order fills). FUBOT_STEPS PHASE3_STEP3 says paper creates no records, but
PHASE3_STEP7's 72h paper run requires logged trades and the dashboard needs
them — the STEP7 requirement wins.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from backend.models.position import Position as PositionModel
from backend.models.signal import Signal as SignalModel

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    decision: str              # EXECUTE | SKIP
    skip_reason: Optional[str] = None
    plan: Optional[dict] = None
    position_id: Optional[int] = None
    paper: bool = False


class StrategyService:
    def __init__(self, risk_svc, order_svc, binance=None,
                 session_factory=None, settings: Optional[dict] = None) -> None:
        self.risk_svc = risk_svc
        self.order_svc = order_svc
        self.binance = binance
        self.session_factory = session_factory
        self.settings = settings or {}

    # ---- pure planning (kept from the earlier refactor, unit-tested) ----

    def evaluate(
        self,
        symbol: str,
        side: str,
        entry_zone_min: Decimal,
        entry_zone_max: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        rr_ratio: Decimal,
        volume_confirmed: bool,
        balance_usdt: Decimal,
        risk_percent: Decimal,
        min_rr: Decimal,
    ) -> Optional[dict]:
        """
        Validate and plan the entry.  Returns an order plan dict or None when the
        setup fails filters.
        """
        if not volume_confirmed:
            logger.info("Skipping %s: volume not confirmed", symbol)
            return None

        if not self.risk_svc.validate_rr_ratio(
            entry_zone_min, stop_loss, take_profit, min_rr
        ):
            logger.info("Skipping %s: R:R below minimum", symbol)
            return None

        size = self.risk_svc.calc_position_size(
            balance_usdt, risk_percent, entry_zone_min, stop_loss
        )
        if size == Decimal("0"):
            return None

        return {
            "symbol": symbol,
            "side": side,
            "entry_zone_min": entry_zone_min,
            "entry_zone_max": entry_zone_max,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "size": size,
            "rr_ratio": rr_ratio,
        }

    # ---- decision layer driven by BotEngine -----------------------------

    async def evaluate_signal(self, signal, paper_mode: bool = False) -> TradeDecision:
        symbol = signal.symbol
        side = "BUY" if signal.direction == "LONG" else "SELL"

        # 0. Only fresh PENDING signals are actionable
        if signal.status != "PENDING":
            return TradeDecision("SKIP", skip_reason=f"Signal is {signal.status}")

        # 1. Open position on this symbol? (exchange AND paper positions in DB)
        existing = await self.order_svc.get_current_position(symbol)
        if existing and existing.get("quantity", 0) > 0:
            return TradeDecision("SKIP", skip_reason=f"Position already open on {symbol}")
        if self.session_factory is not None and await self._has_open_db_position(symbol):
            return TradeDecision("SKIP", skip_reason=f"Position already open on {symbol}")

        # 2. Max simultaneous positions
        max_positions = int(self.settings.get("max_positions", 3))
        if self.session_factory is not None:
            open_count = await self._count_open_positions()
            if open_count >= max_positions:
                return TradeDecision(
                    "SKIP", skip_reason=f"Max positions ({max_positions}) already open")

        # 3. Signal age (normalize naive timestamps defensively)
        max_age_min = float(self.settings.get("signal_max_age_minutes", 60))
        created_at = signal.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
        if age_min > max_age_min:
            signal.status = "EXPIRED"
            await self._mark_signal(signal.db_id, "EXPIRED")
            return TradeDecision("SKIP", skip_reason=f"Signal older than {max_age_min:.0f} min")

        # 4. Price deviation from the entry zone (market price only gates whether
        #    the setup is still reachable — the FILL uses the zone midpoint).
        market_price = await self._current_price(symbol)
        if market_price is None:
            return TradeDecision("SKIP", skip_reason="Could not fetch current price")
        if signal.direction == "LONG":
            if market_price > float(signal.entry_zone_max) * 1.01:
                return TradeDecision("SKIP", skip_reason="Price moved too far above zone")
            if market_price < float(signal.entry_zone_min) * 0.99:
                return TradeDecision("SKIP", skip_reason="Price already below zone")
        else:
            if market_price < float(signal.entry_zone_min) * 0.99:
                return TradeDecision("SKIP", skip_reason="Price moved too far below zone")
            if market_price > float(signal.entry_zone_max) * 1.01:
                return TradeDecision("SKIP", skip_reason="Price already above zone")

        # 4b. The stop (and target) must still be on the CORRECT SIDE of the
        #     market. Entries fill at market, so a setup whose stop has already
        #     been passed is void — and placing the SL then fails with 400
        #     "would immediately trigger", aborting the flow AFTER the entry
        #     filled and leaving the position NAKED (live 2026-10-15: XRPUSDT
        #     and BTCUSDT). The abs()-based R:R check cannot see this.
        stop_px = float(signal.stop_loss)
        tp_px = float(signal.take_profit)
        if signal.direction == "LONG":
            void_reason = ("stop already breached by the market"
                           if stop_px >= market_price else
                           "target already reached" if tp_px <= market_price else None)
        else:
            void_reason = ("stop already breached by the market"
                           if stop_px <= market_price else
                           "target already reached" if tp_px >= market_price else None)
        if void_reason:
            signal.status = "SKIPPED"
            await self._mark_signal(signal.db_id, "SKIPPED", f"Setup void: {void_reason}")
            return TradeDecision("SKIP", skip_reason=f"Setup void: {void_reason}")

        # Fill at the signal's entry-zone midpoint, NOT the live market price.
        # Filling at market could put entry ~at the stop, shrinking the realized
        # risk to near zero and turning any noise into -2R. The zone midpoint
        # makes entry-to-SL a deterministic, intended distance.
        entry = (signal.entry_zone_min + signal.entry_zone_max) / 2

        # 5. Risk validation + sizing
        min_rr = Decimal(str(self.settings.get("rr_min", 3.0)))
        if not self.risk_svc.validate_rr_ratio(
            entry, signal.stop_loss, signal.take_profit, min_rr,
            direction=signal.direction,
        ):
            signal.status = "SKIPPED"
            await self._mark_signal(signal.db_id, "SKIPPED", "R:R below minimum")
            return TradeDecision("SKIP", skip_reason="R:R below minimum")

        # 5b. LIVE fills happen at MARKET price, so the realised R:R is set by
        # the price we actually get, not by the zone midpoint. Re-validate at
        # the live price and skip when the edge is gone — XRP 2026-10-15 had a
        # zone of 1.3258-1.3275 (planned 3.25:1) but filled at 1.3131, leaving
        # ~1.1:1 while stop/TP stayed at the planned levels.
        if not paper_mode and not self.risk_svc.validate_rr_ratio(
            Decimal(str(market_price)), signal.stop_loss, signal.take_profit, min_rr,
            direction=signal.direction,
        ):
            signal.status = "SKIPPED"
            await self._mark_signal(
                signal.db_id, "SKIPPED", "R:R below minimum at live price")
            return TradeDecision(
                "SKIP", skip_reason="R:R below minimum at live market price")

        # Min-risk guard: a degenerate entry-to-stop distance makes any normal
        # adverse move a huge |R| (this caused -2R paper losses when entry
        # filled ~at the stop). Require the risk to be at least 0.05% of the
        # entry price (relative, so it scales across symbols) — override with
        # the min_risk_pct setting.
        risk_distance = abs(float(entry) - float(signal.stop_loss))
        min_risk_pct = float(self.settings.get("min_risk_pct", 0.0005))
        min_risk_price = float(entry) * min_risk_pct
        if risk_distance <= 0 or risk_distance < min_risk_price:
            signal.status = "SKIPPED"
            await self._mark_signal(
                signal.db_id, "SKIPPED",
                f"Risk distance {risk_distance:.6f} below min {min_risk_price:.6f}")
            return TradeDecision(
                "SKIP", skip_reason=f"Risk distance {risk_distance:.6f} too small")

        balance = await self._account_balance(paper_mode)
        if balance is None:
            return TradeDecision("SKIP", skip_reason="Could not fetch account balance")
        risk_percent = Decimal(str(self.settings.get("risk_percent", 1.0)))
        size = self.risk_svc.calc_position_size(
            balance, risk_percent, Decimal(str(entry)), signal.stop_loss
        )
        # Safety backstop: never let a single position exceed a fraction of the
        # account, regardless of how it was sized. The fraction is the
        # `max_position_notional_pct` setting (Settings UI). When the cap binds
        # it LOWERS the effective risk well below risk_percent — with ~1% stops
        # a 50% cap turns a 5% risk target into ~$1-2 per trade — so log the
        # real numbers instead of silently overriding the risk setting.
        notional_pct = Decimal(str(self.settings.get("max_position_notional_pct", 50.0)))
        capped = self.risk_svc.cap_position_notional(
            size, Decimal(str(entry)), balance, notional_pct / Decimal("100"))
        if capped < size:
            intended_risk = balance * risk_percent / Decimal("100")
            eff_risk = capped * abs(Decimal(str(entry)) - signal.stop_loss)
            try:
                eff_pct = float(eff_risk / balance * 100) if balance else 0.0
            except Exception:
                eff_pct = 0.0
            logger.warning(
                "[%s] Notional cap %.0f%% of balance binds — position cut from "
                "%.6f to %.6f qty; risk %.2f -> %.2f USDT (%.2f%% of balance "
                "instead of %.2f%%). Raise 'Max Position Notional' to size by "
                "Risk Per Trade.",
                symbol, float(notional_pct), float(size), float(capped),
                float(intended_risk), float(eff_risk), eff_pct, float(risk_percent))
        size = capped
        if size == Decimal("0"):
            return TradeDecision("SKIP", skip_reason="Position size too small")

        plan = {
            "symbol": symbol,
            "direction": signal.direction,
            "entry": entry,
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "size": float(size),
            "rr_ratio": float(signal.rr_ratio),
            "initial_risk_distance": risk_distance,
        }

        # 6. Execute — simulated fills in paper, real Binance orders in live.
        #
        # PAPER: the Position row is created first (simulated fill at the plan).
        # LIVE: a LIMIT order is placed at the entry-zone midpoint and we wait
        # briefly for the fill, so the position is recorded only once it exists
        # — entries used to be MARKET and filled wherever price happened to be
        # (up to 1% away, degrading the planned R:R and, when the market had
        # already passed the stop, producing an invalid SL that Binance
        # rejected and left the entry naked). The plan's SL/TP are only correct
        # for the plan's price, hence "the planned price or no trade".
        if paper_mode:
            position_id = await self._create_position(
                signal, entry, size, True, initial_risk_distance=risk_distance)
            await self.order_svc.place_order(
                symbol, side, "MARKET", size, price=Decimal(str(entry)),
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                simulate=True,
            )
        else:
            position_id = None
            try:
                fill = await self._enter_with_limit(symbol, side, float(entry), float(size))
                if fill is None:
                    # The order may have filled without us confirming it (status
                    # read failure) — never leave that unprotected.
                    await self._flatten_if_unprotected(symbol)
                    signal.status = "SKIPPED"
                    await self._mark_signal(
                        signal.db_id, "SKIPPED",
                        "Limit entry not filled at the plan price")
                    return TradeDecision(
                        "SKIP", skip_reason="Limit entry not filled at the plan price")

                fill_price = fill["avgPrice"]
                fill_qty = fill["executedQty"]
                # The Position row is created AFTER the fill so its entry/qty
                # are exchange truth from the start (the SL/TP records link to
                # it via orders.position_id).
                position_id = await self._create_position(
                    signal, fill_price, size, False,
                    initial_risk_distance=risk_distance)
                await self._update_position_fill(position_id, fill_price, fill_qty)

                await self.order_svc.place_order(
                    symbol, side, "LIMIT", size, price=Decimal(str(fill_price)),
                    simulate=False, binance_order_id=fill["orderId"],
                    position_id=position_id,
                )
                sl_res = await self.binance.modify_position_stop(
                    symbol, signal.direction, float(signal.stop_loss), fill_qty)
                await self.order_svc.place_order(
                    symbol, "SELL" if signal.direction == "LONG" else "BUY",
                    "STOP_MARKET", size, price=signal.stop_loss,
                    simulate=False, binance_order_id=sl_res.get("orderId"),
                    position_id=position_id,
                )
                tp_res = await self.binance.place_take_profit_order(
                    symbol, signal.direction, float(signal.take_profit), fill_qty)
                await self.order_svc.place_order(
                    symbol, "SELL" if signal.direction == "LONG" else "BUY",
                    "TAKE_PROFIT_MARKET", size, price=signal.take_profit,
                    simulate=False, binance_order_id=tp_res.get("orderId"),
                    position_id=position_id,
                )
            except Exception as exc:
                logger.error("[%s] Live order flow failed: %s", symbol, exc)
                # The entry may already have filled before the failure. Never
                # leave an UNPROTECTED position behind (live 2026-10-15: a
                # rejected SL left XRPUSDT and BTCUSDT open and naked for
                # hours). If the exchange holds a position for this symbol with
                # no resting stop, flatten it. If a stop IS resting, keep the
                # position — the reconcile adopts it with its SL/TP.
                flattened = await self._flatten_if_unprotected(symbol)
                if flattened:
                    # Nothing is open on the exchange for this symbol — undo the
                    # row we just created.
                    await self._close_position_row(position_id)
                else:
                    # The exchange still holds (or may hold) this position. The
                    # row MUST stay OPEN so the reconcile adopts a TRACKED
                    # position. Previously the row was closed unconditionally,
                    # contradicting the comment above and leaving an exchange
                    # position with an SL, no TP and no OPEN row — so nothing
                    # ever managed it again.
                    logger.error(
                        "[%s] Live flow failed (%s) but the exchange position was "
                        "NOT flattened — leaving the row OPEN for reconcile",
                        symbol, exc)
                # Mark the signal in BOTH cases: left PENDING, it would be
                # re-evaluated on the next scan and could place a SECOND entry
                # on a symbol the exchange may still hold.
                signal.status = "SKIPPED"
                await self._mark_signal(
                    signal.db_id, "SKIPPED", f"Order placement failed: {exc}")
                return TradeDecision("SKIP", skip_reason=f"Order placement failed: {exc}")

        # 7. Mark the signal TRIGGERED (position row already created above)
        signal.status = "TRIGGERED"
        signal.triggered_at = datetime.now(timezone.utc)
        await self._mark_signal(signal.db_id, "TRIGGERED")

        logger.info("[%s] %s executed (%s): %s", symbol, signal.strategy_type,
                    "paper" if paper_mode else "LIVE", plan)
        return TradeDecision("EXECUTE", plan=plan, position_id=position_id, paper=paper_mode)

    # ---- helpers ---------------------------------------------------------

    async def _has_open_db_position(self, symbol: str) -> bool:
        """Paper positions live only in the DB — guard against re-entry."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(PositionModel.id).where(
                    PositionModel.symbol == symbol, PositionModel.status == "OPEN"
                )
            )
            return result.first() is not None

    async def _count_open_positions(self) -> int:
        if self.session_factory is None:
            return 0
        async with self.session_factory() as session:
            result = await session.execute(
                select(PositionModel).where(PositionModel.status == "OPEN")
            )
            return len(result.scalars().all())

    async def _current_price(self, symbol: str) -> Optional[float]:
        if self.binance is None:
            return None
        try:
            ticker = await self.binance.get_ticker(symbol)
            return float(ticker.get("price", 0)) or None
        except Exception as exc:
            logger.debug("get_ticker(%s) failed: %s", symbol, exc)
            return None

    async def _account_balance(self, paper_mode: bool) -> Optional[Decimal]:
        if paper_mode:
            # paper_use_real_balance (default true): size paper trades against
            # the trader's REAL Binance USDT balance so simulated positions
            # reflect actual capital. Falls back to the fixed paper_balance_usdt
            # setting when disabled (or when the real balance can't be fetched).
            if self.settings.get("paper_use_real_balance", True) and self.binance is not None:
                try:
                    for asset in await self.binance.get_account_balance():
                        if asset.get("asset") == "USDT":
                            return Decimal(str(asset.get("availableBalance", 0)))
                except Exception as exc:
                    logger.debug("paper real-balance fetch failed, using paper_balance_usdt: %s", exc)
            return Decimal(str(self.settings.get("paper_balance_usdt", 10000)))
        if self.binance is None:
            return None
        try:
            for asset in await self.binance.get_account_balance():
                if asset.get("asset") == "USDT":
                    return Decimal(str(asset.get("availableBalance", 0)))
        except Exception as exc:
            logger.debug("get_account_balance failed: %s", exc)
        return None

    async def _create_position(self, signal, price: float, size: Decimal, paper_mode: bool,
                               initial_risk_distance: float | None = None) -> Optional[int]:
        """Create a Position row. For live positions, pass the ACTUAL fill price
        (from Binance avgPrice), not the signal's entry-zone midpoint — Binance
        fills at market and the midpoint can drift 0.1-0.5% from the real fill."""
        if self.session_factory is None:
            return None
        leverage_key = "leverage_btc" if signal.symbol.upper().startswith("BTC") else "leverage_alts"
        async with self.session_factory() as session:
            position = PositionModel(
                signal_id=signal.db_id,
                symbol=signal.symbol,
                side=signal.direction,
                entry_price=price,  # actual fill price for live, midpoint for paper
                quantity=float(size),
                leverage=int(self.settings.get(leverage_key, 10)),
                stop_loss=float(signal.stop_loss),
                take_profit=float(signal.take_profit),
                initial_risk_distance=initial_risk_distance,
                status="OPEN",
                mode="paper" if paper_mode else "live",
            )
            session.add(position)
            await session.flush()
            position_id = position.id
            await session.commit()
            return position_id

    async def _update_position_fill(self, position_id: Optional[int],
                                    actual_price: float, actual_qty: float | None = None) -> None:
        """Update a position row with the exchange's executed qty + fill price.

        The initial _create_position stores the planned size and the signal's
        entry-zone midpoint, but Binance rounds qty to LOT_SIZE and fills at
        market. Recording the exchange's numbers keeps dashboard PnL/ROI in
        sync with Binance, and the FROZEN risk distance is recomputed from the
        real entry — pnl_r is measured against it at close, so a midpoint-based
        distance misreports R whenever the fill drifts (XRP 2026-10-15: planned
        0.0138 vs real 0.02735, i.e. the same trade read +1.26R instead of
        +0.64R).
        """
        if self.session_factory is None or position_id is None:
            return
        if not ((actual_price and actual_price > 0)
                or (actual_qty is not None and actual_qty > 0)):
            return
        try:
            async with self.session_factory() as session:
                row = await session.get(PositionModel, position_id)
                if row is None:
                    return
                if actual_price and actual_price > 0:
                    row.entry_price = actual_price
                    if row.stop_loss is not None:
                        row.initial_risk_distance = abs(
                            float(actual_price) - float(row.stop_loss))
                if actual_qty is not None and actual_qty > 0:
                    row.quantity = actual_qty
                await session.commit()
        except Exception:
            logger.exception("Failed to update position %s fill", position_id)

    async def _mark_signal(self, db_id: Optional[int], status: str,
                           skip_reason: Optional[str] = None) -> None:
        if self.session_factory is None or db_id is None:
            return
        try:
            values = {"status": status}
            if status == "TRIGGERED":
                values["triggered_at"] = datetime.now(timezone.utc)
            if skip_reason is not None:
                values["skip_reason"] = skip_reason
            async with self.session_factory() as session:
                await session.execute(
                    update(SignalModel).where(SignalModel.id == db_id).values(**values)
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to mark signal %s as %s", db_id, status)

    async def _enter_with_limit(self, symbol: str, side: str, price: float,
                                size: float,
                                timeout_s: Optional[float] = None) -> Optional[dict]:
        """Place a LIMIT at the planned zone price and wait briefly for the fill.

        Returns {orderId, avgPrice, executedQty} when filled, else None after
        cancelling the resting order — "the planned price or no trade". A limit
        at the midpoint fills immediately when price is inside the zone (the
        normal case, since the zone gate requires it) and otherwise leaves the
        plan to be re-evaluated on a later scan instead of chasing the market.
        """
        res = await self.binance.place_limit_order(symbol, side, price, size)
        order_id = res.get("orderId")
        if not order_id:
            return None

        if timeout_s is None:
            timeout_s = getattr(self, "limit_fill_timeout_s", 12.0)
        poll_s = getattr(self, "limit_poll_interval_s", 2.0)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                det = await self.binance.get_order(symbol, int(order_id))
            except asyncio.CancelledError:
                # asyncio.CancelledError is a BaseException, so `except
                # Exception` does NOT catch it — pressing Stop, a deploy, or a
                # task cancel during this wait used to leave a GTC limit ARMED
                # on the exchange with no DB row. It could fill minutes later and
                # open a position nobody was managing.
                await self._cancel_orphan_limit(symbol, int(order_id))
                raise
            except Exception as exc:
                logger.debug("[%s] limit order status check failed: %s", symbol, exc)
                break
            status = str(det.get("status", "")).upper()
            if status == "FILLED":
                return {
                    "orderId": order_id,
                    "avgPrice": float(det.get("avgPrice", 0) or 0) or float(price),
                    "executedQty": float(det.get("executedQty", 0) or 0) or float(size),
                }
            if status == "PARTIALLY_FILLED":
                # A partial fill is a REAL position on the exchange. Treating it
                # as "not filled" cancelled the remainder and reported a clean
                # skip, never reading executedQty — so the partial size was
                # cleaned up only if _flatten_if_unprotected happened to act.
                logger.warning(
                    "[%s] limit entry PARTIALLY filled (%s/%s) — cancelling the "
                    "remainder and flattening the filled slice",
                    symbol, det.get("executedQty"), size)
                await self._cancel_orphan_limit(symbol, int(order_id))
                await self._flatten_if_unprotected(symbol)
                return None
            if status in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
                return None
            await asyncio.sleep(poll_s)

        await self._cancel_orphan_limit(symbol, int(order_id), reason=f"not filled in {timeout_s:.0f}s")
        return None

    async def _cancel_orphan_limit(self, symbol: str, order_id: int,
                                   reason: str = "cancelled") -> None:
        """Cancel a resting entry LIMIT so it cannot fill unmanaged.

        Every path out of _enter_with_limit must come through here. A GTC order
        that outlives the process has no DB row, so if it fills later it opens a
        position with no SL, no TP and nothing tracking it — the reconcile
        adopts it within 5 minutes, but as an UNPROTECTED position.
        """
        try:
            await self.binance.cancel_order(symbol, int(order_id))
            logger.info("[%s] limit entry %s — order %s cancelled",
                        symbol, reason, order_id)
        except Exception as exc:
            logger.error(
                "[%s] CRITICAL — could not cancel resting limit entry %s (%s). "
                "It may fill later and open an unmanaged position. Manual "
                "cancellation on the exchange may be required.", symbol, order_id, exc)

    async def _flatten_if_unprotected(self, symbol: str) -> bool:
        """Close an exchange position left naked by a failed live order flow.

        Called after any live-flow exception. The entry can fill before a later
        leg fails (SL/TP placement, logging), so without this the position sits
        on the exchange with no stop — and the reconcile can only adopt it
        UNPROTECTED (SL=None/TP=None).

        Returns True when the exchange holds nothing for this symbol (nothing to
        clean up, or it was successfully flattened) — only then may the caller
        close the DB row. Returns False when the position is deliberately being
        LEFT open (a protective stop rests) or when the exchange state could
        not be determined; in both cases the caller MUST keep the row OPEN so
        the reconcile adopts a tracked position.

        The previous version returned None unconditionally, so the caller
        closed the row even when this method had deliberately left an exchange
        position open — an open position with an SL, no TP, and no OPEN row, so
        nothing ever managed it again.
        """
        if self.binance is None:
            return True
        try:
            pos = await self.binance.get_position(symbol)
        except Exception as exc:
            # UNKNOWN, not "flat". Failing closed here means the caller keeps
            # the DB row OPEN so the reconcile adopts a TRACKED position; the
            # previous bare `return` let the caller close the row, dropping an
            # exchange position that we simply couldn't check (a -1003 blip
            # reproduced the original naked-position incident).
            logger.error(
                "[%s] Could not verify position after failed flow (%s) — "
                "state UNKNOWN, not flat. Keeping the row open for reconcile.",
                symbol, exc)
            return False

        qty = float((pos or {}).get("quantity", 0) or 0)
        if qty <= 0:
            return True  # entry never filled — nothing to clean up

        try:
            algos = await self.binance.get_open_algo_orders(symbol)
        except Exception as exc:
            # UNKNOWN whether a protective stop rests. An exception here must
            # NOT be read as "no stop" — that is the exact condition which
            # authorises a market close, so a transient blip would flatten a
            # correctly-protected position. Fail closed: trust the exchange's
            # stop and let the reconcile resolve it.
            logger.error(
                "[%s] Could not read resting algo orders after flow failure "
                "(%s) — refusing to act. A stop may exist; the reconcile will "
                "adopt it.", symbol, exc)
            return False

        if any(a.get("orderType") == "STOP_MARKET" for a in algos):
            logger.warning(
                "[%s] Live flow failed but a resting SL exists — leaving the "
                "position OPEN for reconcile to adopt (qty %s)", symbol, qty)
            return False

        close_side = "SELL" if (pos.get("side") or "LONG") == "LONG" else "BUY"
        try:
            await self.binance.place_market_order(symbol, close_side, qty)
            logger.error(
                "[%s] Live flow failed with an UNPROTECTED %s %s on the exchange "
                "— flattened at market", symbol, pos.get("side"), qty)
            return True
        except Exception as exc:
            logger.critical(
                "[%s] FAILED to flatten naked %s %s after flow failure: %s — "
                "manual intervention required", symbol, pos.get("side"), qty, exc)
            return False

    async def _close_position_row(self, position_id: Optional[int]) -> None:
        """Close a just-created position row AND record the realized Trade after
        a failed live entry that was flattened.

        A live entry that fails after the position row is created would leave an
        OPEN position with no exchange order behind it. When the position was
        flattened at market, the realized PnL must be recorded as a Trade row —
        otherwise the loss/profit vanishes from trade history (observed live:
        APEUSDT flattened at -$0.32 with no Trade row created).
        """
        if self.session_factory is None or position_id is None:
            return
        try:
            async with self.session_factory() as session:
                row = await session.get(PositionModel, position_id)
                if not row or row.status != "OPEN":
                    return

                # Fetch realized PnL from the exchange before closing
                pnl_usdt = 0.0
                pnl_r = 0.0
                if self._mode == "live" and self.binance is not None:
                    try:
                        income = await self.binance.get_position_income(
                            row.symbol, "REALIZED_PNL",
                            int(row.opened_at.timestamp() * 1000) if row.opened_at else None,
                        )
                        pnl_usdt = float(income.get("totalPnl", 0) or 0)
                        if row.initial_risk_distance and float(row.initial_risk_distance) > 0:
                            pnl_r = pnl_usdt / (float(row.quantity) * float(row.initial_risk_distance))
                    except Exception:
                        logger.debug("Could not fetch income for flatten close of %s", row.symbol)

                row.status = "CLOSED"
                row.closed_at = datetime.now(timezone.utc)

                # Record the Trade row so the loss appears in trade history
                from backend.models.trade import Trade as TradeModel
                trade = TradeModel(
                    position_id=row.id,
                    symbol=row.symbol,
                    side=row.side,
                    mode=self._mode,
                    entry_price=row.entry_price,
                    exit_price=row.entry_price,  # approximate — market close, no exact price captured
                    quantity=row.quantity,
                    pnl_usdt=pnl_usdt,
                    pnl_r=pnl_r,
                    exit_reason="FLATTENED",
                    opened_at=row.opened_at,
                    closed_at=row.closed_at,
                )
                session.add(trade)
                await session.commit()
                logger.warning(
                    "[%s] Recorded FLATTENED trade: %.4f USDT / %.2fR (failed entry, market-closed)",
                    row.symbol, pnl_usdt, pnl_r)
        except Exception:
            logger.exception("Failed to close position row %s after failed entry", position_id)


_strategy_service: Optional[StrategyService] = None


def get_strategy_service() -> Optional[StrategyService]:
    global _strategy_service
    return _strategy_service


def set_strategy_service(svc: StrategyService) -> None:
    global _strategy_service
    _strategy_service = svc
