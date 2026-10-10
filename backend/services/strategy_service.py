"""Strategy service — trade decision logic, signal evaluation."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.signal import Signal
from backend.models.position import Position
from backend.services.risk_service import RiskService, PositionSize
from backend.services.order_service import OrderService, OrderResult


@dataclass
class TradeDecision:
    signal_id: int
    decision: str            # 'EXECUTE', 'SKIP', 'PAPER'
    position_size: Optional[PositionSize] = None
    skip_reason: Optional[str] = None
    order_ids: list[int] = None


class StrategyService:
    """Strategy decision layer — evaluates signals and decides to trade or skip."""

    def __init__(
        self,
        db: AsyncSession,
        risk_service: RiskService,
        order_service: OrderService,
        settings: dict,
    ):
        self.db = db
        self.risk = risk_service
        self.orders = order_service
        self.settings = settings

    async def evaluate_signal(self, signal: Signal, paper_mode: bool = False) -> TradeDecision:
        """
        Full evaluation of one signal:
        1. Check if symbol already has open position
        2. Check signal freshness
        3. Run risk validation
        4. If valid: execute (or simulate in paper mode)
        5. If invalid: log skip reason
        """
        # 1. Already have open position?
        existing = await self.orders.get_current_position(signal.symbol)
        if existing and existing.get("quantity", 0) > 0:
            return TradeDecision(
                signal_id=signal.id,
                decision="SKIP",
                skip_reason=f"Position already open on {signal.symbol}",
            )

        # 2. Signal freshness
        signal_age = datetime.now(timezone.utc) - signal.created_at
        max_age = timedelta(minutes=self.settings.get("signal_max_age_minutes", 60))
        if signal_age > max_age:
            signal.status = "EXPIRED"
            await self.db.commit()
            return TradeDecision(
                signal_id=signal.id,
                decision="SKIP",
                skip_reason=f"Signal expired ({signal_age.total_seconds() / 60:.0f}min old)",
            )

        # 3. Get entry price (midpoint of zone)
        entry_price = (signal.entry_zone_min + signal.entry_zone_max) / 2

        # 4. Verify price is still near entry zone
        max_deviation = self.settings.get("price_deviation_max", 0.01)
        if signal.direction == "LONG":
            if entry_price > signal.entry_zone_max * (1 + max_deviation):
                return TradeDecision(
                    signal_id=signal.id,
                    decision="SKIP",
                    skip_reason="Price moved too far above zone",
                )
            if entry_price < signal.entry_zone_min * (1 - max_deviation):
                return TradeDecision(
                    signal_id=signal.id,
                    decision="SKIP",
                    skip_reason="Price already below zone",
                )
        else:
            if entry_price < signal.entry_zone_min * (1 - max_deviation):
                return TradeDecision(
                    signal_id=signal.id,
                    decision="SKIP",
                    skip_reason="Price moved too far below zone",
                )
            if entry_price > signal.entry_zone_max * (1 + max_deviation):
                return TradeDecision(
                    signal_id=signal.id,
                    decision="SKIP",
                    skip_reason="Price already above zone",
                )

        # 5. Full risk validation
        risk_check = await self.risk.validate_trade(
            symbol=signal.symbol,
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            direction=signal.direction,
        )

        if not risk_check.is_valid:
            signal.status = "SKIPPED"
            signal.skip_reason = risk_check.reason
            await self.db.commit()
            return TradeDecision(
                signal_id=signal.id,
                decision="SKIP",
                skip_reason=risk_check.reason,
            )

        # 6. Execute (or paper)
        if paper_mode:
            return TradeDecision(
                signal_id=signal.id,
                decision="PAPER",
                position_size=risk_check.position_size,
            )

        # Place entry order
        entry_result = await self.orders.place_entry_order(
            symbol=signal.symbol,
            side=signal.direction,
            entry_price=entry_price,
            quantity=risk_check.position_size.quantity,
            signal_id=signal.id,
        )

        if not entry_result.success:
            return TradeDecision(
                signal_id=signal.id,
                decision="SKIP",
                skip_reason=f"Order placement failed: {entry_result.error}",
            )

        # Place SL and TP
        sl_result = await self.orders.place_stop_loss(
            symbol=signal.symbol,
            side=signal.direction,
            stop_price=signal.stop_loss,
            quantity=risk_check.position_size.quantity,
        )

        tp_result = await self.orders.place_take_profit(
            symbol=signal.symbol,
            side=signal.direction,
            stop_price=signal.take_profit,
            quantity=risk_check.position_size.quantity,
        )

        order_ids = []
        if entry_result.binance_order_id:
            order_ids.append(entry_result.binance_order_id)
        if sl_result.binance_order_id:
            order_ids.append(sl_result.binance_order_id)
        if tp_result.binance_order_id:
            order_ids.append(tp_result.binance_order_id)

        # Create Position record
        position = Position(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=signal.direction,
            entry_price=entry_price,
            quantity=risk_check.position_size.quantity,
            leverage=risk_check.position_size.leverage_used,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status="OPEN",
        )
        self.db.add(position)
        await self.db.commit()
        await self.db.refresh(position)

        # Update signal status
        signal.status = "TRIGGERED"
        signal.triggered_at = datetime.now(timezone.utc)
        await self.db.commit()

        return TradeDecision(
            signal_id=signal.id,
            decision="EXECUTE",
            position_size=risk_check.position_size,
            order_ids=order_ids,
        )

    async def evaluate_all_signals(self, signals: list[Signal], paper_mode: bool = False) -> list[TradeDecision]:
        """Evaluate a batch of signals, returning decisions for each."""
        decisions = []
        for signal in signals:
            try:
                decision = await self.evaluate_signal(signal, paper_mode)
                decisions.append(decision)
            except Exception:
                decisions.append(TradeDecision(
                    signal_id=signal.id,
                    decision="SKIP",
                    skip_reason="Evaluation error",
                ))
        return decisions
