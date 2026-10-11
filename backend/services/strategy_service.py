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
import logging
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

        # 1. Open position on this symbol?
        existing = await self.order_svc.get_current_position(symbol)
        if existing and existing.get("quantity", 0) > 0:
            return TradeDecision("SKIP", skip_reason=f"Position already open on {symbol}")

        # 2. Max simultaneous positions
        max_positions = int(self.settings.get("max_positions", 3))
        if self.session_factory is not None:
            open_count = await self._count_open_positions()
            if open_count >= max_positions:
                return TradeDecision(
                    "SKIP", skip_reason=f"Max positions ({max_positions}) already open")

        # 3. Signal age
        max_age_min = float(self.settings.get("signal_max_age_minutes", 60))
        age_min = (datetime.now(timezone.utc) - signal.created_at).total_seconds() / 60
        if age_min > max_age_min:
            signal.status = "EXPIRED"
            await self._mark_signal(signal.db_id, "EXPIRED")
            return TradeDecision("SKIP", skip_reason=f"Signal older than {max_age_min:.0f} min")

        # 4. Price deviation from the entry zone
        entry = (signal.entry_zone_min + signal.entry_zone_max) / 2
        price = await self._current_price(symbol)
        if price is None:
            return TradeDecision("SKIP", skip_reason="Could not fetch current price")
        if signal.direction == "LONG":
            if price > float(signal.entry_zone_max) * 1.01:
                return TradeDecision("SKIP", skip_reason="Price moved too far above zone")
            if price < float(signal.entry_zone_min) * 0.99:
                return TradeDecision("SKIP", skip_reason="Price already below zone")
        else:
            if price < float(signal.entry_zone_min) * 0.99:
                return TradeDecision("SKIP", skip_reason="Price moved too far below zone")
            if price > float(signal.entry_zone_max) * 1.01:
                return TradeDecision("SKIP", skip_reason="Price already above zone")

        # 5. Risk validation + sizing
        min_rr = Decimal(str(self.settings.get("rr_min", 3.0)))
        if not self.risk_svc.validate_rr_ratio(
            entry, signal.stop_loss, signal.take_profit, min_rr
        ):
            signal.status = "SKIPPED"
            await self._mark_signal(signal.db_id, "SKIPPED", "R:R below minimum")
            return TradeDecision("SKIP", skip_reason="R:R below minimum")

        balance = await self._account_balance(paper_mode)
        if balance is None:
            return TradeDecision("SKIP", skip_reason="Could not fetch account balance")
        risk_percent = Decimal(str(self.settings.get("risk_percent", 1.0)))
        size = self.risk_svc.calc_position_size(
            balance, risk_percent, Decimal(str(price)), signal.stop_loss
        )
        if size == Decimal("0"):
            return TradeDecision("SKIP", skip_reason="Position size too small")

        plan = {
            "symbol": symbol,
            "direction": signal.direction,
            "entry": price,
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "size": float(size),
            "rr_ratio": float(signal.rr_ratio),
        }

        # 6. Execute — simulated fills in paper, real Binance orders in live
        if paper_mode:
            await self.order_svc.place_order(
                symbol, side, "MARKET", size, price=Decimal(str(price)),
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                simulate=True,
            )
        else:
            try:
                entry_res = await self.binance.place_market_order(symbol, side, float(size))
                await self.order_svc.place_order(
                    symbol, side, "MARKET", size, price=Decimal(str(price)),
                    simulate=False, binance_order_id=entry_res.get("orderId"),
                )
                sl_res = await self.binance.modify_position_stop(
                    symbol, signal.direction, float(signal.stop_loss), float(size))
                await self.order_svc.place_order(
                    symbol, "SELL" if signal.direction == "LONG" else "BUY",
                    "STOP_MARKET", size, price=signal.stop_loss,
                    simulate=False, binance_order_id=sl_res.get("orderId"),
                )
                tp_res = await self.binance.place_take_profit_order(
                    symbol, signal.direction, float(signal.take_profit), float(size))
                await self.order_svc.place_order(
                    symbol, "SELL" if signal.direction == "LONG" else "BUY",
                    "TAKE_PROFIT_MARKET", size, price=signal.take_profit,
                    simulate=False, binance_order_id=tp_res.get("orderId"),
                )
            except Exception as exc:
                logger.error("[%s] Live order flow failed: %s", symbol, exc)
                return TradeDecision("SKIP", skip_reason=f"Order placement failed: {exc}")

        # 7. Record the position and mark the signal TRIGGERED
        position_id = await self._create_position(signal, price, size, paper_mode)
        signal.status = "TRIGGERED"
        signal.triggered_at = datetime.now(timezone.utc)
        await self._mark_signal(signal.db_id, "TRIGGERED")

        logger.info("[%s] %s executed (%s): %s", symbol, signal.strategy_type,
                    "paper" if paper_mode else "LIVE", plan)
        return TradeDecision("EXECUTE", plan=plan, position_id=position_id, paper=paper_mode)

    # ---- helpers ---------------------------------------------------------

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

    async def _create_position(self, signal, price: float, size: Decimal, paper_mode: bool) -> Optional[int]:
        if self.session_factory is None:
            return None
        leverage_key = "leverage_btc" if signal.symbol.upper().startswith("BTC") else "leverage_alts"
        async with self.session_factory() as session:
            position = PositionModel(
                signal_id=signal.db_id,
                symbol=signal.symbol,
                side=signal.direction,
                entry_price=price,
                quantity=float(size),
                leverage=int(self.settings.get(leverage_key, 10)),
                stop_loss=float(signal.stop_loss),
                take_profit=float(signal.take_profit),
                status="OPEN",
            )
            session.add(position)
            await session.flush()
            position_id = position.id
            await session.commit()
            return position_id

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


_strategy_service: Optional[StrategyService] = None


def get_strategy_service() -> Optional[StrategyService]:
    global _strategy_service
    return _strategy_service


def set_strategy_service(svc: StrategyService) -> None:
    global _strategy_service
    _strategy_service = svc
