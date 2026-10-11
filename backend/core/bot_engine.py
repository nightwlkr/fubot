"""Bot engine — main async trading loop orchestrator.

DB access: the engine shares a session FACTORY (AsyncSessionLocal), never a
long-lived session — AsyncSession is not task-safe, and the scan loop runs
concurrently with request handlers on the same event loop. Every DB operation
opens a short-lived session.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select

from backend.models.symbol import Symbol
from backend.models.equity_log import EquityLog
from backend.services.signal_service import SignalService
from backend.services.strategy_service import StrategyService
from backend.services.risk_service import RiskService
from backend.services.order_service import OrderService
from backend.services.alert_service import AlertService
from backend.services.binance_service import BinanceService
from backend.core.daily_reset import DailyResetService

logger = logging.getLogger(__name__)


class BotEngine:
    """Main trading bot orchestrator — runs continuous scan/evaluate/execute loop."""

    def __init__(
        self,
        settings: dict,
        binance: BinanceService,
        signal_service: SignalService,
        strategy_service: StrategyService,
        risk_service: RiskService,
        order_service: OrderService,
        alert_service: AlertService,
        daily_reset: DailyResetService,
        session_factory: Optional[Callable] = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.binance = binance
        self.signals = signal_service
        self.strategy = strategy_service
        self.risk = risk_service
        self.orders = order_service
        self.alerts = alert_service
        self.daily_reset = daily_reset

        self._running = False
        self._mode: str = "paper"
        self._task: Optional[asyncio.Task] = None
        self._last_scan: Optional[datetime] = None
        self._scan_count = 0
        self._started_at: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def mode(self) -> str:
        return self._mode

    async def start(self, mode: str = "paper"):
        """Start the trading bot in paper or live mode."""
        mode = mode.lower()
        if mode not in ("paper", "live"):
            raise ValueError("Mode must be 'paper' or 'live'")

        self._running = True
        self._mode = mode
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._main_loop())
        await self.alerts.bot_started(mode)
        logger.info(f"BotEngine: started in {mode} mode")

    async def stop(self):
        """Gracefully stop the bot."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.alerts.bot_stopped()
        logger.info("BotEngine: stopped")

    async def _main_loop(self):
        """Main trading loop — runs every check_interval_seconds."""
        interval = self.settings.get("check_interval_seconds", 60)

        while self._running:
            try:
                self._scan_count += 1
                start = datetime.now(timezone.utc)

                await self._scan_and_evaluate()

                # Equity snapshot every 5 scans
                if self._scan_count % 5 == 0:
                    await self._maybe_snapshot_equity()

                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                self._last_scan = datetime.now(timezone.utc)

                logger.debug(f"Scan #{self._scan_count} completed in {elapsed:.1f}s")

                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"BotEngine main loop error: {e}", exc_info=True)
                await self.alerts.error(f"Bot loop error: {e}")
                await asyncio.sleep(interval)

    async def _scan_and_evaluate(self):
        """For each enabled symbol: scan, evaluate, execute."""
        symbols = await self._get_enabled_symbols()

        for symbol in symbols:
            if not self._running:
                break

            try:
                # Skip if already have open position
                existing = await self.orders.get_current_position(symbol)
                if existing and existing.get("quantity", 0) > 0:
                    continue

                # Check daily loss limit
                if not await self.daily_reset.check_and_enforce_limit():
                    logger.info(f"Daily limit reached, skipping {symbol}")
                    stats = await self.daily_reset.get_daily_stats()
                    await self.alerts.daily_limit_hit(stats["used_r"], stats["limit_r"])
                    break

                # Scan for signals
                signals = await self.signals.scan_symbol(symbol)

                for signal in signals:
                    decision = await self.strategy.evaluate_signal(signal, paper_mode=(self._mode == "paper"))

                    if decision.decision == "EXECUTE":
                        await self.alerts.trade_executed(
                            symbol=signal.symbol,
                            direction=signal.direction,
                            entry=(signal.entry_zone_min + signal.entry_zone_max) / 2,
                            sl=signal.stop_loss,
                            tp=signal.take_profit,
                            rr=signal.rr_ratio,
                        )
                    elif decision.decision == "SKIP":
                        logger.debug(f"[{signal.symbol}] Skipped: {decision.skip_reason}")

            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}", exc_info=True)
                continue

    async def _get_enabled_symbols(self) -> list[str]:
        """Fetch list of enabled symbols from DB."""
        if self.session_factory is None:
            return []
        async with self.session_factory() as session:
            result = await session.execute(
                select(Symbol.symbol).where(Symbol.enabled == True)
            )
            return [row[0] for row in result.fetchall()]

    async def _maybe_snapshot_equity(self):
        """Log equity snapshot."""
        try:
            balance_data = await self.binance.get_account_balance()
            usdt_balance = 0.0
            for asset in balance_data:
                if asset.get("asset") == "USDT":
                    usdt_balance = float(asset.get("availableBalance", 0))
                    break

            if self.session_factory is None:
                return
            async with self.session_factory() as session:
                session.add(EquityLog(
                    balance_usdt=usdt_balance,
                    recorded_at=datetime.now(timezone.utc),
                ))
                await session.commit()
        except Exception as e:
            logger.debug(f"Equity snapshot failed: {e}")

    async def get_status(self) -> dict:
        """Current bot status for dashboard."""
        daily_stats = await self.daily_reset.get_daily_stats()

        return {
            "status": "running" if self._running else "stopped",
            "mode": self._mode,
            "uptime_seconds": (datetime.now(timezone.utc) - self._started_at).total_seconds() if self._running and self._started_at else 0,
            "scan_count": self._scan_count,
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
            "daily_stats": daily_stats,
            "open_positions": [],
            "enabled_symbols": await self._get_enabled_symbols(),
        }
