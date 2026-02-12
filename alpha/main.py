"""Alpha — main entry point. Runs the bot loop with auto-strategy selection."""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import ccxt.async_support as ccxt
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from alpha.alerts import AlertManager
from alpha.config import config
from alpha.db import Database
from alpha.market_analyzer import MarketAnalyzer
from alpha.risk_manager import RiskManager
from alpha.strategies.arbitrage import ArbitrageStrategy
from alpha.strategies.base import BaseStrategy, StrategyName
from alpha.strategies.grid import GridStrategy
from alpha.strategies.momentum import MomentumStrategy
from alpha.strategy_selector import StrategySelector
from alpha.trade_executor import TradeExecutor
from alpha.utils import setup_logger

logger = setup_logger("main")


class AlphaBot:
    """Top-level bot orchestrator."""

    def __init__(self) -> None:
        # Core components (initialized in start())
        self.binance: ccxt.Exchange | None = None
        self.kucoin: ccxt.Exchange | None = None
        self.db = Database()
        self.alerts = AlertManager()
        self.risk_manager = RiskManager()
        self.executor: TradeExecutor | None = None
        self.analyzer: MarketAnalyzer | None = None
        self.selector: StrategySelector | None = None

        # Strategies
        self._strategies: dict[StrategyName, BaseStrategy] = {}
        self._active_strategy: BaseStrategy | None = None

        # Scheduler
        self._scheduler = AsyncIOScheduler()

        # Shutdown flag
        self._running = False

    async def start(self) -> None:
        """Initialize all components and start the main loop."""
        logger.info("=" * 60)
        logger.info("  ALPHA BOT — Starting up")
        logger.info("  Pair: %s | Capital: $%.2f", config.trading.pair, config.trading.starting_capital)
        logger.info("=" * 60)

        # Connect external services
        await self._init_exchanges()
        await self.db.connect()
        await self.alerts.connect()

        # Restore state from DB if available
        await self._restore_state()

        # Build components
        self.executor = TradeExecutor(self.binance, db=self.db, alerts=self.alerts)  # type: ignore[arg-type]
        self.analyzer = MarketAnalyzer(self.binance, config.trading.pair)  # type: ignore[arg-type]
        self.selector = StrategySelector(db=self.db, arb_enabled=self.kucoin is not None)

        # Register strategies
        self._strategies = {
            StrategyName.GRID: GridStrategy(config.trading.pair, self.executor, self.risk_manager),
            StrategyName.MOMENTUM: MomentumStrategy(config.trading.pair, self.executor, self.risk_manager),
            StrategyName.ARBITRAGE: ArbitrageStrategy(
                config.trading.pair, self.executor, self.risk_manager, self.kucoin
            ),
        }

        # Schedule periodic tasks
        self._scheduler.add_job(self._analysis_cycle, "interval", seconds=config.trading.analysis_interval_sec)
        self._scheduler.add_job(self._daily_reset, "cron", hour=0, minute=0)
        self._scheduler.add_job(self._save_status, "interval", minutes=5)
        self._scheduler.add_job(self._poll_commands, "interval", seconds=10)
        self._scheduler.start()

        # Notify
        await self.alerts.send_bot_started(config.trading.pair, config.trading.starting_capital)

        # Register shutdown signals
        self._running = True
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown("Signal received")))

        # Run initial analysis immediately
        await self._analysis_cycle()

        # Keep running
        logger.info("Bot running — press Ctrl+C to stop")
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self.shutdown("KeyboardInterrupt")

    async def shutdown(self, reason: str = "Shutdown requested") -> None:
        """Graceful shutdown — stop strategies, save state, close connections."""
        if not self._running:
            return
        self._running = False
        logger.info("Shutting down: %s", reason)

        # Stop active strategy
        if self._active_strategy:
            await self._active_strategy.stop()

        # Save final state
        await self._save_status()

        # Stop scheduler
        self._scheduler.shutdown(wait=False)

        # Notify
        await self.alerts.send_bot_stopped(reason)

        # Close exchange connections
        if self.binance:
            await self.binance.close()
        if self.kucoin:
            await self.kucoin.close()

        logger.info("Shutdown complete")

    # -- Core cycle ------------------------------------------------------------

    async def _analysis_cycle(self) -> None:
        """Run market analysis and switch strategy if needed."""
        if not self._running:
            return

        try:
            analysis = await self.analyzer.analyze()  # type: ignore[union-attr]

            # Check for arbitrage opportunity
            arb_opportunity = False
            if self.kucoin:
                arb_opportunity = await self._check_arb_opportunity()

            selected = await self.selector.select(analysis, arb_opportunity)  # type: ignore[union-attr]

            await self._switch_strategy(selected)

        except Exception:
            logger.exception("Error in analysis cycle")

    async def _switch_strategy(self, name: StrategyName | None) -> None:
        """Stop current strategy and start the new one."""
        current_name = self._active_strategy.name if self._active_strategy else None

        if current_name == name:
            return  # no change

        # Stop current
        if self._active_strategy:
            await self._active_strategy.stop()
            self._active_strategy = None

        if name is None:
            logger.info("No strategy active (paused)")
            return

        # Start new
        strategy = self._strategies.get(name)
        if strategy is None:
            logger.error("Strategy %s not registered", name)
            return

        self._active_strategy = strategy
        await strategy.start()

        # Alert
        await self.alerts.send_strategy_switch(
            old=current_name.value if current_name else None,
            new=name.value,
            reason=self.analyzer.last_analysis.reason if self.analyzer and self.analyzer.last_analysis else "initial",
        )

    async def _check_arb_opportunity(self) -> bool:
        """Quick check if there's a cross-exchange spread."""
        if not self.kucoin:
            return False
        try:
            binance_ticker = await self.binance.fetch_ticker(config.trading.pair)  # type: ignore[union-attr]
            kucoin_ticker = await self.kucoin.fetch_ticker(config.trading.pair)
            bp = binance_ticker["last"]
            kp = kucoin_ticker["last"]
            spread_pct = abs((bp - kp) / bp) * 100
            return spread_pct > config.trading.arb_min_spread_pct
        except Exception:
            return False

    # -- Scheduled jobs --------------------------------------------------------

    async def _daily_reset(self) -> None:
        """Midnight reset: send daily summary, reset daily P&L."""
        logger.info("Daily reset triggered")
        await self.alerts.send_daily_summary(
            total_pnl=self.risk_manager.daily_pnl,
            win_rate=self.risk_manager.win_rate,
            trades_count=len(self.risk_manager.trade_results),
            capital=self.risk_manager.capital,
            strategy=self._active_strategy.name.value if self._active_strategy else None,
        )
        self.risk_manager.reset_daily()

    async def _save_status(self) -> None:
        """Persist bot state to Supabase for crash recovery + dashboard display."""
        rm = self.risk_manager
        last = self.analyzer.last_analysis if self.analyzer else None
        status = {
            "total_pnl": rm.capital - config.trading.starting_capital,
            "daily_pnl": rm.daily_pnl,
            "daily_loss_pct": rm.daily_loss_pct,
            "win_rate": rm.win_rate,
            "total_trades": len(rm.trade_results),
            "open_positions": len(rm.open_positions),
            "active_strategy": self._active_strategy.name.value if self._active_strategy else None,
            "market_condition": last.condition.value if last else None,
            "capital": rm.capital,
            "pair": config.trading.pair,
            "is_running": self._running,
            "is_paused": rm.is_paused,
            "pause_reason": rm._pause_reason or None,
        }
        await self.db.save_bot_status(status)

    async def _poll_commands(self) -> None:
        """Check Supabase for pending dashboard commands and execute them."""
        try:
            commands = await self.db.poll_pending_commands()
            for cmd in commands:
                await self._handle_command(cmd)
        except Exception:
            logger.exception("Error polling commands")

    async def _handle_command(self, cmd: dict) -> None:
        """Process a single dashboard command."""
        cmd_id: int = cmd["id"]
        command: str = cmd["command"]
        params: dict = cmd.get("params") or {}
        result_msg = "ok"

        logger.info("Processing command %d: %s %s", cmd_id, command, params)

        try:
            if command == "pause":
                self.risk_manager.is_paused = True
                self.risk_manager._pause_reason = params.get("reason", "Paused via dashboard")
                if self._active_strategy:
                    await self._active_strategy.stop()
                    self._active_strategy = None
                await self.alerts.send_risk_alert("Bot paused via dashboard")
                result_msg = "Bot paused"

            elif command == "resume":
                self.risk_manager.unpause()
                await self._analysis_cycle()  # re-evaluate and start strategy
                await self.alerts.send_risk_alert("Bot resumed via dashboard")
                result_msg = "Bot resumed"

            elif command == "force_strategy":
                strategy_name = params.get("strategy")
                if strategy_name:
                    try:
                        target = StrategyName(strategy_name)
                        self.risk_manager.unpause()
                        await self._switch_strategy(target)
                        result_msg = f"Forced strategy: {strategy_name}"
                    except ValueError:
                        result_msg = f"Unknown strategy: {strategy_name}"
                else:
                    result_msg = "Missing 'strategy' param"

            elif command == "update_config":
                # Apply runtime config overrides (non-persistent)
                if "pair" in params:
                    logger.info("Pair override not supported at runtime (restart required)")
                    result_msg = "Pair change requires restart"
                elif "max_position_pct" in params:
                    self.risk_manager.max_position_pct = float(params["max_position_pct"])
                    result_msg = f"max_position_pct → {params['max_position_pct']}"
                elif "daily_loss_limit_pct" in params:
                    self.risk_manager.daily_loss_limit_pct = float(params["daily_loss_limit_pct"])
                    result_msg = f"daily_loss_limit_pct → {params['daily_loss_limit_pct']}"
                else:
                    result_msg = f"Config updated: {params}"
            else:
                result_msg = f"Unknown command: {command}"

        except Exception as e:
            result_msg = f"Error: {e}"
            logger.exception("Failed to handle command %d", cmd_id)

        await self.db.mark_command_executed(cmd_id, result_msg)

    async def _restore_state(self) -> None:
        """Restore capital and state from last saved status."""
        last = await self.db.get_last_bot_status()
        if last:
            self.risk_manager.capital = last.get("capital", config.trading.starting_capital)
            logger.info("Restored state from DB — capital: $%.2f", self.risk_manager.capital)
        else:
            logger.info("No previous state found — starting fresh")

    # -- Exchange init ---------------------------------------------------------

    async def _init_exchanges(self) -> None:
        """Create ccxt exchange instances."""
        # Binance (required)
        self.binance = ccxt.binance({
            "apiKey": config.binance.api_key,
            "secret": config.binance.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if not config.binance.api_key:
            logger.warning("Binance API key not set — running in sandbox/read-only mode")
            self.binance.set_sandbox_mode(True)

        # KuCoin (optional, for arbitrage)
        if config.kucoin.api_key:
            self.kucoin = ccxt.kucoin({
                "apiKey": config.kucoin.api_key,
                "secret": config.kucoin.secret,
                "password": config.kucoin.passphrase,
                "enableRateLimit": True,
            })
            logger.info("KuCoin exchange initialized (arbitrage enabled)")
        else:
            logger.info("KuCoin credentials not set — arbitrage disabled")


def main() -> None:
    """Entry point."""
    bot = AlphaBot()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
