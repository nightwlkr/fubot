"""Bot engine — main async trading loop orchestrator.

DB access: the engine shares a session FACTORY (AsyncSessionLocal), never a
long-lived session — AsyncSession is not task-safe, and the scan loop runs
concurrently with request handlers on the same event loop. Every DB operation
opens a short-lived session.
"""
import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select

from backend.models.symbol import Symbol
from backend.models.equity_log import EquityLog
from backend.models.trade import Trade
from backend.models.position import Position
from backend.services.signal_service import SignalService
from backend.services.strategy_service import StrategyService
from backend.services.risk_service import RiskService
from backend.services.order_service import OrderService
from backend.services.alert_service import AlertService
from backend.services.binance_service import BinanceService
from backend.core.daily_reset import DailyResetService
from backend.core.weekly_drawdown import WeeklyDrawdownGuard
from backend.core.correlation_guard import CorrelationGuard
from backend.core.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


class BotModeConflictError(RuntimeError):
    """Raised when a running engine is asked to start in another mode."""


# Bounds on the scan cadence. Mirrors config.py (ge=10, le=300) but enforced
# at the point of use as well, so a bad settings row cannot spin the loop.
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300
DEFAULT_SCAN_INTERVAL = 60


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
        # Weekly drawdown guard — halts bot if weekly losses exceed threshold
        self.weekly_guard = WeeklyDrawdownGuard(
            session_factory=session_factory,
            settings=settings,
            bot_engine=self,
            alert_service=alert_service,
        )
        # Correlation guard — prevents overexposure to correlated assets
        self.correlation_guard = CorrelationGuard(settings=settings)
        self.position_manager = None  # set by main.py after construction

        self._running = False
        self._stopping = False
        self._lifecycle_generation = 0
        self._mode: str = settings.get("bot_mode", "paper")
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
        if self._stopping:
            raise BotModeConflictError("Bot is stopping; retry after it finishes")

        if self._running:
            # `_running` is set before the first await below, so concurrent
            # requests on this event loop see the in-progress start too.
            if self._task is None or not self._task.done():
                if mode != self._mode:
                    raise BotModeConflictError(
                        f"Bot is already running in {self._mode} mode; stop it before switching modes"
                    )
                logger.info("BotEngine: duplicate start ignored in %s mode", mode)
                return
            # Recover if a loop exited unexpectedly while leaving the flag set.
            self._running = False
        elif self._task is not None and not self._task.done():
            raise BotModeConflictError("Bot is still shutting down; retry after it finishes")

        self._lifecycle_generation += 1
        generation = self._lifecycle_generation
        self._running = True
        self._mode = mode
        # Tell the position manager the mode — in live mode it must update
        # exchange stops on BE-trailing and cancel/place orders on close.
        if self.position_manager is not None:
            self.position_manager._mode = mode
            try:
                await self.position_manager.set_mode(mode)
            except BaseException:
                if generation == self._lifecycle_generation:
                    self._running = False
                raise
        if generation != self._lifecycle_generation or not self._running:
            raise BotModeConflictError("Bot start was interrupted by a stop request")
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._main_loop())
        await self.alerts.bot_started(mode)
        if generation != self._lifecycle_generation or not self._running:
            raise BotModeConflictError("Bot start was interrupted by a stop request")
        await ws_manager.broadcast({"type": "bot_status",
                                    "data": {"status": "running", "mode": mode}})
        logger.info(f"BotEngine: started in {mode} mode")

    async def stop(self):
        """Gracefully stop the bot."""
        if self._stopping:
            return
        self._stopping = True
        self._lifecycle_generation += 1
        self._running = False
        task = self._task
        try:
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await self.alerts.bot_stopped()
            await ws_manager.broadcast({"type": "bot_status",
                                        "data": {"status": "stopped", "mode": self._mode}})
            logger.info("BotEngine: stopped")
        finally:
            self._stopping = False

    async def _main_loop(self):
        """Main trading loop — runs every check_interval_seconds."""
        interval = self._safe_interval()

        while self._running:
            try:
                self._scan_count += 1
                start = datetime.now(timezone.utc)

                await self._scan_and_evaluate()
                if not self._running:
                    break

                # Equity snapshot every 5 scans
                if self._scan_count % 5 == 0:
                    await self._maybe_snapshot_equity()
                    await ws_manager.broadcast({"type": "equity_snapshot", "data": {
                        "scan_count": self._scan_count}})

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
                await asyncio.sleep(self._safe_interval())

    def _safe_interval(self) -> int:
        """Scan interval, clamped to a sane range.

        `check_interval_seconds` reaching 0 (or negative) made
        `sleep_time = max(0, interval - elapsed)` equal 0 on every pass, so the
        scan loop spun continuously and hammered the Binance REST API — a
        self-inflicted rate-limit ban. The settings schema now rejects it on
        write, but a value already in the DB (or hand-edited) would still spin.
        """
        try:
            interval = int(self.settings.get("check_interval_seconds", 60))
        except (TypeError, ValueError):
            interval = 60
        if not (MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL):
            logger.error(
                "check_interval_seconds=%r out of range (%s-%s) — using %ss",
                self.settings.get("check_interval_seconds"),
                MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            return DEFAULT_SCAN_INTERVAL
        return interval

    async def _scan_and_evaluate(self):
        """For each enabled symbol: scan, evaluate, execute."""
        symbols = await self._get_enabled_symbols()

        # Overtrading guard — daily trade cap (checked once per pass)
        max_daily = int(self.settings.get("max_daily_trades", 12))
        daily_stats = await self.daily_reset.get_daily_stats()
        if max_daily > 0 and daily_stats["total_trades"] >= max_daily:
            logger.info(f"Daily trade cap reached ({daily_stats['total_trades']}/{max_daily})")
            return

        # Fetch ALL open positions ONCE per pass (single positionRisk call that
        # returns every position) instead of one call per symbol — the old loop
        # made ~24 signed requests/scan and tripped Binance's IP rate limit
        # (-1003 → HTTP 400/429), which blanked every signed call for ~60s and
        # made the bot look dead.
        open_positions: dict[str, dict] = {}
        try:
            open_positions = await self.orders.get_all_open_positions()
        except Exception as exc:
            if self._mode == "live":
                logger.error(
                    "Could not fetch open positions for the scan; skipping this pass because "
                    "account exposure is unknown (%s)", type(exc).__name__)
                return
            # Paper positions are tracked in the local DB; a missing exchange
            # snapshot must not disable offline/paper evaluation.
            logger.warning(
                "Could not fetch exchange positions in paper mode; continuing with "
                "local position checks only (%s)", type(exc).__name__)
            open_positions = {}

        if self._mode == "live" and open_positions:
            # Unrealized loss must use the position's frozen entry-to-stop risk,
            # not a generic percent-of-entry proxy. Pull the bases in one DB
            # read only when there is live exposure to account for.
            try:
                if self.session_factory is None:
                    raise RuntimeError("DB session factory unavailable for open-position risk bases")
                async with self.session_factory() as session:
                    result = await session.execute(
                        select(Position.symbol, Position.initial_risk_distance).where(
                            Position.status == "OPEN",
                            Position.mode == "live",
                        )
                    )
                    rows = result.fetchall()
                risk_by_symbol = {}
                for row in rows:
                    if row[0] in risk_by_symbol:
                        raise RuntimeError(f"Multiple open DB positions for {row[0]}")
                    risk_by_symbol[row[0]] = (
                        float(row[1]) if row[1] is not None else None)
                for symbol, position in open_positions.items():
                    position["initialRiskDistance"] = risk_by_symbol.get(symbol)
            except Exception as exc:
                logger.error(
                    "Could not load frozen risk bases for open live positions; "
                    "skipping this pass (%s)", type(exc).__name__)
                return
        # The breaker needs exchange PnL and the frozen R basis from this
        # snapshot. Keep it separate from the scan occupancy map, which is
        # updated as new positions execute below.
        daily_guard_positions = dict(open_positions) if self._mode == "live" else {}

        for symbol in symbols:
            if not self._running:
                break

            try:
                # Skip if already have open position (from the once-per-pass fetch)
                existing = open_positions.get(symbol)
                if existing and existing.get("quantity", 0) > 0:
                    continue

                # Check daily loss limit
                if not await self.daily_reset.check_and_enforce_limit(daily_guard_positions):
                    logger.info(f"Daily limit reached, skipping {symbol}")
                    break

                # Per-symbol cooldown: don't re-enter a symbol traded recently
                cooldown_min = int(self.settings.get("symbol_cooldown_minutes", 240))
                if cooldown_min > 0:
                    last_trade_age_min = await self._minutes_since_last_trade(symbol)
                    if last_trade_age_min is not None and last_trade_age_min < cooldown_min:
                        logger.debug(
                            f"[{symbol}] cooldown: last trade {last_trade_age_min:.0f}m ago "
                            f"< {cooldown_min}m")
                        continue

                # Check weekly drawdown limit
                if not await self.weekly_guard.check_weekly_drawdown():
                    logger.info(f"Weekly drawdown limit hit, skipping {symbol}")
                    continue

                # Scan for signals
                signals = await self.signals.scan_symbol(symbol)

                for signal in signals:
                    if signal.status == "PENDING":
                        await ws_manager.broadcast({"type": "signal_update", "data": {
                            "symbol": signal.symbol,
                            "strategy_type": signal.strategy_type,
                            "direction": signal.direction}})
                    decision = await self.strategy.evaluate_signal(
                        signal,
                        paper_mode=(self._mode == "paper"),
                        open_positions=open_positions,
                        correlation_guard=self.correlation_guard,
                    )

                    if not self._record_scan_exposure(
                        signal, decision, open_positions, daily_guard_positions,
                        mode=self._mode,
                    ):
                        logger.critical(
                            "[%s] Live exposure after a failed entry could not be "
                            "safely accounted; aborting the rest of this scan pass",
                            signal.symbol,
                        )
                        return

                    if decision.decision == "EXECUTE":
                        # Correlation and same-symbol guards run for later
                        # candidates in this pass. Include this fill now; the
                        # exchange snapshot above is otherwise stale until the
                        # next scan.
                        await ws_manager.broadcast({"type": "position_update", "data": {
                            "symbol": signal.symbol, "side": signal.direction,
                            "mode": self._mode}})
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

    @staticmethod
    def _record_scan_exposure(signal, decision, open_positions: dict,
                              daily_guard_positions: dict, mode: str) -> bool:
        """Reserve fills and protected survivors in this pass's risk snapshots.

        Return False when post-failure exchange state is unknown, so the caller
        aborts before evaluating another candidate against stale exposure.
        """
        plan = (decision.plan if decision.decision == "EXECUTE"
                else decision.surviving_position)
        if plan:
            try:
                quantity = float(plan.get(
                    "quantity", plan.get("size", 0)))
                risk_distance = float(plan.get(
                    "initialRiskDistance", plan.get("initial_risk_distance", 0)))
                unrealized = float(plan.get("unrealizedPnl", 0) or 0)
                if (not all(math.isfinite(x) for x in
                            (quantity, risk_distance, unrealized)) or
                        quantity <= 0 or risk_distance <= 0):
                    return False
                position = {
                    "side": plan.get("side", signal.direction),
                    "quantity": quantity,
                    "initialRiskDistance": risk_distance,
                }
                open_positions[signal.symbol] = position
                if mode == "live":
                    daily_guard_positions[signal.symbol] = {
                        **position, "unrealizedPnl": unrealized,
                    }
            except (TypeError, ValueError, AttributeError):
                return False
        return not decision.exposure_unknown

    async def _minutes_since_last_trade(self, symbol: str) -> Optional[float]:
        """Minutes since the most recent closed trade on `symbol` (None if no
        trades yet). Backs the per-symbol cooldown overtrading guard."""
        if self.session_factory is None:
            return None
        async with self.session_factory() as session:
            result = await session.execute(
                select(Trade.closed_at)
                .where(Trade.symbol == symbol)
                .order_by(Trade.closed_at.desc())
                .limit(1)
            )
            last = result.scalar_one_or_none()
        if last is None:
            return None
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 60.0

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
        """Log equity snapshot.

        Records `totalMarginBalance` from /fapi/v2/account — the account-wide,
        USD-denominated equity that Binance's own UI labels "Margin Balance".

        It previously recorded the USDT row of /fapi/v2/balance, which is NOT
        the account: this account also holds BTC and BNB as cross-margin
        collateral, so the USDT row was ~$23.90 against a real margin balance of
        ~$223.72 — the equity curve understated the account by ~90%. Summing
        `balance` across assets does not work either, since those values are
        denominated in each asset (0.0022 BTC), not USD.

        `availableBalance` was rejected earlier for a different reason: it is
        free margin, which dips on every entry and recovers on exit.
        """
        try:
            equity = await self.binance.get_account_equity()
            usdt_balance = float(equity.get("margin_balance", 0) or 0)

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

    async def _open_positions_from_db(self) -> list[dict]:
        """OPEN positions straight from the database, as plain JSON types.

        The fallback for `_open_position_summaries` — see its docstring for why
        the cache alone cannot answer.
        """
        if self.session_factory is None:
            return []
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(Position).where(Position.status == "OPEN"))
                rows = result.scalars().all()
        except Exception as exc:
            # Never fail the status endpoint over a reporting nicety; an empty
            # list is the pre-existing shape and the dashboard has its own
            # source of truth for positions.
            logger.warning("get_status could not read open positions: %s", exc)
            return []
        return [
            {
                "id": r.id,
                "symbol": r.symbol,
                "side": r.side,
                "entry_price": float(r.entry_price) if r.entry_price is not None else None,
                "quantity": float(r.quantity) if r.quantity is not None else None,
                "stop_loss": float(r.stop_loss) if r.stop_loss is not None else None,
                "take_profit": float(r.take_profit) if r.take_profit is not None else None,
                "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            }
            for r in rows
        ]

    async def _open_position_summaries(self) -> list[dict]:
        """The engine's real OPEN positions, for `/bot/status`.

        This key was a literal `[]`, so the endpoint reported a flat account
        while three positions were open — observed live. Nothing in the
        dashboard reads it (that page uses `/dashboard/overview`), so the lie
        only ever misled other consumers and an operator with curl.

        Prefers the `PositionManager` snapshot cache (refreshed by the 30s REST
        pass), for the same reason the bookTicker tick path does: no query per
        call. The cache CANNOT be trusted alone — `_cached_open_positions()`
        returns `[]` both when the account is genuinely flat and when the first
        pass has not run yet (the engine only populates it once
        `PositionManager.start()` is running), and an operator asking "what is
        open?" must not be told "nothing" in the second case. So an empty cache
        falls back to a single indexed `status='OPEN'` query. `/bot/status` is
        not polled by the frontend (the dashboard reads
        `/dashboard/overview`), so that fallback is a low-rate operator call, not
        a hot path.
        """
        rows = None
        pm = self.position_manager
        if pm is not None:
            try:
                rows = pm._cached_open_positions()
            except Exception as exc:
                logger.debug("get_status could not read the position cache: %s", exc)
                rows = None
        if rows:
            return [
                {
                    "id": r.get("id"),
                    "symbol": r.get("symbol"),
                    "side": r.get("side"),
                    "entry_price": r.get("entry_price"),
                    "quantity": r.get("quantity"),
                    "stop_loss": r.get("stop_loss"),
                    "take_profit": r.get("take_profit"),
                    "opened_at": r.opened_at.isoformat()
                    if isinstance(getattr(r, "opened_at", None), datetime) else None,
                }
                for r in rows
            ]
        return await self._open_positions_from_db()

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
            "open_positions": await self._open_position_summaries(),
            "enabled_symbols": await self._get_enabled_symbols(),
        }
