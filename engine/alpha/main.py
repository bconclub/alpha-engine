"""Alpha — main entry point. Multi-pair, multi-exchange concurrent orchestrator.

Supports Binance (spot) and Delta Exchange India (futures) in parallel.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
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
from alpha.strategies.scalp import ScalpStrategy
from alpha.strategy_selector import StrategySelector
from alpha.trade_executor import TradeExecutor
from alpha.utils import iso_now, setup_logger

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

        # Scalp overlay strategies: pair -> ScalpStrategy (run independently)
        self._scalp_strategies: dict[str, ScalpStrategy] = {}

        # Scheduler
        self._scheduler = AsyncIOScheduler()

        # Shutdown flag
        self._running = False
        self._start_time: float = 0.0  # monotonic time for uptime calc

        # Suppress strategy-change alerts on the very first analysis cycle
        self._has_run_first_cycle: bool = False

        # Latest analysis data — cached for hourly market update
        self._latest_analyses: list[dict[str, Any]] = []

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
        from pathlib import Path
        from alpha.utils import get_version
        version = get_version()

        # Load soul document
        soul_path = Path(__file__).resolve().parent.parent / "SOUL.md"
        if soul_path.exists():
            soul_lines = soul_path.read_text().strip().splitlines()
            for line in soul_lines[:3]:
                if line.strip():
                    logger.info("  %s", line.strip().lstrip("#").strip())

        logger.info("=" * 60)
        logger.info("  ALPHA v%s — Delta Scalping Agent", version)
        logger.info("  DELTA ONLY — %s, %dx leverage",
                     ", ".join(self.delta_pairs), config.delta.leverage)
        logger.info("  Soul: Momentum is everything. Speed wins. Never idle.")
        logger.info("=" * 60)

        # Connect external services
        await self._init_exchanges()
        await self.db.connect()
        await self.alerts.connect()

        # Mark ALL Binance open trades as closed (dust, unsellable)
        await self._close_all_binance_trades()

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

        # Build components — Delta only (Binance kept as fallback exchange reference)
        self.executor = TradeExecutor(
            self.binance,  # type: ignore[arg-type]
            db=self.db,
            alerts=self.alerts,
            delta_exchange=self.delta,
            risk_manager=self.risk_manager,
        )

        # No Binance analyzer — Delta only
        self.pairs = []  # clear Binance pairs entirely

        if self.delta:
            self.delta_analyzer = MarketAnalyzer(
                self.delta, pair=self.delta_pairs[0] if self.delta_pairs else None,
            )
        self.selector = StrategySelector(
            db=self.db,
            arb_enabled=False,
            futures_pairs=set(self.delta_pairs) if self.delta else None,
        )

        # Load Delta market limits only
        await self.executor.load_market_limits(
            [],  # no Binance pairs
            delta_pairs=self.delta_pairs if self.delta else None,
        )

        # Register strategies — Delta only
        if self.delta:
            for pair in self.delta_pairs:
                self._strategies[pair] = {
                    StrategyName.FUTURES_MOMENTUM: FuturesMomentumStrategy(
                        pair, self.executor, self.risk_manager,
                        exchange=self.delta,
                    ),
                }
                self._active_strategies[pair] = None

        # Register scalp overlay — Delta only
        if self.delta:
            for pair in self.delta_pairs:
                self._scalp_strategies[pair] = ScalpStrategy(
                    pair, self.executor, self.risk_manager,
                    exchange=self.delta,
                    is_futures=True,
                )

        # Inject restored position state into strategy instances
        self._restore_strategy_state()

        # Start all scalp strategies immediately (they run as parallel overlays)
        for pair, scalp in self._scalp_strategies.items():
            await scalp.start()
        logger.info("Scalp overlay started on %d pairs", len(self._scalp_strategies))

        # Schedule periodic tasks
        self._scheduler.add_job(
            self._analysis_cycle, "interval",
            seconds=config.trading.analysis_interval_sec,
        )
        self._scheduler.add_job(self._daily_reset, "cron", hour=18, minute=30)  # midnight IST = 18:30 UTC
        self._scheduler.add_job(self._hourly_report, "cron", minute=0)  # every hour
        self._scheduler.add_job(self._save_status, "interval", minutes=5)
        self._scheduler.add_job(self._poll_commands, "interval", seconds=10)
        self._scheduler.start()

        # Fetch live exchange balances → per-exchange capital for trade sizing
        binance_bal = await self._fetch_portfolio_usd(self.binance)
        delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None
        self.risk_manager.update_exchange_balances(binance_bal, delta_bal)

        total_capital = self.risk_manager.capital

        # Notify
        await self.alerts.send_bot_started(
            self.all_pairs, total_capital,
            binance_balance=binance_bal, delta_balance=delta_bal,
        )

        # Register shutdown signals
        self._running = True
        self._start_time = time.monotonic()
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown("Signal received")))

        # Run initial analysis immediately
        await self._analysis_cycle()

        # Keep running
        logger.info(
            "Bot running — DELTA ONLY — %d pairs, %dx leverage — Ctrl+C to stop",
            len(self.delta_pairs) if self.delta else 0, config.delta.leverage,
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

        # Stop all active strategies concurrently (primary + scalp)
        stop_tasks = []
        for pair, strategy in self._active_strategies.items():
            if strategy:
                stop_tasks.append(strategy.stop())
        for pair, scalp in self._scalp_strategies.items():
            if scalp.is_active:
                stop_tasks.append(scalp.stop())
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
            all_analysis_dicts: list[dict[str, Any]] = []

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

                new_name = selected.value if selected else None

                # Collect analysis data for market update (ALL pairs)
                all_analysis_dicts.append({
                    "pair": pair,
                    "condition": analysis.condition.value,
                    "adx": analysis.adx,
                    "rsi": analysis.rsi,
                    "direction": analysis.direction,
                })

                # Detect change (skip initial assignment on startup)
                if new_name != old_name and self._has_run_first_cycle:
                    strategy_changes.append({
                        "pair": pair,
                        "old_strategy": old_name,
                        "new_strategy": new_name,
                        "reason": analysis.reason,
                    })

            rm = self.risk_manager

            # 4b. Cache latest analysis data for the hourly market update
            self._latest_analyses = all_analysis_dicts

            # 4c. Send batched strategy changes (only when something changed)
            if strategy_changes:
                await self.alerts.send_strategy_changes(strategy_changes)

            # Mark first cycle complete (suppress strategy change spam on startup)
            self._has_run_first_cycle = True

            # 5. Check liquidation risk for futures positions
            await self._check_liquidation_risks()

            # 6. Check daily loss warning (alert once when > 80% of limit)
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
        # Strategy change alerts are now batched in _analysis_cycle
        # via send_strategy_changes -- no per-pair alert here.

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
        binance_bal = await self._fetch_portfolio_usd(self.binance)
        delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None

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
        # Reset scalp daily stats
        for scalp in self._scalp_strategies.values():
            scalp.reset_daily_stats()

    async def _hourly_report(self) -> None:
        """Send hourly market update + summary to Telegram, then reset hourly counters.

        Positions are cross-checked against actual exchange balances, not just
        internal state. The balance shown is the REAL portfolio value including
        held assets (USDT + value of BTC/ETH/SOL etc.).
        """
        try:
            rm = self.risk_manager

            # Build active strategies map
            active_map: dict[str, str | None] = {}
            for pair in self.all_pairs:
                strat = self._active_strategies.get(pair)
                active_map[pair] = strat.name.value if strat else None

            # Fetch live exchange balances (includes held assets)
            binance_bal = await self._fetch_portfolio_usd(self.binance)
            delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None

            # Capital = sum of actual exchange balances
            total_capital = (binance_bal or 0) + (delta_bal or 0)

            # Cross-check positions against exchange: verify we actually hold coins
            verified_positions = await self._verify_positions_against_exchange()

            # Send hourly market update (all pairs grouped by exchange)
            if self._latest_analyses:
                await self.alerts.send_market_update(
                    analyses=self._latest_analyses,
                    active_strategies=active_map,
                    capital=total_capital,
                    open_position_count=len(verified_positions),
                )

            await self.alerts.send_hourly_summary(
                open_positions=verified_positions,
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

    async def _verify_positions_against_exchange(self) -> list[dict[str, Any]]:
        """Cross-check risk manager positions against actual exchange balances.

        Returns a list of verified positions (those confirmed to still exist
        on the exchange). Also cleans up stale positions from the risk manager.
        """
        rm = self.risk_manager
        verified: list[dict[str, Any]] = []

        # Fetch exchange balances once
        binance_free: dict[str, Any] = {}
        try:
            if self.binance:
                bal = await self.binance.fetch_balance()
                binance_free = bal.get("free", {})
        except Exception:
            logger.debug("Could not fetch Binance balance for position verification")
            # Fall back to internal state
            return [
                {"pair": p.pair, "position_type": p.position_type, "exchange": p.exchange}
                for p in rm.open_positions
            ]

        for pos in rm.open_positions:
            if pos.exchange == "binance":
                base = pos.pair.split("/")[0] if "/" in pos.pair else pos.pair
                held = float(binance_free.get(base, 0) or 0)
                held_value = held * pos.entry_price if pos.entry_price > 0 else 0
                if held > 0 and held_value > 0.50:
                    verified.append({
                        "pair": pos.pair,
                        "position_type": pos.position_type,
                        "exchange": pos.exchange,
                        "held": held,
                        "held_value": held_value,
                    })
                else:
                    logger.info(
                        "Position %s on %s not found on exchange (held=%.8f, value=$%.2f) — stale?",
                        pos.pair, pos.exchange, held, held_value,
                    )
            else:
                # Delta/futures: trust internal state (futures positions may not show as balances)
                verified.append({
                    "pair": pos.pair,
                    "position_type": pos.position_type,
                    "exchange": pos.exchange,
                })

        return verified

    async def _save_status(self) -> None:
        """Persist bot state to Supabase for crash recovery + dashboard display."""
        rm = self.risk_manager

        # Build per-pair info
        active_map: dict[str, str | None] = {}
        active_count = 0
        for pair in self.all_pairs:
            strat = self._active_strategies.get(pair)
            active_map[pair] = strat.name.value if strat else None
            if strat is not None:
                active_count += 1

        # Use primary pair's analysis for condition
        last = self.analyzer.last_analysis if self.analyzer else None

        # Fetch exchange balances and update per-exchange capital
        binance_bal = await self._fetch_portfolio_usd(self.binance)
        delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None
        rm.update_exchange_balances(binance_bal, delta_bal)

        # Determine bot state
        if rm.is_paused:
            bot_state = "paused"
        elif not self._running:
            bot_state = "error"
        else:
            bot_state = "running"

        # Query ACTUAL P&L from trades table (source of truth)
        # Never trust in-memory calculations for dashboard display
        trade_stats = await self.db.get_trade_stats()

        status = {
            "total_pnl": trade_stats["total_pnl"],
            "daily_pnl": rm.daily_pnl,
            "daily_loss_pct": rm.daily_loss_pct,
            "win_rate": trade_stats["win_rate"],
            "total_trades": trade_stats["total_trades"],
            "open_positions": len(rm.open_positions),
            "active_strategy": active_map.get(self.pairs[0]) if self.pairs else None,
            "market_condition": last.condition.value if last else None,
            "capital": rm.capital,
            "pair": ", ".join(self.all_pairs),
            "is_running": self._running,
            "is_paused": rm.is_paused,
            "pause_reason": rm._pause_reason or None,
            # New fields
            "binance_balance": binance_bal,
            "delta_balance": delta_bal,
            "binance_connected": self.binance is not None and binance_bal is not None,
            "delta_connected": self.delta is not None and delta_bal is not None,
            "bot_state": bot_state,
            "shorting_enabled": config.delta.enable_shorting,
            "leverage": config.delta.leverage,
            "active_strategy_count": active_count,
            "uptime_seconds": int(time.monotonic() - self._start_time) if self._start_time else 0,
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
                # Stop all active strategies (primary + scalp)
                stop_tasks = []
                for pair, strategy in self._active_strategies.items():
                    if strategy:
                        stop_tasks.append(strategy.stop())
                        self._active_strategies[pair] = None
                for pair, scalp in self._scalp_strategies.items():
                    if scalp.is_active:
                        stop_tasks.append(scalp.stop())
                if stop_tasks:
                    await asyncio.gather(*stop_tasks, return_exceptions=True)
                await self.alerts.send_command_confirmation("pause")
                result_msg = "Bot paused"

            elif command == "resume":
                self.risk_manager.unpause()
                await self._analysis_cycle()  # re-evaluate and start strategies
                # Restart scalp overlays
                for pair, scalp in self._scalp_strategies.items():
                    if not scalp.is_active:
                        await scalp.start()
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

    async def _close_all_binance_trades(self) -> None:
        """Mark all open Binance trades as closed — they're unsellable dust."""
        try:
            open_trades = await self.db.get_all_open_trades()
            binance_trades = [t for t in open_trades if t.get("exchange") == "binance"]
            if binance_trades:
                for trade in binance_trades:
                    trade_id = trade.get("id")
                    if trade_id:
                        await self.db.update_trade(trade_id, {
                            "status": "closed",
                            "closed_at": iso_now(),
                            "reason": "dust_unsellable_delta_only_mode",
                        })
                logger.info(
                    "Closed %d Binance dust trades (Delta-only mode)", len(binance_trades),
                )
        except Exception:
            logger.exception("Failed to close Binance trades")

    async def _restore_state(self) -> None:
        """Restore capital, state, and open positions from last saved status.

        Open positions from DB are verified against actual exchange balances.
        Stale positions (no longer on exchange) are marked closed.
        """
        last = await self.db.get_last_bot_status()
        if last:
            # Restore per-exchange balances if available
            binance_bal = last.get("binance_balance")
            delta_bal = last.get("delta_balance")
            if binance_bal is not None or delta_bal is not None:
                self.risk_manager.update_exchange_balances(
                    float(binance_bal) if binance_bal else None,
                    float(delta_bal) if delta_bal else None,
                )
                logger.info(
                    "Restored state from DB -- Binance=$%.2f, Delta=$%.2f, Total=$%.2f",
                    self.risk_manager.binance_capital, self.risk_manager.delta_capital,
                    self.risk_manager.capital,
                )
            else:
                # Fallback to single capital field
                self.risk_manager.capital = last.get("capital", config.trading.starting_capital)
                logger.info("Restored state from DB -- capital: $%.2f (legacy)", self.risk_manager.capital)
        else:
            logger.info("No previous state found -- starting fresh")

        # Restore open positions from DB and verify against exchange balances
        await self._restore_open_positions()

    async def _restore_open_positions(self) -> None:
        """Load open trades from DB and verify they still exist on exchange.

        For each open trade:
        - Spot (Binance): check if we still hold the base asset (> $1 worth)
        - Futures (Delta): check via fetch_positions() for real contract holdings
        If position no longer exists, mark trade as closed in DB.
        If it does exist, register it with the risk manager.

        NOTE: Restored trades are saved in self._restored_trades for later
        injection into strategy instances (see _restore_strategy_state).
        """
        self._restored_trades: list[dict[str, Any]] = []

        open_trades = await self.db.get_all_open_trades()
        if not open_trades:
            logger.info("No open trades to restore from DB")
            return

        logger.info("Found %d open trades in DB — verifying against exchange...", len(open_trades))

        # Fetch exchange balances once (not per-trade)
        binance_balance: dict[str, Any] = {}

        try:
            if self.binance:
                bal = await self.binance.fetch_balance()
                binance_balance = bal.get("free", {})
        except Exception:
            logger.warning("Could not fetch Binance balance for position restore")

        # Fetch Delta positions via fetch_positions() — actual open contracts
        delta_positions: dict[str, dict[str, Any]] = {}
        try:
            if self.delta:
                positions = await self.delta.fetch_positions()
                for pos in positions:
                    contracts = float(pos.get("contracts", 0) or 0)
                    if contracts != 0:
                        symbol = pos.get("symbol", "")
                        side = "long" if contracts > 0 else "short"
                        entry_px = float(pos.get("entryPrice", 0) or 0)
                        delta_positions[symbol] = {
                            "side": side,
                            "contracts": abs(contracts),
                            "entry_price": entry_px,
                            "info": pos,
                        }
                        logger.info(
                            "Found open Delta position: %s %s %.0f contracts @ $%.2f",
                            symbol, side, abs(contracts), entry_px,
                        )
                if not delta_positions:
                    logger.info("No open Delta positions on exchange")
        except Exception as e:
            logger.error("Failed to fetch Delta positions on startup: %s", e)

        restored = 0
        closed = 0

        for trade in open_trades:
            pair = trade.get("pair", "")
            exchange_id = trade.get("exchange", "binance")
            entry_price = float(trade.get("entry_price", 0) or 0)
            amount = float(trade.get("amount", 0) or 0)
            strategy = trade.get("strategy", "")
            position_type = trade.get("position_type", "spot")
            leverage = int(trade.get("leverage", 1) or 1)
            trade_id = trade.get("id")

            # Get the base asset (e.g., "ETH" from "ETH/USDT" or "ETHUSD")
            base = pair.split("/")[0] if "/" in pair else pair.replace("USD", "").replace("USDT", "")

            # Check if position still exists on exchange
            position_exists = False

            if exchange_id == "binance":
                held = float(binance_balance.get(base, 0) or 0)
                # Check if held amount is worth at least $5 (Binance min notional)
                # Below $5 = unsellable dust, mark as closed
                if held > 0 and entry_price > 0:
                    held_value = held * entry_price
                    position_exists = held_value >= 5.0
                    if not position_exists and held_value > 0:
                        logger.info(
                            "Binance position %s is dust ($%.2f < $5 min) — marking closed",
                            pair, held_value,
                        )
                elif held > 0:
                    position_exists = True
            elif exchange_id == "delta":
                # Verify against actual Delta positions from fetch_positions()
                delta_pos = delta_positions.get(pair)
                if delta_pos:
                    position_exists = True
                    # Use exchange data for accuracy (overrides DB if available)
                    if delta_pos["entry_price"] > 0:
                        entry_price = delta_pos["entry_price"]
                    amount = delta_pos["contracts"]
                    position_type = delta_pos["side"]
                    logger.info(
                        "Delta position %s verified: %s %.0f contracts @ $%.2f (exchange data)",
                        pair, position_type, amount, entry_price,
                    )
                else:
                    # Position not found on Delta — it was closed externally
                    logger.info(
                        "Delta position %s NOT found on exchange — was closed externally",
                        pair,
                    )
                    position_exists = False

            if position_exists:
                # Register with risk manager using a synthetic Signal
                from alpha.strategies.base import Signal, StrategyName
                try:
                    strat_name = StrategyName(strategy)
                except ValueError:
                    strat_name = StrategyName.SCALP  # fallback

                side = "buy" if position_type in ("spot", "long") else "sell"
                synthetic_signal = Signal(
                    side=side,
                    price=entry_price,
                    amount=amount,
                    order_type="market",
                    reason="restored from DB",
                    strategy=strat_name,
                    pair=pair,
                    leverage=leverage,
                    position_type=position_type,
                    exchange_id=exchange_id,
                )
                self.risk_manager.record_open(synthetic_signal)
                self._restored_trades.append({
                    "pair": pair,
                    "exchange_id": exchange_id,
                    "entry_price": entry_price,
                    "amount": amount,
                    "position_type": position_type,
                    "leverage": leverage,
                    "strategy": strategy,
                })
                restored += 1
                logger.info(
                    "Restored position: %s %s %s (%.6f @ $%.2f) on %s [%s]",
                    position_type, side, pair, amount, entry_price, exchange_id, strategy,
                )
            else:
                # Position no longer on exchange — mark closed in DB
                if trade_id:
                    await self.db.update_trade(trade_id, {
                        "status": "closed",
                        "closed_at": iso_now(),
                        "reason": "position_not_found_on_restart",
                    })
                closed += 1
                logger.info(
                    "Position %s no longer on %s — marked closed (trade_id=%s)",
                    pair, exchange_id, trade_id,
                )

        # Also check for Delta positions NOT in DB (opened manually or DB out of sync)
        if delta_positions:
            db_delta_pairs = {
                t.get("pair") for t in open_trades if t.get("exchange") == "delta"
            }
            for symbol, dpos in delta_positions.items():
                if symbol not in db_delta_pairs:
                    logger.warning(
                        "Delta position %s %s %.0f contracts exists on exchange "
                        "but NOT in DB — creating DB record",
                        symbol, dpos["side"], dpos["contracts"],
                    )
                    # Create a DB trade record so the bot can manage exit
                    await self.db.log_trade({
                        "pair": symbol,
                        "exchange": "delta",
                        "strategy": "scalp",
                        "side": "buy" if dpos["side"] == "long" else "sell",
                        "entry_price": dpos["entry_price"],
                        "amount": dpos["contracts"],
                        "position_type": dpos["side"],
                        "leverage": config.delta.leverage,
                        "status": "open",
                        "opened_at": iso_now(),
                        "reason": "discovered_on_restart",
                    })
                    self._restored_trades.append({
                        "pair": symbol,
                        "exchange_id": "delta",
                        "entry_price": dpos["entry_price"],
                        "amount": dpos["contracts"],
                        "position_type": dpos["side"],
                        "leverage": config.delta.leverage,
                        "strategy": "scalp",
                    })
                    # Also register with risk manager
                    from alpha.strategies.base import Signal, StrategyName
                    synthetic_signal = Signal(
                        side="buy" if dpos["side"] == "long" else "sell",
                        price=dpos["entry_price"],
                        amount=dpos["contracts"],
                        order_type="market",
                        reason="discovered on exchange",
                        strategy=StrategyName.SCALP,
                        pair=symbol,
                        leverage=config.delta.leverage,
                        position_type=dpos["side"],
                        exchange_id="delta",
                    )
                    self.risk_manager.record_open(synthetic_signal)
                    restored += 1

        logger.info(
            "Position restore complete: %d restored, %d marked closed (of %d DB open)",
            restored, closed, len(open_trades),
        )

    def _restore_strategy_state(self) -> None:
        """Inject restored positions into strategy instances.

        Called AFTER strategies are created but BEFORE they start ticking.
        This tells scalp/futures_momentum strategies about positions that
        were open before the restart, so they manage exits instead of
        opening duplicate positions.
        """
        if not hasattr(self, "_restored_trades") or not self._restored_trades:
            return

        for trade in self._restored_trades:
            pair = trade["pair"]
            exchange_id = trade["exchange_id"]
            entry_price = trade["entry_price"]
            amount = trade["amount"]
            position_type = trade["position_type"]  # "long", "short", or "spot"
            strategy_name = trade.get("strategy", "")

            # Try scalp strategy first (most common)
            scalp = self._scalp_strategies.get(pair)
            if scalp and strategy_name in ("scalp", ""):
                scalp.in_position = True
                scalp.position_side = position_type if position_type in ("long", "short") else "long"
                scalp.entry_price = entry_price
                scalp.entry_amount = amount
                scalp.entry_time = time.monotonic()  # treat as just entered (for timeout)
                scalp.highest_since_entry = entry_price
                scalp.lowest_since_entry = entry_price
                scalp._positions_on_pair = 1
                logger.info(
                    "Injected restored position into ScalpStrategy: "
                    "%s %s %.6f @ $%.2f on %s",
                    pair, scalp.position_side, amount, entry_price, exchange_id,
                )
                continue

            # Try futures_momentum strategy
            if strategy_name == "futures_momentum":
                pair_strats = self._strategies.get(pair, {})
                fm = pair_strats.get(StrategyName.FUTURES_MOMENTUM)
                if fm and hasattr(fm, "position_side"):
                    fm.position_side = position_type
                    fm.entry_price = entry_price
                    fm.entry_amount = amount
                    if position_type == "long":
                        fm.highest_since_entry = entry_price
                    else:
                        fm.lowest_since_entry = entry_price
                    logger.info(
                        "Injected restored position into FuturesMomentumStrategy: "
                        "%s %s %.6f @ $%.2f",
                        pair, position_type, amount, entry_price,
                    )
                    continue

            logger.warning(
                "Could not inject restored position %s (%s) into any strategy",
                pair, strategy_name,
            )

        logger.info(
            "Strategy state restoration complete — %d positions injected",
            len(self._restored_trades),
        )

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
            # Validate credentials are plain strings
            delta_key = str(config.delta.api_key).strip()
            delta_secret = str(config.delta.secret).strip()
            logger.info(
                "Delta credentials: key_len=%d, secret_len=%d, key_type=%s, secret_type=%s",
                len(delta_key), len(delta_secret),
                type(config.delta.api_key).__name__, type(config.delta.secret).__name__,
            )

            delta_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=aiohttp.resolver.ThreadedResolver(), ssl=True,
                )
            )
            self.delta = ccxt.delta({
                "apiKey": delta_key,
                "secret": delta_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
                "session": delta_session,
            })
            # Override to India endpoint — urls['api'] must be a dict with public/private keys
            self.delta.urls["api"] = {
                "public": config.delta.base_url,
                "private": config.delta.base_url,
            }
            logger.info(
                "Delta Exchange India initialized (futures enabled, testnet=%s, leverage=%dx, url=%s)",
                config.delta.testnet, config.delta.leverage, config.delta.base_url,
            )
        else:
            self.delta_pairs = []  # no Delta pairs if no credentials
            logger.info("Delta credentials not set -- futures disabled")

    async def _fetch_portfolio_usd(
        self, exchange: ccxt.Exchange | None,
    ) -> float | None:
        """Fetch total portfolio value in USD including held assets.

        Total = USDT free + value of held BTC + value of held ETH + value of held SOL + ...
        Not just USDT balance — counts all coins worth > $0.50.
        For Delta, converts INR to USD.
        """
        if not exchange:
            return None
        ex_id = getattr(exchange, "id", "?")
        try:
            balance = await exchange.fetch_balance()
            total_map = balance.get("total", {})
            free_map = balance.get("free", {})

            # Log what we see
            holdings = {k: float(v) for k, v in total_map.items()
                        if v is not None and float(v) > 0}
            logger.info("Holdings on %s: %s", ex_id, holdings)

            # Stablecoins counted at face value
            stablecoin_total = 0.0
            for key in ("USDT", "USD", "USDC"):
                val = total_map.get(key)
                if val is not None and float(val) > 0:
                    stablecoin_total += float(val)

            # Value held crypto assets using live ticker prices
            asset_total = 0.0
            asset_details: list[str] = []
            # Only price assets that are tracked pairs
            tracked_bases = set()
            for pair in (config.trading.pairs or []):
                base = pair.split("/")[0] if "/" in pair else pair
                tracked_bases.add(base)

            for asset, qty in holdings.items():
                if asset in ("USDT", "USD", "USDC", "INR"):
                    continue  # stablecoins handled separately
                if asset not in tracked_bases:
                    continue  # skip untracked dust
                qty_f = float(qty)
                if qty_f <= 0:
                    continue
                # Try to get price from exchange
                try:
                    ticker = await exchange.fetch_ticker(f"{asset}/USDT")
                    price = ticker.get("last", 0) or 0
                    if price and price > 0:
                        value = qty_f * price
                        if value > 0.50:  # ignore sub-$0.50 dust
                            asset_total += value
                            asset_details.append(f"{asset}={qty_f:.6f}@${price:.2f}=${value:.2f}")
                except Exception:
                    pass  # skip assets we can't price

            # Delta Exchange India uses INR — convert to USD
            inr_total = 0.0
            inr_val = total_map.get("INR") or free_map.get("INR")
            if inr_val is not None and float(inr_val) > 0:
                inr = float(inr_val)
                inr_total = inr / 85.0  # approximate INR/USD rate

            portfolio_total = stablecoin_total + asset_total + inr_total

            if asset_details:
                logger.info(
                    "Portfolio %s: USDT=$%.2f + assets=$%.2f (%s) + INR=$%.2f = $%.2f",
                    ex_id, stablecoin_total, asset_total,
                    ", ".join(asset_details), inr_total, portfolio_total,
                )
            else:
                logger.info(
                    "Portfolio %s: USDT=$%.2f + INR=$%.2f = $%.2f",
                    ex_id, stablecoin_total, inr_total, portfolio_total,
                )

            return portfolio_total if portfolio_total > 0 else 0.0

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
