"""Alpha — main entry point. Multi-pair, multi-exchange concurrent orchestrator.

Supports Binance (spot) and Delta Exchange India (futures) in parallel.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import aiohttp
import ccxt.async_support as ccxt
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from alpha.alerts import AlertManager
from alpha.config import config
from alpha.db import Database
from alpha.market_analyzer import MarketAnalyzer
from alpha.risk_manager import RiskManager
from alpha.strategies.arbitrage import ArbitrageStrategy
from alpha.strategies.base import BaseStrategy, StrategyName
from alpha.strategies.futures_momentum import FuturesMomentumStrategy
from alpha.strategies.grid import GridStrategy
from alpha.strategies.momentum import MomentumStrategy
from alpha.strategy_selector import StrategySelector
from alpha.trade_executor import TradeExecutor
from alpha.utils import setup_logger

logger = setup_logger("main")


class AlphaBot:
    """Top-level bot orchestrator — runs multiple pairs and exchanges concurrently."""

    def __init__(self) -> None:
        # Core components (initialized in start())
        self.binance: ccxt.Exchange | None = None
        self.kucoin: ccxt.Exchange | None = None
        self.delta: ccxt.Exchange | None = None
        self.db = Database()
        self.alerts = AlertManager()
        self.risk_manager = RiskManager()
        self.executor: TradeExecutor | None = None
        self.analyzer: MarketAnalyzer | None = None
        self.delta_analyzer: MarketAnalyzer | None = None
        self.selector: StrategySelector | None = None

        # Multi-pair: Binance spot
        self.pairs: list[str] = config.trading.pairs
        # Delta futures pairs
        self.delta_pairs: list[str] = config.delta.pairs

        # Per-pair strategy instances:  pair -> {StrategyName -> instance}
        self._strategies: dict[str, dict[StrategyName, BaseStrategy]] = {}
        # Per-pair active strategy:  pair -> running strategy or None
        self._active_strategies: dict[str, BaseStrategy | None] = {}

        # Scheduler
        self._scheduler = AsyncIOScheduler()

        # Shutdown flag
        self._running = False

        # Hourly tracking
        self._hourly_pnl: float = 0.0
        self._hourly_wins: int = 0
        self._hourly_losses: int = 0
        self._daily_loss_warned: bool = False

    @property
    def all_pairs(self) -> list[str]:
        """All tracked pairs across both exchanges."""
        return self.pairs + (self.delta_pairs if self.delta else [])

    async def start(self) -> None:
        """Initialize all components and start the main loop."""
        logger.info("=" * 60)
        logger.info("  ALPHA BOT -- Starting up (multi-pair, multi-exchange)")
        logger.info("  Binance pairs: %s", ", ".join(self.pairs))
        if self.delta_pairs:
            logger.info("  Delta pairs:   %s", ", ".join(self.delta_pairs))
        logger.info("  Capital: $%.2f", config.trading.starting_capital)
        logger.info("=" * 60)

        # Connect external services
        await self._init_exchanges()
        await self.db.connect()
        await self.alerts.connect()

        # Restore state from DB if available
        await self._restore_state()

        # Hook into risk_manager.record_close to track hourly stats
        _original_record_close = self.risk_manager.record_close

        def _tracked_record_close(pair: str, pnl: float) -> None:
            _original_record_close(pair, pnl)
            self._hourly_pnl += pnl
            if pnl >= 0:
                self._hourly_wins += 1
            else:
                self._hourly_losses += 1

        self.risk_manager.record_close = _tracked_record_close  # type: ignore[assignment]

        # Build components
        self.executor = TradeExecutor(
            self.binance,  # type: ignore[arg-type]
            db=self.db,
            alerts=self.alerts,
            delta_exchange=self.delta,
        )
        self.analyzer = MarketAnalyzer(self.binance, pair=self.pairs[0])  # type: ignore[arg-type]
        if self.delta:
            self.delta_analyzer = MarketAnalyzer(
                self.delta, pair=self.delta_pairs[0] if self.delta_pairs else None,
            )
        self.selector = StrategySelector(
            db=self.db,
            arb_enabled=self.kucoin is not None,
            futures_pairs=set(self.delta_pairs) if self.delta else None,
        )

        # Load minimum order sizes for all exchanges
        await self.executor.load_market_limits(
            self.pairs,
            delta_pairs=self.delta_pairs if self.delta else None,
        )

        # Register strategies per Binance pair
        for pair in self.pairs:
            self._strategies[pair] = {
                StrategyName.GRID: GridStrategy(pair, self.executor, self.risk_manager),
                StrategyName.MOMENTUM: MomentumStrategy(pair, self.executor, self.risk_manager),
                StrategyName.ARBITRAGE: ArbitrageStrategy(
                    pair, self.executor, self.risk_manager, self.kucoin,
                ),
            }
            self._active_strategies[pair] = None

        # Register strategies per Delta pair
        if self.delta:
            for pair in self.delta_pairs:
                self._strategies[pair] = {
                    StrategyName.FUTURES_MOMENTUM: FuturesMomentumStrategy(
                        pair, self.executor, self.risk_manager,
                        exchange=self.delta,
                    ),
                }
                self._active_strategies[pair] = None

        # Schedule periodic tasks
        self._scheduler.add_job(
            self._analysis_cycle, "interval",
            seconds=config.trading.analysis_interval_sec,
        )
        self._scheduler.add_job(self._daily_reset, "cron", hour=0, minute=0)
        self._scheduler.add_job(self._hourly_report, "cron", minute=0)  # every hour
        self._scheduler.add_job(self._save_status, "interval", minutes=5)
        self._scheduler.add_job(self._poll_commands, "interval", seconds=10)
        self._scheduler.start()

        # Fetch exchange balances for startup alert
        binance_bal = await self._fetch_balance(self.binance, "USDT")
        delta_bal = await self._fetch_balance(self.delta, "USDT") if self.delta else None

        # Capital = sum of actual exchange balances
        total_capital = (binance_bal or 0) + (delta_bal or 0)

        # Notify
        await self.alerts.send_bot_started(
            self.all_pairs, total_capital,
            binance_balance=binance_bal, delta_balance=delta_bal,
        )

        # Register shutdown signals
        self._running = True
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown("Signal received")))

        # Run initial analysis immediately
        await self._analysis_cycle()

        # Keep running
        logger.info(
            "Bot running -- %d Binance pairs + %d Delta pairs -- press Ctrl+C to stop",
            len(self.pairs), len(self.delta_pairs) if self.delta else 0,
        )
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self.shutdown("KeyboardInterrupt")

    async def shutdown(self, reason: str = "Shutdown requested") -> None:
        """Graceful shutdown -- stop all strategies, save state, close connections."""
        if not self._running:
            return
        self._running = False
        logger.info("Shutting down: %s", reason)

        # Stop all active strategies concurrently
        stop_tasks = []
        for pair, strategy in self._active_strategies.items():
            if strategy:
                stop_tasks.append(strategy.stop())
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        self._active_strategies = {p: None for p in self.all_pairs}

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
        if self.delta:
            await self.delta.close()

        logger.info("Shutdown complete")

    # -- Core cycle ------------------------------------------------------------

    async def _analysis_cycle(self) -> None:
        """Analyze all pairs (both exchanges) concurrently, switch strategies by signal strength."""
        if not self._running:
            return

        try:
            # 1. Analyze all pairs in parallel
            analysis_tasks = [
                self.analyzer.analyze(pair)  # type: ignore[union-attr]
                for pair in self.pairs
            ]
            # Delta pairs use the delta analyzer
            if self.delta and self.delta_analyzer:
                for pair in self.delta_pairs:
                    analysis_tasks.append(self.delta_analyzer.analyze(pair))

            all_tracked = self.all_pairs
            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            # 2. Collect successful analyses
            analyses = []
            for pair, result in zip(all_tracked, results):
                if isinstance(result, Exception):
                    logger.error("Analysis failed for %s: %s", pair, result)
                else:
                    analyses.append(result)

            # 3. Sort by signal_strength descending -- best opportunities first
            analyses.sort(key=lambda a: a.signal_strength, reverse=True)

            logger.info(
                "Analysis complete -- strength ranking: %s",
                ", ".join(f"{a.pair}={a.signal_strength:.0f}" for a in analyses),
            )

            # 4. Select strategy per pair, switch, and collect changes for alert
            strategy_changes: list[dict[str, Any]] = []
            for analysis in analyses:
                pair = analysis.pair

                # Record old strategy before switching
                old_strat = self._active_strategies.get(pair)
                old_name = old_strat.name.value if old_strat else None

                # Check for arb opportunity on Binance pairs only
                arb_opportunity = False
                if self.kucoin and pair in self.pairs:
                    arb_opportunity = await self._check_arb_opportunity(pair)

                selected = await self.selector.select(analysis, arb_opportunity)  # type: ignore[union-attr]
                await self._switch_strategy(pair, selected)

                # Detect change
                new_name = selected.value if selected else None
                if new_name != old_name:
                    strategy_changes.append({
                        "pair": pair,
                        "condition": analysis.condition.value,
                        "adx": analysis.adx,
                        "rsi": analysis.rsi,
                        "old_strategy": old_name,
                        "new_strategy": new_name,
                        "direction": analysis.direction,
                    })

            # 4b. Send market update alert (only if strategies changed)
            if strategy_changes:
                await self.alerts.send_market_update(strategy_changes)

            # 5. Check liquidation risk for futures positions
            await self._check_liquidation_risks()

            # 6. Check daily loss warning (alert once when > 80% of limit)
            rm = self.risk_manager
            loss_threshold = rm.daily_loss_limit_pct * 0.8
            if rm.daily_loss_pct >= loss_threshold and not self._daily_loss_warned:
                self._daily_loss_warned = True
                await self.alerts.send_daily_loss_warning(
                    rm.daily_loss_pct, rm.daily_loss_limit_pct,
                )

        except Exception:
            logger.exception("Error in analysis cycle")

    async def _switch_strategy(self, pair: str, name: StrategyName | None) -> None:
        """Stop current strategy for a pair and start the new one."""
        current = self._active_strategies.get(pair)
        current_name = current.name if current else None

        if current_name == name:
            return  # no change

        # Stop current
        if current:
            await current.stop()
            self._active_strategies[pair] = None

        if name is None:
            logger.info("[%s] No strategy active (paused)", pair)
            return

        # Start new
        pair_strategies = self._strategies.get(pair, {})
        strategy = pair_strategies.get(name)
        if strategy is None:
            logger.error("[%s] Strategy %s not registered", pair, name)
            return

        self._active_strategies[pair] = strategy
        await strategy.start()

        # Alert
        last = None
        if self.analyzer:
            last = self.analyzer.last_analysis_for(pair)  # type: ignore[union-attr]
        if last is None and self.delta_analyzer:
            last = self.delta_analyzer.last_analysis_for(pair)
        await self.alerts.send_strategy_switch(
            pair=pair,
            old=current_name.value if current_name else None,
            new=name.value,
            reason=last.reason if last else "initial",
        )

    async def _check_arb_opportunity(self, pair: str) -> bool:
        """Quick check if there's a cross-exchange spread for a pair."""
        if not self.kucoin:
            return False
        try:
            binance_ticker = await self.binance.fetch_ticker(pair)  # type: ignore[union-attr]
            kucoin_ticker = await self.kucoin.fetch_ticker(pair)
            bp = binance_ticker["last"]
            kp = kucoin_ticker["last"]
            spread_pct = abs((bp - kp) / bp) * 100
            return spread_pct > config.trading.arb_min_spread_pct
        except Exception:
            return False

    async def _check_liquidation_risks(self) -> None:
        """Monitor futures positions for liquidation proximity."""
        if not self.delta:
            return
        for pair in self.delta_pairs:
            try:
                ticker = await self.delta.fetch_ticker(pair)
                current_price = ticker["last"]
                distance = self.risk_manager.check_liquidation_risk(pair, current_price)
                if distance is not None and distance < 10.0:
                    # Find position info for alert
                    for pos in self.risk_manager.open_positions:
                        if pos.pair == pair and pos.leverage > 1:
                            # Calculate liq price for the alert
                            if pos.position_type == "long":
                                liq_price = pos.entry_price * (1 - 1 / pos.leverage)
                            else:
                                liq_price = pos.entry_price * (1 + 1 / pos.leverage)
                            await self.alerts.send_liquidation_warning(
                                pair, distance, pos.position_type, pos.leverage,
                                current_price=current_price, liq_price=liq_price,
                            )
                            logger.warning(
                                "[%s] LIQUIDATION WARNING: %.1f%% from liquidation (%s %dx)",
                                pair, distance, pos.position_type, pos.leverage,
                            )
                            break
            except Exception:
                logger.debug("Could not check liquidation risk for %s", pair)

    # -- Scheduled jobs --------------------------------------------------------

    async def _daily_reset(self) -> None:
        """Midnight reset: send daily summary, reset daily P&L."""
        logger.info("Daily reset triggered")
        rm = self.risk_manager

        # Count today's wins and losses
        total = len(rm.trade_results)
        wins = sum(1 for w in rm.trade_results if w)
        losses = total - wins

        # Find best and worst trades from daily pnl by pair
        pnl_map = dict(rm.daily_pnl_by_pair)
        best_trade = None
        worst_trade = None
        if pnl_map:
            best_pair = max(pnl_map, key=pnl_map.get)  # type: ignore[arg-type]
            worst_pair = min(pnl_map, key=pnl_map.get)  # type: ignore[arg-type]
            best_trade = {"pair": best_pair, "pnl": pnl_map[best_pair]}
            worst_trade = {"pair": worst_pair, "pnl": pnl_map[worst_pair]}

        # Fetch live exchange balances
        binance_bal = await self._fetch_balance(self.binance, "USDT")
        delta_bal = await self._fetch_balance(self.delta, "USDT") if self.delta else None

        # Capital = sum of actual exchange balances
        total_capital = (binance_bal or 0) + (delta_bal or 0)

        await self.alerts.send_daily_summary(
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=rm.win_rate,
            daily_pnl=rm.daily_pnl,
            capital=total_capital,
            pnl_by_pair=pnl_map,
            best_trade=best_trade,
            worst_trade=worst_trade,
            binance_balance=binance_bal,
            delta_balance=delta_bal,
        )
        rm.reset_daily()
        self._daily_loss_warned = False
        # Also reset hourly counters at midnight
        self._hourly_pnl = 0.0
        self._hourly_wins = 0
        self._hourly_losses = 0

    async def _hourly_report(self) -> None:
        """Send hourly summary to Telegram, then reset hourly counters."""
        try:
            rm = self.risk_manager

            # Build open positions list for the alert
            open_pos = [
                {"pair": p.pair, "position_type": p.position_type, "exchange": p.exchange}
                for p in rm.open_positions
            ]

            # Build active strategies map
            active_map: dict[str, str | None] = {}
            for pair in self.all_pairs:
                strat = self._active_strategies.get(pair)
                active_map[pair] = strat.name.value if strat else None

            # Fetch live exchange balances
            binance_bal = await self._fetch_balance(self.binance, "USDT")
            delta_bal = await self._fetch_balance(self.delta, "USDT") if self.delta else None

            # Capital = sum of actual exchange balances
            total_capital = (binance_bal or 0) + (delta_bal or 0)

            await self.alerts.send_hourly_summary(
                open_positions=open_pos,
                hourly_wins=self._hourly_wins,
                hourly_losses=self._hourly_losses,
                hourly_pnl=self._hourly_pnl,
                daily_pnl=rm.daily_pnl,
                capital=total_capital,
                active_strategies=active_map,
                win_rate_24h=rm.win_rate,
                binance_balance=binance_bal,
                delta_balance=delta_bal,
            )

            # Reset hourly counters
            self._hourly_pnl = 0.0
            self._hourly_wins = 0
            self._hourly_losses = 0
        except Exception:
            logger.exception("Error sending hourly report")

    async def _save_status(self) -> None:
        """Persist bot state to Supabase for crash recovery + dashboard display."""
        rm = self.risk_manager

        # Build per-pair info
        active_map: dict[str, str | None] = {}
        for pair in self.all_pairs:
            strat = self._active_strategies.get(pair)
            active_map[pair] = strat.name.value if strat else None

        # Use primary pair's analysis for condition
        last = self.analyzer.last_analysis if self.analyzer else None

        status = {
            "total_pnl": rm.capital - config.trading.starting_capital,
            "daily_pnl": rm.daily_pnl,
            "daily_loss_pct": rm.daily_loss_pct,
            "win_rate": rm.win_rate,
            "total_trades": len(rm.trade_results),
            "open_positions": len(rm.open_positions),
            "active_strategy": active_map.get(self.pairs[0]) if self.pairs else None,
            "market_condition": last.condition.value if last else None,
            "capital": rm.capital,
            "pair": ", ".join(self.all_pairs),
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
                # Stop all active strategies
                stop_tasks = []
                for pair, strategy in self._active_strategies.items():
                    if strategy:
                        stop_tasks.append(strategy.stop())
                        self._active_strategies[pair] = None
                if stop_tasks:
                    await asyncio.gather(*stop_tasks, return_exceptions=True)
                await self.alerts.send_command_confirmation("pause")
                result_msg = "Bot paused"

            elif command == "resume":
                self.risk_manager.unpause()
                await self._analysis_cycle()  # re-evaluate and start strategies
                await self.alerts.send_command_confirmation("resume")
                result_msg = "Bot resumed"

            elif command == "force_strategy":
                strategy_name = params.get("strategy")
                target_pair = params.get("pair", self.pairs[0])  # default to primary pair
                if strategy_name:
                    try:
                        target = StrategyName(strategy_name)
                        self.risk_manager.unpause()
                        await self._switch_strategy(target_pair, target)
                        short = target_pair.split("/")[0] if "/" in target_pair else target_pair
                        await self.alerts.send_command_confirmation(
                            "force_strategy", f"{strategy_name.capitalize()} on {short}",
                        )
                        result_msg = f"Forced {strategy_name} on {target_pair}"
                    except ValueError:
                        result_msg = f"Unknown strategy: {strategy_name}"
                else:
                    result_msg = "Missing 'strategy' param"

            elif command == "update_config":
                if "max_position_pct" in params:
                    self.risk_manager.max_position_pct = float(params["max_position_pct"])
                    result_msg = f"max_position_pct -> {params['max_position_pct']}"
                elif "daily_loss_limit_pct" in params:
                    self.risk_manager.daily_loss_limit_pct = float(params["daily_loss_limit_pct"])
                    result_msg = f"daily_loss_limit_pct -> {params['daily_loss_limit_pct']}"
                else:
                    result_msg = f"Config updated: {params}"
                await self.alerts.send_command_confirmation("update_config", result_msg)
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
            logger.info("Restored state from DB -- capital: $%.2f", self.risk_manager.capital)
        else:
            logger.info("No previous state found -- starting fresh")

    # -- Exchange init ---------------------------------------------------------

    async def _init_exchanges(self) -> None:
        """Create ccxt exchange instances.

        Uses the threaded DNS resolver to avoid aiodns failures on Windows.
        """
        # Force threaded resolver so aiohttp doesn't depend on aiodns/c-ares
        resolver = aiohttp.resolver.ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver, ssl=True)
        session = aiohttp.ClientSession(connector=connector)

        # Binance (required)
        self.binance = ccxt.binance({
            "apiKey": config.binance.api_key,
            "secret": config.binance.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
            "session": session,
        })
        if not config.binance.api_key:
            logger.warning("Binance API key not set -- running in sandbox/read-only mode")
            self.binance.set_sandbox_mode(True)

        # KuCoin (optional, for arbitrage)
        if config.kucoin.api_key:
            kucoin_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver(), ssl=True)
            )
            self.kucoin = ccxt.kucoin({
                "apiKey": config.kucoin.api_key,
                "secret": config.kucoin.secret,
                "password": config.kucoin.passphrase,
                "enableRateLimit": True,
                "session": kucoin_session,
            })
            logger.info("KuCoin exchange initialized (arbitrage enabled)")
        else:
            logger.info("KuCoin credentials not set -- arbitrage disabled")

        # Delta Exchange India (optional, for futures)
        if config.delta.api_key:
            delta_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=aiohttp.resolver.ThreadedResolver(), ssl=True,
                )
            )
            self.delta = ccxt.delta({
                "apiKey": config.delta.api_key,
                "secret": config.delta.secret,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
                "session": delta_session,
            })
            # Set sandbox FIRST, then override to India endpoint
            # (sandbox mode resets URLs, so our override must come after)
            if config.delta.testnet:
                self.delta.set_sandbox_mode(True)
            self.delta.urls["api"] = config.delta.base_url
            logger.info(
                "Delta Exchange India initialized (futures enabled, testnet=%s, leverage=%dx, url=%s)",
                config.delta.testnet, config.delta.leverage, config.delta.base_url,
            )
        else:
            self.delta_pairs = []  # no Delta pairs if no credentials
            logger.info("Delta credentials not set -- futures disabled")

    @staticmethod
    async def _fetch_balance(exchange: ccxt.Exchange | None, currency: str = "USDT") -> float | None:
        """Fetch free balance for a currency on an exchange. Returns None on failure."""
        if not exchange:
            return None
        ex_id = getattr(exchange, "id", "?")
        try:
            balance = await exchange.fetch_balance()
            free_map = balance.get("free", {})

            # Try exact currency, then common alternatives
            for key in (currency, "USD", "USDC"):
                val = free_map.get(key)
                if val is not None and float(val) > 0:
                    result = float(val)
                    logger.info("Balance for %s: %s = %.4f", ex_id, key, result)
                    return result

            # If no positive balance found, check total as fallback
            total_map = balance.get("total", {})
            for key in (currency, "USD", "USDC"):
                val = total_map.get(key)
                if val is not None and float(val) > 0:
                    result = float(val)
                    logger.info("Balance for %s (total): %s = %.4f", ex_id, key, result)
                    return result

            # Log available keys to help debug
            available = {k: v for k, v in free_map.items() if v and float(v) > 0}
            logger.warning("No %s balance found on %s. Available: %s", currency, ex_id, available)
            return 0.0
        except Exception as e:
            logger.warning("Could not fetch balance from %s: %s (type: %s)", ex_id, e, type(e).__name__)
            return None


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
