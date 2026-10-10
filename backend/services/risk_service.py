"""Risk service — position sizing, R:R validation, daily loss limit enforcement."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.daily_summary import DailySummary
from backend.models.setting import Setting


@dataclass
class PositionSize:
    quantity: float
    notional_value: float
    margin_required: float
    risk_usdt: float
    leverage_used: int


@dataclass
class RiskCheck:
    is_valid: bool
    reason: str
    rr_ratio: Optional[float] = None
    position_size: Optional[PositionSize] = None


class RiskService:
    """Risk management: position sizing, R:R validation, daily loss limit."""

    STEP_SIZES = {
        "BTCUSDT": 0.001,
        "ETHUSDT": 0.01,
        "BNBUSDT": 0.01,
        "SOLUSDT": 0.01,
        "XRPUSDT": 0.01,
        "ADAUSDT": 0.01,
        "DOGEUSDT": 0.1,
    }

    def __init__(self, db: AsyncSession, settings: dict):
        self.db = db
        self.settings = settings

    def _get_setting(self, key: str, default=None):
        s = self.settings.get(key)
        if s is not None:
            return s
        return default

    async def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        leverage: Optional[int] = None,
        balance: float = 180.0,
    ) -> PositionSize:
        """
        Calculate position size from account balance and risk %.

        Risk $ = balance × risk_percent
        Stop distance % = |entry - stop| / entry
        Notional = risk $ / stop_distance %
        Margin = notional / leverage
        """
        risk_percent = self._get_setting("risk_percent", 1.0) / 100.0
        risk_amount = balance * risk_percent

        stop_distance_pct = abs(entry_price - stop_loss) / entry_price
        stop_distance_pct = max(stop_distance_pct, 0.0001)

        notional = risk_amount / stop_distance_pct

        lev = leverage or self._get_default_leverage(symbol)
        margin = notional / lev
        risk_usdt = notional * stop_distance_pct

        quantity = self._round_to_step(notional / entry_price, symbol)

        return PositionSize(
            quantity=quantity,
            notional_value=notional,
            margin_required=margin,
            risk_usdt=risk_usdt,
            leverage_used=lev,
        )

    async def validate_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        direction: str,
        balance: float = 180.0,
    ) -> RiskCheck:
        """Full risk validation before any order is placed."""
        # 1. R:R check
        rr_ratio = self._calculate_rr(entry_price, stop_loss, take_profit, direction)
        rr_min = self._get_setting("rr_min", 3.0)
        if rr_ratio < rr_min:
            return RiskCheck(
                is_valid=False,
                reason=f"R:R {rr_ratio:.2f} < minimum {rr_min}",
                rr_ratio=rr_ratio,
            )

        # 2. Position size check
        try:
            size = await self.calculate_position_size(symbol, entry_price, stop_loss, balance=balance)
        except Exception as e:
            return RiskCheck(is_valid=False, reason=f"Position sizing error: {e}", rr_ratio=rr_ratio)

        if size.margin_required > balance * 0.95:
            return RiskCheck(
                is_valid=False,
                reason=f"Insufficient margin: need ${size.margin_required:.2f}, have ${balance:.2f}",
                rr_ratio=rr_ratio,
                position_size=size,
            )

        # 3. Daily loss limit check
        daily_check = await self._check_daily_loss_limit()
        if not daily_check["allowed"]:
            return RiskCheck(
                is_valid=False,
                reason=f"Daily loss limit reached: {daily_check['used_r']:.1f}R used",
                rr_ratio=rr_ratio,
                position_size=size,
            )

        # 4. Max positions check
        open_count = await self._get_open_position_count()
        max_pos = self._get_setting("max_positions", 3)
        if open_count >= max_pos:
            return RiskCheck(
                is_valid=False,
                reason=f"Max positions ({max_pos}) already open",
                rr_ratio=rr_ratio,
                position_size=size,
            )

        return RiskCheck(is_valid=True, reason="OK", rr_ratio=rr_ratio, position_size=size)

    def _calculate_rr(self, entry: float, stop: float, tp: float, direction: str) -> float:
        """Calculate R:R ratio."""
        if direction == "LONG":
            risk = entry - stop
            reward = tp - entry
        else:
            risk = stop - entry
            reward = entry - tp
        if risk <= 0:
            return 0.0
        return reward / risk

    def _round_to_step(self, quantity: float, symbol: str) -> float:
        """Round quantity to Binance step size."""
        step = self.STEP_SIZES.get(symbol, 0.01)
        return float(int(quantity / step) * step)

    def _get_default_leverage(self, symbol: str) -> int:
        """Default leverage per symbol."""
        if "BTC" in symbol:
            return self._get_setting("leverage_btc", 10)
        return self._get_setting("leverage_alts", 5)

    async def _check_daily_loss_limit(self) -> dict:
        """Check cumulative daily P&L in R units."""
        today = datetime.now(timezone.utc).date()

        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        limit_r = abs(self._get_setting("daily_loss_limit_r", -3.0))
        used_r = abs(float(summary.total_pnl_r)) if summary else 0.0

        return {
            "allowed": used_r < limit_r,
            "used_r": used_r,
            "limit_r": limit_r,
            "remaining_r": limit_r - used_r,
        }

    async def _get_open_position_count(self) -> int:
        """Count open positions."""
        from backend.models.position import Position
        result = await self.db.execute(
            select(Position).where(Position.status == "OPEN")
        )
        return len(result.scalars().all())

    async def update_daily_summary(self, pnl_r: float, pnl_usdt: float) -> None:
        """Update daily summary after a trade closes."""
        today = datetime.now(timezone.utc).date()

        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        if not summary:
            from backend.models.daily_summary import DailySummary as DS
            summary = DS(date=today)
            self.db.add(summary)

        summary.total_trades += 1
        summary.total_pnl_r += pnl_r
        summary.total_pnl_usdt += pnl_usdt

        if pnl_r > 0:
            summary.wins += 1
            if pnl_r > summary.best_trade_r:
                summary.best_trade_r = pnl_r
        elif pnl_r < 0:
            summary.losses += 1
            if pnl_r < summary.worst_trade_r:
                summary.worst_trade_r = pnl_r
        else:
            summary.breakeven += 1

        await self.db.commit()
