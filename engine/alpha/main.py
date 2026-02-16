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
from alpha.price_feed import PriceFeed
from alpha.risk_manager import RiskManager
from alpha.strategies.base import Signal, StrategyName
from alpha.strategies.options_scalp import OptionsScalpStrategy
from alpha.strategies.scalp import ScalpStrategy
from alpha.trade_executor import TradeExecutor, DELTA_CONTRACT_SIZE
from alpha.utils import iso_now, setup_logger

logger = setup_logger("main")


class AlphaBot:
    """Top-level bot orchestrator — runs multiple pairs and exchanges concurrently."""

    def __init__(self) -> None:
        # Core components (initialized in start())
        self.binance: ccxt.Exchange | None = None
        self.kucoin: ccxt.Exchange | None = None
        self.delta: ccxt.Exchange | None = None
        self.delta_options: ccxt.Exchange | None = None  # Delta options exchange
        self.db = Database()
        self.alerts = AlertManager()
        self.risk_manager = RiskManager()
        self.executor: TradeExecutor | None = None
        self.analyzer: MarketAnalyzer | None = None
        self.delta_analyzer: MarketAnalyzer | None = None
        # strategy_selector DISABLED — all pairs use scalp only

        # Multi-pair: Binance spot
        self.pairs: list[str] = config.trading.pairs
        # Delta futures pairs
        self.delta_pairs: list[str] = config.delta.pairs

        # Scalp overlay strategies: pair -> ScalpStrategy (run independently)
        self._scalp_strategies: dict[str, ScalpStrategy] = {}
        # Options overlay strategies: pair -> OptionsScalpStrategy
        self._options_strategies: dict[str, OptionsScalpStrategy] = {}

        # WebSocket price feed for real-time exit checks
        self._price_feed: PriceFeed | None = None

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
        logger.info("  ALPHA v%s — Multi-Exchange Scalping Agent", version)
        logger.info("  BINANCE (spot): %s", ", ".join(self.pairs))
        logger.info("  DELTA (futures): %s, %dx leverage",
                     ", ".join(self.delta_pairs), config.delta.leverage)
        logger.info("  Soul: Momentum is everything. Speed wins. Never idle.")
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

        # Build components — both Binance (spot) and Delta (futures)
        self.executor = TradeExecutor(
            self.binance,  # type: ignore[arg-type]
            db=self.db,
            alerts=self.alerts,
            delta_exchange=self.delta,
            risk_manager=self.risk_manager,
            options_exchange=self.delta_options,
        )

        # Binance analyzer for spot pairs
        if self.binance and self.pairs:
            self.analyzer = MarketAnalyzer(
                self.binance, pair=self.pairs[0],
            )

        if self.delta:
            self.delta_analyzer = MarketAnalyzer(
                self.delta, pair=self.delta_pairs[0] if self.delta_pairs else None,
            )
        # strategy_selector DISABLED — scalp-only, no dynamic strategy switching

        # Load market limits for both exchanges
        await self.executor.load_market_limits(
            self.pairs,  # Binance spot pairs
            delta_pairs=self.delta_pairs if self.delta else None,
        )

        # Register scalp strategies — Delta futures (with 15m trend filter)
        if self.delta:
            for pair in self.delta_pairs:
                self._scalp_strategies[pair] = ScalpStrategy(
                    pair, self.executor, self.risk_manager,
                    exchange=self.delta,
                    is_futures=True,
                    market_analyzer=self.delta_analyzer,
                )

        # Register scalp strategies — Binance spot (same signals, no leverage, long-only)
        if self.binance and self.pairs:
            for pair in self.pairs:
                self._scalp_strategies[pair] = ScalpStrategy(
                    pair, self.executor, self.risk_manager,
                    exchange=self.binance,
                    is_futures=False,
                    market_analyzer=self.analyzer,
                )

        # Register options overlay — Delta only (reads signals from scalp)
        if self.delta and self.delta_options:
            for pair in self.delta_pairs:
                scalp = self._scalp_strategies.get(pair)
                self._options_strategies[pair] = OptionsScalpStrategy(
                    pair, self.executor, self.risk_manager,
                    options_exchange=self.delta_options,
                    futures_exchange=self.delta,
                    scalp_strategy=scalp,
                    market_analyzer=self.delta_analyzer,
                )

        # Inject restored position state into strategy instances
        self._restore_strategy_state()

        # ── Close orphaned positions from removed strategies ─────────────
        # If any open trades exist from non-scalp strategies (e.g. futures_momentum),
        # close them immediately at market to free up margin.
        await self._close_orphaned_positions()

        # ── ORPHAN PROTECTION: close any exchange positions not in bot memory ──
        await self._reconcile_exchange_positions()

        # Start all scalp strategies immediately (they run as parallel overlays)
        for pair, scalp in self._scalp_strategies.items():
            await scalp.start()
        logger.info("Scalp overlay started on %d pairs", len(self._scalp_strategies))

        # Start options strategies (run as parallel overlays alongside scalp)
        for pair, opts in self._options_strategies.items():
            await opts.start()
        if self._options_strategies:
            logger.info("Options overlay started on %d pairs", len(self._options_strategies))

        # Start WebSocket price feed for real-time exit checks
        try:
            binance_ws_exchange = None
            if config.binance.api_key:
                import ccxt.pro as ccxtpro
                binance_ws_exchange = ccxtpro.binance({
                    "apiKey": config.binance.api_key,
                    "secret": config.binance.secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                })

            self._price_feed = PriceFeed(
                strategies=self._scalp_strategies,
                binance_exchange=binance_ws_exchange,
                delta_pairs=self.delta_pairs,
                binance_pairs=self.pairs,
                delta_testnet=config.delta.testnet,
            )
            await self._price_feed.start()
        except Exception:
            logger.exception("PriceFeed failed to start — REST polling continues as fallback")
            self._price_feed = None

        # Schedule periodic tasks
        self._scheduler.add_job(
            self._analysis_cycle, "interval",
            seconds=config.trading.analysis_interval_sec,
        )
        self._scheduler.add_job(self._daily_reset, "cron", hour=18, minute=30)  # midnight IST = 18:30 UTC
        self._scheduler.add_job(self._hourly_report, "cron", minute=0)  # every hour
        self._scheduler.add_job(self._save_status, "interval", minutes=2)
        self._scheduler.add_job(self._reconcile_exchange_positions, "interval", seconds=60)
        self._scheduler.add_job(self._poll_commands, "interval", seconds=10)
        self._scheduler.start()

        # Fetch live exchange balances → per-exchange capital for trade sizing
        binance_bal = await self._fetch_portfolio_usd(self.binance)
        delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None
        self.risk_manager.update_exchange_balances(binance_bal, delta_bal)

        total_capital = self.risk_manager.capital

        # ── Log per-pair affordability for Delta scalp ────────────────────
        if self.delta and delta_bal is not None:
            from alpha.trade_executor import DELTA_CONTRACT_SIZE

            active_pairs: list[str] = []
            skipped_pairs: list[str] = []
            for pair in self.delta_pairs:
                contract_size = DELTA_CONTRACT_SIZE.get(pair, 0)
                if contract_size <= 0:
                    logger.warning("[STARTUP] %s — unknown contract size, may not trade", pair)
                    skipped_pairs.append(pair)
                    continue
                try:
                    ticker = await self.delta.fetch_ticker(pair)
                    price = float(ticker.get("last", 0) or 0)
                except Exception:
                    price = 0
                if price > 0:
                    collateral = (contract_size * price) / config.delta.leverage
                    affordable = delta_bal >= collateral
                    status = "ACTIVE" if affordable else "SKIPPED"
                    logger.info(
                        "[STARTUP] %s %s — 1 contract=$%.2f collateral (%dx), bal=$%.2f",
                        pair, status, collateral, config.delta.leverage, delta_bal,
                    )
                    if affordable:
                        active_pairs.append(pair)
                    else:
                        skipped_pairs.append(pair)
                else:
                    logger.warning("[STARTUP] %s — could not fetch price", pair)
                    active_pairs.append(pair)  # still register, let runtime handle it
            logger.info(
                "[STARTUP] Delta Active: %s | Skipped: %s",
                ", ".join(active_pairs) or "none",
                ", ".join(skipped_pairs) or "none",
            )

        # ── Log Binance spot pair info ──────────────────────────────────
        if self.binance and binance_bal is not None and self.pairs:
            min_trade = 6.0  # MIN_NOTIONAL_SPOT
            can_trade = binance_bal >= min_trade
            logger.info(
                "[STARTUP] Binance: $%.2f USDT | %d pairs | Min trade: $%.0f | %s",
                binance_bal, len(self.pairs), min_trade,
                "ACTIVE" if can_trade else "INSUFFICIENT — need $6+",
            )
            for pair in self.pairs:
                logger.info("[STARTUP] Binance spot: %s (1x, long-only)", pair)

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
            "Bot running — Binance %d spot + Delta %d futures (%dx) — Ctrl+C to stop",
            len(self.pairs), len(self.delta_pairs) if self.delta else 0,
            config.delta.leverage,
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

        # Stop WebSocket price feed first (prevents new exit triggers)
        if self._price_feed:
            await self._price_feed.stop()

        # Stop all active strategies concurrently (scalp + options overlays)
        stop_tasks = []
        for pair, scalp in self._scalp_strategies.items():
            if scalp.is_active:
                stop_tasks.append(scalp.stop())
        for pair, opts in self._options_strategies.items():
            if opts.is_active:
                stop_tasks.append(opts.stop())
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

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
        if self.delta_options:
            await self.delta_options.close()

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

            # 4. Log analysis per pair — ALL pairs use SCALP only (no strategy switching)
            all_analysis_dicts: list[dict[str, Any]] = []

            for analysis in analyses:
                pair = analysis.pair

                # Log to strategy_log DB table (dashboard reads this) — always "scalp"
                try:
                    exchange = "delta" if pair in self.delta_pairs else "binance"
                    if analysis.rsi >= 50:
                        entry_distance_pct = analysis.rsi - 55.0
                    else:
                        entry_distance_pct = 45.0 - analysis.rsi

                    # Grab live signal state from the scalp strategy (1m data)
                    scalp = self._scalp_strategies.get(pair)
                    sig = scalp.last_signal_state if scalp else None
                    sig_count = sig.get("strength", 0) if sig else 0
                    sig_side = sig.get("side") if sig else None  # "long", "short", or None
                    sig_reason = sig.get("reason") or ""
                    # Parse individual signals from reason string like "LONG 3/4: MOM:+0.18% + VOL:1.5x + RSI:35<40"
                    sig_mom = "MOM:" in sig_reason
                    sig_vol = "VOL:" in sig_reason
                    sig_rsi = "RSI:" in sig_reason
                    sig_bb = "BB:" in sig_reason

                    await self.db.log_strategy_selection({
                        "timestamp": iso_now(),
                        "pair": pair,
                        "exchange": exchange,
                        "market_condition": analysis.condition.value,
                        "adx": analysis.adx,
                        "atr": analysis.atr,
                        "bb_width": analysis.bb_width,
                        "bb_upper": analysis.bb_upper,
                        "bb_lower": analysis.bb_lower,
                        "rsi": analysis.rsi,
                        "volume_ratio": analysis.volume_ratio,
                        "signal_strength": analysis.signal_strength,
                        "macd_value": analysis.macd_value,
                        "macd_signal": analysis.macd_signal,
                        "macd_histogram": analysis.macd_histogram,
                        "current_price": analysis.current_price,
                        "price_change_15m": analysis.price_change_pct,
                        "price_change_1h": analysis.price_change_1h,
                        "price_change_24h": analysis.price_change_24h,
                        "entry_distance_pct": entry_distance_pct,
                        "plus_di": analysis.plus_di,
                        "minus_di": analysis.minus_di,
                        "direction": analysis.direction,
                        "strategy_selected": "scalp",
                        "reason": f"[{pair}] Scalp-only mode — all pairs use scalp strategy",
                        # Signal state from 1m scalp strategy (dashboard reads these)
                        "signal_count": sig_count,
                        "signal_side": sig_side,
                        "signal_mom": sig_mom,
                        "signal_vol": sig_vol,
                        "signal_rsi": sig_rsi,
                        "signal_bb": sig_bb,
                    })
                except Exception:
                    logger.debug("Failed to log strategy selection for %s", pair)

                # Collect analysis data for market update (ALL pairs)
                all_analysis_dicts.append({
                    "pair": pair,
                    "condition": analysis.condition.value,
                    "adx": analysis.adx,
                    "rsi": analysis.rsi,
                    "direction": analysis.direction,
                })

            rm = self.risk_manager

            # 4b. Cache latest analysis data for the hourly market update
            self._latest_analyses = all_analysis_dicts

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
        """Monitor futures positions for liquidation proximity.

        At 20x leverage, liquidation is ~5% away. SL at 0.75% triggers long before.
        Warning levels:
          >3%: no warning (normal operation)
          2-3%: INFO log only, no Telegram
          1-2%: Telegram WARNING (once per pair, yellow)
          <1%: Telegram CRITICAL (every 30s, red)

        Skip warning entirely if the position has a SL set that would trigger
        before reaching liquidation.
        """
        if not self.delta:
            return

        # Initialize warning state if needed
        if not hasattr(self, "_liq_warned"):
            self._liq_warned: dict[str, float] = {}  # pair -> last telegram time

        for pair in self.delta_pairs:
            try:
                ticker = await self.delta.fetch_ticker(pair)
                current_price = ticker["last"]
                distance = self.risk_manager.check_liquidation_risk(pair, current_price)
                if distance is None:
                    # No futures position — clear warning state
                    self._liq_warned.pop(pair, None)
                    continue

                # >3%: normal operation, no warning needed
                if distance > 3.0:
                    self._liq_warned.pop(pair, None)
                    continue

                # Find position info
                pos = None
                for p in self.risk_manager.open_positions:
                    if p.pair == pair and p.leverage > 1:
                        pos = p
                        break
                if pos is None:
                    continue

                # Calculate liq price
                if pos.position_type == "long":
                    liq_price = pos.entry_price * (1 - 1 / pos.leverage)
                else:
                    liq_price = pos.entry_price * (1 + 1 / pos.leverage)

                # Skip if SL would trigger before liquidation
                # At 0.50% SL and 5% liquidation, SL always fires first
                sl_distance_pct = 0.50  # our configured SL
                if distance > sl_distance_pct:
                    # SL will trigger before we reach liquidation — safe
                    continue

                now = time.monotonic()

                # 2-3%: INFO log only (once per minute)
                if 2.0 <= distance <= 3.0:
                    logger.info(
                        "[%s] Liquidation distance: %.1f%% (%s %dx) — SL should trigger first",
                        pair, distance, pos.position_type, pos.leverage,
                    )
                    continue

                # 1-2%: Telegram WARNING (once per pair)
                if 1.0 <= distance < 2.0:
                    if pair not in self._liq_warned:
                        self._liq_warned[pair] = now
                        await self.alerts.send_liquidation_warning(
                            pair, distance, pos.position_type, pos.leverage,
                            current_price=current_price, liq_price=liq_price,
                        )
                        logger.warning(
                            "[%s] LIQUIDATION WARNING: %.1f%% from liquidation (%s %dx)",
                            pair, distance, pos.position_type, pos.leverage,
                        )
                    continue

                # <1%: CRITICAL — alert every 30 seconds
                if distance < 1.0:
                    last_alert = self._liq_warned.get(pair, 0)
                    if now - last_alert >= 30:
                        self._liq_warned[pair] = now
                        await self.alerts.send_liquidation_warning(
                            pair, distance, pos.position_type, pos.leverage,
                            current_price=current_price, liq_price=liq_price,
                        )
                        logger.critical(
                            "[%s] CRITICAL LIQUIDATION: %.1f%% from liquidation (%s %dx) — price=$%.2f liq=$%.2f",
                            pair, distance, pos.position_type, pos.leverage,
                            current_price, liq_price,
                        )

            except Exception:
                logger.debug("Could not check liquidation risk for %s", pair)

    # -- Scheduled jobs --------------------------------------------------------

    async def _daily_reset(self) -> None:
        """Midnight reset: send daily summary, reset daily P&L.

        Trade stats are queried from the DATABASE, not in-memory counters.
        This survives bot restarts and is the single source of truth.
        """
        logger.info("Daily reset triggered")
        rm = self.risk_manager

        # Query today's trade stats from DB (survives restarts)
        if self.db is not None:
            today_stats = await self.db.get_today_trade_stats()
            total = today_stats["total_trades"]
            wins = today_stats["wins"]
            losses = today_stats["losses"]
            daily_pnl = today_stats["daily_pnl"]
            win_rate = today_stats["win_rate"]
            pnl_map = today_stats["pnl_by_pair"]
            best_trade = today_stats["best_trade"]
            worst_trade = today_stats["worst_trade"]
        else:
            # Fallback to in-memory (should never happen)
            total = len(rm.trade_results)
            wins = sum(1 for w in rm.trade_results if w)
            losses = total - wins
            daily_pnl = rm.daily_pnl
            win_rate = rm.win_rate
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
            win_rate=win_rate,
            daily_pnl=daily_pnl,
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

            # Build active strategies map (scalp + options overlays)
            active_map: dict[str, str | None] = {}
            for pair in self.all_pairs:
                scalp = self._scalp_strategies.get(pair)
                opts = self._options_strategies.get(pair)
                if scalp and scalp.in_position:
                    side = scalp.position_side or "long"
                    active_map[pair] = f"scalp_{side}"
                elif opts and getattr(opts, "in_position", False):
                    active_map[pair] = "options_scalp"
                elif scalp:
                    active_map[pair] = "scalp"
                else:
                    active_map[pair] = None

            # Fetch live exchange balances (includes held assets)
            binance_bal = await self._fetch_portfolio_usd(self.binance)
            delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None

            # Capital = sum of actual exchange balances
            total_capital = (binance_bal or 0) + (delta_bal or 0)

            # Cross-check positions against exchange: verify we actually hold coins
            verified_positions = await self._verify_positions_against_exchange()

            # Compute unrealized P&L for open positions from scalp strategies
            unrealized_pnl = 0.0
            for pair, scalp in self._scalp_strategies.items():
                if scalp.in_position and scalp.entry_price > 0:
                    # Get latest price from analysis cache
                    analysis = self._latest_analyses.get(pair) if self._latest_analyses else None
                    if analysis and analysis.current_price and analysis.current_price > 0:
                        if scalp.position_side == "short":
                            pnl_pct = (scalp.entry_price - analysis.current_price) / scalp.entry_price * 100
                        else:
                            pnl_pct = (analysis.current_price - scalp.entry_price) / scalp.entry_price * 100
                        # Estimate position value from risk manager
                        for pos in rm.open_positions:
                            if pos.pair == pair:
                                notional = pos.entry_price * pos.amount
                                unrealized_pnl += notional * (pnl_pct / 100)
                                break

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
                unrealized_pnl=unrealized_pnl,
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

        # Build per-pair info (scalp + options overlays)
        active_map: dict[str, str | None] = {}
        active_count = 0
        for pair in self.all_pairs:
            scalp = self._scalp_strategies.get(pair)
            opts = self._options_strategies.get(pair)
            if scalp and scalp.in_position:
                side = scalp.position_side or "long"
                active_map[pair] = f"scalp_{side}"
                active_count += 1
            elif opts and getattr(opts, "in_position", False):
                active_map[pair] = "options_scalp"
                active_count += 1
            elif scalp:
                active_map[pair] = "scalp"
                active_count += 1
            else:
                active_map[pair] = None

        # Use primary pair's analysis for condition
        last = self.analyzer.last_analysis if self.analyzer else None

        # Fetch exchange balances and update per-exchange capital
        binance_bal = await self._fetch_portfolio_usd(self.binance)
        delta_bal = await self._fetch_portfolio_usd(self.delta) if self.delta else None
        rm.update_exchange_balances(binance_bal, delta_bal)

        # Fetch raw INR balance for dashboard display
        delta_balance_inr = None
        if self.delta:
            try:
                bal = await self.delta.fetch_balance()
                inr_val = bal.get("total", {}).get("INR") or bal.get("free", {}).get("INR")
                if inr_val is not None and float(inr_val) > 0:
                    delta_balance_inr = round(float(inr_val), 2)
            except Exception:
                pass

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
            # Exchange data
            "binance_balance": binance_bal,
            "delta_balance": delta_bal,
            "delta_balance_inr": delta_balance_inr,
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
                # Stop all active strategies (scalp + options overlays)
                stop_tasks = []
                for pair, scalp in self._scalp_strategies.items():
                    if scalp.is_active:
                        stop_tasks.append(scalp.stop())
                for pair, opts in self._options_strategies.items():
                    if opts.is_active:
                        stop_tasks.append(opts.stop())
                if stop_tasks:
                    await asyncio.gather(*stop_tasks, return_exceptions=True)
                await self.alerts.send_command_confirmation("pause")
                result_msg = "Bot paused"

            elif command == "resume":
                self.risk_manager.unpause()
                await self._analysis_cycle()  # re-evaluate and start strategies
                # Restart scalp + options overlays
                for pair, scalp in self._scalp_strategies.items():
                    if not scalp.is_active:
                        await scalp.start()
                for pair, opts in self._options_strategies.items():
                    if not opts.is_active:
                        await opts.start()
                await self.alerts.send_command_confirmation("resume")
                result_msg = "Bot resumed"

            elif command == "force_strategy":
                # Only scalp and options_scalp are active — force_strategy is a no-op
                result_msg = "Only scalp and options_scalp strategies are active"
                await self.alerts.send_command_confirmation("force_strategy", result_msg)

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

    async def _close_binance_dust_trades(self) -> None:
        """Mark Binance trades below $6 as closed dust (too small to sell).

        Only closes trades that are genuinely unsellable — checks actual balance.
        """
        try:
            if not self.binance:
                return
            bal = await self.binance.fetch_balance()
            free_map = bal.get("free", {})

            open_trades = await self.db.get_all_open_trades()
            binance_trades = [t for t in open_trades if t.get("exchange") == "binance"]
            dust_count = 0
            for trade in binance_trades:
                pair = trade.get("pair", "")
                base = pair.split("/")[0] if "/" in pair else pair
                held = float(free_map.get(base, 0) or 0)
                entry_price = float(trade.get("entry_price", 0) or 0)
                held_value = held * entry_price if entry_price > 0 else 0
                if held_value < 5.0 and held_value > 0:
                    trade_id = trade.get("id")
                    order_id = trade.get("order_id", "")
                    if trade_id:
                        # Calculate P&L from entry — dust is a small loss
                        try:
                            ticker = await self.binance.fetch_ticker(pair)  # type: ignore[union-attr]
                            current_price = float(ticker.get("last", 0) or 0)
                        except Exception:
                            current_price = entry_price  # fallback: 0 P&L
                        if entry_price > 0 and current_price > 0:
                            pnl = (current_price - entry_price) * held
                            pnl_pct = ((current_price - entry_price) / entry_price) * 100
                        else:
                            pnl = 0.0
                            pnl_pct = 0.0
                        if order_id:
                            await self.db.close_trade(order_id, current_price, pnl, pnl_pct)
                        else:
                            await self.db.update_trade(trade_id, {
                                "status": "closed",
                                "closed_at": iso_now(),
                                "exit_price": current_price,
                                "pnl": round(pnl, 6),
                                "pnl_pct": round(pnl_pct, 4),
                                "reason": "dust_unsellable",
                            })
                        dust_count += 1
                        logger.info(
                            "Dust trade %s: exit=$%.2f pnl=$%.4f (%.2f%%)",
                            pair, current_price, pnl, pnl_pct,
                        )
            if dust_count:
                logger.info("Closed %d Binance dust trades (< $5)", dust_count)
        except Exception:
            logger.exception("Failed to check Binance dust trades")

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
                    "opened_at": trade.get("opened_at"),
                    "peak_pnl": trade.get("peak_pnl"),
                })
                restored += 1
                logger.info(
                    "Restored position: %s %s %s (%.6f @ $%.2f) on %s [%s]",
                    position_type, side, pair, amount, entry_price, exchange_id, strategy,
                )
            else:
                # Position no longer on exchange — find actual exit price
                exit_price = 0.0
                pnl = 0.0
                pnl_pct = 0.0

                # Try to get real exit price from recent trade history
                try:
                    exchange = self.delta if exchange_id == "delta" else self.binance
                    if exchange:
                        # fetch_my_trades returns recent fills for this pair
                        recent_trades = await exchange.fetch_my_trades(pair, limit=20)
                        if recent_trades:
                            # Find the most recent closing trade (opposite side)
                            close_side = "sell" if position_type in ("long", "spot") else "buy"
                            closing_fills = [
                                t for t in recent_trades
                                if t.get("side") == close_side
                            ]
                            if closing_fills:
                                last_fill = closing_fills[-1]  # most recent
                                exit_price = float(last_fill.get("price", 0) or 0)
                                logger.info(
                                    "Found exit fill for %s: $%.2f (from trade history)",
                                    pair, exit_price,
                                )
                        # Fallback: use current ticker price
                        if exit_price <= 0:
                            ticker = await exchange.fetch_ticker(pair)
                            exit_price = float(ticker.get("last", 0) or 0)
                            logger.info(
                                "No exit fill found for %s, using current price: $%.2f",
                                pair, exit_price,
                            )
                except Exception as e:
                    logger.warning(
                        "Could not fetch exit price for %s: %s — using entry as fallback",
                        pair, e,
                    )
                    exit_price = entry_price  # worst case: 0 P&L

                # Calculate P&L
                if entry_price > 0 and exit_price > 0:
                    if position_type in ("long", "spot"):
                        pnl = (exit_price - entry_price) * amount
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                    else:  # short
                        pnl = (entry_price - exit_price) * amount
                        pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                # Close in DB with real data
                order_id = trade.get("order_id", "")
                if order_id:
                    await self.db.close_trade(order_id, exit_price, pnl, pnl_pct)
                elif trade_id:
                    await self.db.update_trade(trade_id, {
                        "status": "closed",
                        "closed_at": iso_now(),
                        "exit_price": exit_price,
                        "pnl": round(pnl, 6),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": "position_not_found_on_restart",
                    })

                closed += 1
                logger.info(
                    "Position %s no longer on %s — closed (exit=$%.2f, pnl=$%.4f, %.2f%%, trade_id=%s)",
                    pair, exchange_id, exit_price, pnl, pnl_pct, trade_id,
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
                        "opened_at": None,  # just discovered, treat as fresh
                        "peak_pnl": None,
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
        This tells scalp strategies about positions that were open before
        the restart, so they manage exits instead of opening duplicates.

        CRITICAL: entry_time is computed from the real opened_at timestamp
        (not time.monotonic()), so timeout/breakeven exits work correctly
        across bot restarts. Without this, timers reset to 0 on every deploy
        and positions can get stuck indefinitely.
        """
        if not hasattr(self, "_restored_trades") or not self._restored_trades:
            return

        injected = 0
        for trade in self._restored_trades:
            pair = trade["pair"]
            exchange_id = trade["exchange_id"]
            entry_price = trade["entry_price"]
            amount = trade["amount"]
            position_type = trade["position_type"]  # "long", "short", or "spot"
            strategy_name = trade.get("strategy", "")

            # Only inject scalp positions (our active strategy)
            scalp = self._scalp_strategies.get(pair)
            if scalp and strategy_name in ("scalp", ""):
                scalp.in_position = True
                scalp.position_side = position_type if position_type in ("long", "short") else "long"
                scalp.entry_price = entry_price
                scalp.entry_amount = amount

                # ── CRITICAL: use real opened_at time, not monotonic now ──
                # This ensures timeout (5min) and breakeven (60s) count from
                # ORIGINAL entry, not from restart. Without this, positions
                # survive forever across deploys because timers keep resetting.
                opened_at_str = trade.get("opened_at")
                if opened_at_str:
                    try:
                        from datetime import datetime, timezone
                        if isinstance(opened_at_str, str):
                            # Parse ISO timestamp: "2026-02-16T04:58:07.123Z"
                            opened_at_str = opened_at_str.replace("Z", "+00:00")
                            opened_dt = datetime.fromisoformat(opened_at_str)
                        else:
                            opened_dt = opened_at_str  # already datetime
                        # Convert to monotonic: how many seconds ago was it opened?
                        seconds_ago = (datetime.now(timezone.utc) - opened_dt).total_seconds()
                        seconds_ago = max(0, seconds_ago)  # don't go negative
                        scalp.entry_time = time.monotonic() - seconds_ago
                        logger.info(
                            "Restored %s entry_time: opened %ds ago (timeout/breakeven preserved)",
                            pair, int(seconds_ago),
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not parse opened_at '%s' for %s: %s — using now",
                            opened_at_str, pair, e,
                        )
                        scalp.entry_time = time.monotonic()
                else:
                    scalp.entry_time = time.monotonic()

                scalp.highest_since_entry = entry_price
                scalp.lowest_since_entry = entry_price

                # Restore peak P&L if available (for decay exit)
                peak_pnl = trade.get("peak_pnl")
                if peak_pnl is not None and peak_pnl > 0:
                    scalp._peak_unrealized_pnl = float(peak_pnl)
                    logger.info("Restored %s peak_pnl: %.2f%%", pair, float(peak_pnl))

                logger.info(
                    "Injected restored position into ScalpStrategy: "
                    "%s %s %.6f @ $%.2f on %s",
                    pair, scalp.position_side, amount, entry_price, exchange_id,
                )
                injected += 1
                continue

            # Non-scalp positions will be closed by _close_orphaned_positions()
            logger.warning(
                "Skipping restore for non-scalp position %s (%s strategy) — will be closed as orphan",
                pair, strategy_name,
            )

        logger.info(
            "Strategy state restoration complete — %d positions injected",
            injected,
        )

    # ==================================================================
    # ORPHAN PROTECTION — reconcile exchange positions every 60s
    # ==================================================================

    async def _reconcile_exchange_positions(self) -> None:
        """Fetch ALL exchange positions and reconcile with bot memory.

        CASE 1: Exchange has position, bot doesn't track it → CLOSE immediately
        CASE 2: Bot thinks it has position, exchange doesn't → Mark closed in DB

        This is the #1 safety net. Runs on startup AND every 60 seconds.
        """
        try:
            await self._reconcile_delta_positions()
        except Exception:
            logger.exception("Orphan reconciliation failed (Delta)")

        try:
            await self._reconcile_binance_positions()
        except Exception:
            logger.exception("Orphan reconciliation failed (Binance)")

    async def _reconcile_delta_positions(self) -> None:
        """Reconcile Delta Exchange positions with bot memory."""
        if not self.delta:
            return

        # Fetch ALL open positions from Delta exchange
        try:
            positions = await self.delta.fetch_positions()
        except Exception:
            logger.debug("Failed to fetch Delta positions for reconciliation")
            return

        # Build map of what exchange has: symbol → (side, contracts, entry_price)
        exchange_positions: dict[str, dict[str, Any]] = {}
        for pos in positions:
            contracts = float(pos.get("contracts", 0) or 0)
            if contracts == 0:
                continue
            symbol = pos.get("symbol", "")
            side = "long" if contracts > 0 else "short"
            entry_px = float(pos.get("entryPrice", 0) or 0)
            exchange_positions[symbol] = {
                "side": side,
                "contracts": abs(contracts),
                "entry_price": entry_px,
            }

        # CASE 1: Exchange has position that bot doesn't track → ORPHAN → CLOSE
        for pair in self.delta_pairs:
            epos = exchange_positions.get(pair)
            scalp = self._scalp_strategies.get(pair)

            if epos and (not scalp or not scalp.in_position):
                # ORPHAN FOUND — exchange has it, bot doesn't
                side = epos["side"]
                contracts = epos["contracts"]
                entry_px = epos["entry_price"]

                logger.warning(
                    "ORPHAN DETECTED: %s %s %.0f contracts @ $%.2f — NOT in bot memory! CLOSING",
                    pair, side, contracts, entry_px,
                )

                # Send Telegram alert
                try:
                    await self.alerts.send_orphan_alert(
                        pair=pair, side=side, contracts=contracts,
                        action="CLOSING AT MARKET",
                        detail=f"Entry: ${entry_px:.2f} — bot has no record of this position",
                    )
                except Exception:
                    pass

                # Close at market
                try:
                    close_side = "sell" if side == "long" else "buy"
                    await self.delta.create_order(
                        pair, "market", close_side, int(contracts),
                        params={"reduce_only": True},
                    )
                    logger.info(
                        "ORPHAN CLOSED: %s %s %.0f contracts at market",
                        pair, side, contracts,
                    )

                    # Mark closed in DB if there's a matching open trade
                    if self.db.is_connected:
                        open_trade = await self.db.get_open_trade(
                            pair=pair, exchange="delta",
                        )
                        if open_trade:
                            try:
                                ticker = await self.delta.fetch_ticker(pair)
                                exit_price = float(ticker.get("last", 0) or 0) or entry_px
                            except Exception:
                                exit_price = entry_px
                            pnl_pct = 0.0
                            pnl = 0.0
                            if entry_px > 0 and exit_price > 0:
                                if side == "long":
                                    pnl_pct = ((exit_price - entry_px) / entry_px) * 100
                                else:
                                    pnl_pct = ((entry_px - exit_price) / entry_px) * 100
                                from alpha.trade_executor import DELTA_CONTRACT_SIZE
                                coin = contracts * DELTA_CONTRACT_SIZE.get(pair, 0.01)
                                pnl = entry_px * coin * (pnl_pct / 100)
                            order_id = open_trade.get("order_id", "")
                            if order_id:
                                await self.db.close_trade(order_id, exit_price, pnl, pnl_pct)
                                logger.info("Orphan DB trade %s closed: P&L=%.2f%%", pair, pnl_pct)

                except Exception:
                    logger.exception("Failed to close orphan %s — MANUAL INTERVENTION NEEDED", pair)
                    try:
                        await self.alerts.send_orphan_alert(
                            pair=pair, side=side, contracts=contracts,
                            action="CLOSE FAILED — MANUAL INTERVENTION NEEDED",
                            detail="Auto-close order failed. Close manually on Delta Exchange!",
                        )
                    except Exception:
                        pass

        # CASE 2: Bot thinks it has position, but exchange doesn't → PHANTOM
        for pair, scalp in self._scalp_strategies.items():
            if not scalp.in_position or not scalp.is_futures:
                continue
            epos = exchange_positions.get(pair)
            if not epos:
                # PHANTOM — bot thinks it's in position, exchange says no
                logger.warning(
                    "PHANTOM DETECTED: %s — bot thinks %s @ $%.2f but exchange has NO position! Clearing.",
                    pair, scalp.position_side, scalp.entry_price,
                )
                try:
                    await self.alerts.send_orphan_alert(
                        pair=pair,
                        side=scalp.position_side or "unknown",
                        contracts=scalp.entry_amount,
                        action="PHANTOM CLEARED (no exchange position)",
                        detail=f"Bot thought {scalp.position_side} @ ${scalp.entry_price:.2f} but exchange has nothing",
                    )
                except Exception:
                    pass

                # Clear bot state
                scalp.in_position = False
                scalp.position_side = None
                scalp.entry_price = 0.0
                scalp.entry_amount = 0.0
                scalp._last_position_exit = time.monotonic()
                ScalpStrategy._live_pnl.pop(pair, None)

                # Mark closed in DB
                if self.db.is_connected:
                    open_trade = await self.db.get_open_trade(
                        pair=pair, exchange="delta", strategy="scalp",
                    )
                    if open_trade:
                        order_id = open_trade.get("order_id", "")
                        if order_id:
                            entry_px = float(open_trade.get("entry_price", 0) or 0)
                            await self.db.close_trade(order_id, entry_px, 0.0, 0.0)
                            logger.info("Phantom trade %s marked closed in DB", pair)

                # Remove from risk manager
                self.risk_manager.record_close(pair, 0.0)

    async def _reconcile_binance_positions(self) -> None:
        """Reconcile Binance spot positions with bot memory.

        Simpler than Delta — just check if we hold a meaningful amount of each asset.
        """
        if not self.binance:
            return

        try:
            balance = await self.binance.fetch_balance()
            free_balances = balance.get("free", {})
        except Exception:
            return

        for pair, scalp in self._scalp_strategies.items():
            if scalp.is_futures:
                continue  # skip Delta pairs
            if not scalp.in_position:
                continue  # bot doesn't think it has a position, skip

            # Check if we actually hold this asset
            base = pair.split("/")[0] if "/" in pair else pair
            held = float(free_balances.get(base, 0) or 0)
            held_value = held * scalp.entry_price if scalp.entry_price > 0 else 0

            if held_value < 3.0:
                # PHANTOM — bot thinks position exists but nothing on exchange
                logger.warning(
                    "PHANTOM (Binance): %s — bot thinks long @ $%.2f but only $%.2f held. Clearing.",
                    pair, scalp.entry_price, held_value,
                )
                try:
                    await self.alerts.send_orphan_alert(
                        pair=pair, side="long", contracts=scalp.entry_amount,
                        action="PHANTOM CLEARED (insufficient balance)",
                        detail=f"Only ${held_value:.2f} held — position was closed externally",
                    )
                except Exception:
                    pass

                scalp.in_position = False
                scalp.position_side = None
                scalp.entry_price = 0.0
                scalp.entry_amount = 0.0
                scalp._last_position_exit = time.monotonic()

                if self.db.is_connected:
                    open_trade = await self.db.get_open_trade(
                        pair=pair, exchange="binance", strategy="scalp",
                    )
                    if open_trade:
                        order_id = open_trade.get("order_id", "")
                        if order_id:
                            await self.db.close_trade(order_id, 0.0, 0.0, 0.0)
                self.risk_manager.record_close(pair, 0.0)

    async def _close_orphaned_positions(self) -> None:
        """Close any open positions from non-scalp strategies (e.g. futures_momentum).

        Called on startup to free up margin tied by strategies that have been removed.
        Sends a market sell/buy to close the position, then marks the DB trade as closed.
        """
        if not self.db.is_connected:
            return

        open_trades = await self.db.get_all_open_trades()
        if not open_trades:
            return

        # Only close non-scalp, non-options_scalp positions
        allowed_strategies = {"scalp", "options_scalp", ""}
        orphans = [
            t for t in open_trades
            if t.get("strategy", "") not in allowed_strategies
        ]

        if not orphans:
            return

        logger.warning(
            "Found %d orphaned position(s) from removed strategies — closing at market",
            len(orphans),
        )

        for trade in orphans:
            pair = trade["pair"]
            exchange_id = trade.get("exchange", "delta")
            position_type = trade.get("position_type", "long")
            amount = trade.get("amount", 0)
            entry_price = trade.get("entry_price", 0)
            order_id = trade.get("order_id", "")
            strategy_name = trade.get("strategy", "unknown")

            logger.info(
                "Closing orphaned %s position: %s %s %.6f @ $%.2f (strategy=%s)",
                strategy_name, pair, position_type, amount, entry_price, strategy_name,
            )

            try:
                # Determine close side
                close_side = "sell" if position_type == "long" else "buy"

                # Get current price for P&L calc
                exchange = self.delta if exchange_id == "delta" else self.binance
                if exchange:
                    ticker = await exchange.fetch_ticker(pair)
                    current_price = float(ticker.get("last", 0) or 0)
                else:
                    current_price = entry_price

                # For Delta: convert to contracts
                if exchange_id == "delta":
                    contract_size = DELTA_CONTRACT_SIZE.get(pair, 0.01)
                    contracts = max(1, int(amount / contract_size))

                    await exchange.create_order(  # type: ignore[union-attr]
                        pair, "market", close_side, contracts,
                        params={"reduce_only": True},
                    )
                    logger.info(
                        "Closed orphaned position: %s %s %d contracts at market",
                        pair, close_side, contracts,
                    )
                else:
                    # Binance spot — sell the amount
                    if exchange:
                        await exchange.create_order(  # type: ignore[union-attr]
                            pair, "market", close_side, amount,
                        )

                # Calculate P&L
                if entry_price > 0 and current_price > 0:
                    if position_type == "long":
                        pnl = (current_price - entry_price) * amount
                        pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    else:
                        pnl = (entry_price - current_price) * amount
                        pnl_pct = ((entry_price - current_price) / entry_price) * 100
                else:
                    pnl = 0.0
                    pnl_pct = 0.0

                # Close in DB
                if order_id:
                    await self.db.close_trade(order_id, current_price, pnl, pnl_pct)

                # Send alert
                await self.alerts.send_text(
                    f"🧹 Closed orphaned {strategy_name} position\n"
                    f"{pair} {position_type.upper()} @ ${entry_price:.2f}\n"
                    f"Exit: ${current_price:.2f} | P&L: ${pnl:+.4f} ({pnl_pct:+.2f}%)\n"
                    f"Reason: Strategy removed — freeing margin"
                )

            except Exception:
                logger.exception("Failed to close orphaned position %s", pair)
                # Try to at least mark it in DB — use current price if possible
                try:
                    if order_id:
                        # Try to get current price for accurate P&L
                        fallback_exit = entry_price
                        fallback_pnl = 0.0
                        fallback_pnl_pct = 0.0
                        try:
                            exchange = self.delta if exchange_id == "delta" else self.binance
                            if exchange:
                                ticker = await exchange.fetch_ticker(pair)
                                fallback_exit = float(ticker.get("last", 0) or 0) or entry_price
                                if entry_price > 0 and fallback_exit > 0:
                                    if position_type == "long":
                                        fallback_pnl = (fallback_exit - entry_price) * amount
                                        fallback_pnl_pct = ((fallback_exit - entry_price) / entry_price) * 100
                                    else:
                                        fallback_pnl = (entry_price - fallback_exit) * amount
                                        fallback_pnl_pct = ((entry_price - fallback_exit) / entry_price) * 100
                        except Exception:
                            pass  # keep fallback_exit = entry_price, pnl = 0
                        await self.db.close_trade(
                            order_id, fallback_exit, fallback_pnl, fallback_pnl_pct,
                        )
                        logger.info(
                            "Orphan fallback close %s: exit=$%.2f pnl=$%.4f (%.2f%%)",
                            pair, fallback_exit, fallback_pnl, fallback_pnl_pct,
                        )
                except Exception:
                    pass

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
            # ── LEVERAGE SAFETY CHECK ─────────────────────────────────────
            if config.delta.leverage > 20:
                logger.warning(
                    "!!! LEVERAGE IS %dx — max supported is 20x !!! "
                    "Set DELTA_LEVERAGE=20 in .env",
                    config.delta.leverage,
                )
            logger.info(
                "Delta Exchange India initialized (futures enabled, testnet=%s, leverage=%dx, url=%s)",
                config.delta.testnet, config.delta.leverage, config.delta.base_url,
            )

            # Delta Options — separate ccxt instance with defaultType=option
            delta_options_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=aiohttp.resolver.ThreadedResolver(), ssl=True,
                )
            )
            self.delta_options = ccxt.delta({
                "apiKey": delta_key,
                "secret": delta_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "option"},
                "session": delta_options_session,
            })
            self.delta_options.urls["api"] = {
                "public": config.delta.base_url,
                "private": config.delta.base_url,
            }
            logger.info("Delta Exchange India options initialized")
        else:
            self.delta_pairs = []  # no Delta pairs if no credentials
            logger.info("Delta credentials not set -- futures disabled")

    async def _fetch_portfolio_usd(
        self, exchange: ccxt.Exchange | None,
    ) -> float | None:
        """Fetch total portfolio value in USD including held assets.

        For Binance: USDT free + value of held crypto assets.
        For Delta: wallet balance + unrealized P&L from open positions.
        """
        if not exchange:
            return None
        ex_id = getattr(exchange, "id", "?")
        try:
            balance = await exchange.fetch_balance()
            total_map = balance.get("total", {})
            free_map = balance.get("free", {})

            # Log raw balance data for debugging
            holdings = {k: float(v) for k, v in total_map.items()
                        if v is not None and float(v) > 0}
            free_holdings = {k: float(v) for k, v in free_map.items()
                            if v is not None and float(v) > 0}
            logger.info("Holdings on %s: total=%s free=%s", ex_id, holdings, free_holdings)

            # Also log the info dict if available (contains exchange-specific fields)
            info = balance.get("info")
            if info and isinstance(info, dict):
                # Log key fields for Delta (wallet_balance, equity, margin_balance, etc.)
                for key in ("wallet_balance", "equity", "available_balance",
                            "margin_balance", "unrealized_pnl", "balance", "result"):
                    if key in info:
                        logger.info("  %s.info.%s = %s", ex_id, key, info[key])
                # Delta may nest under 'result' key
                result = info.get("result") if isinstance(info.get("result"), dict) else None
                if result:
                    for key in ("balance", "available_balance", "portfolio_margin",
                                "commission", "unrealized_pnl"):
                        if key in result:
                            logger.info("  %s.info.result.%s = %s", ex_id, key, result[key])

            # ── Stablecoins at face value ──────────────────────────────────
            stablecoin_total = 0.0
            for key in ("USDT", "USD", "USDC"):
                val = total_map.get(key)
                if val is not None and float(val) > 0:
                    stablecoin_total += float(val)

            # ── Value held crypto assets using live ticker prices ──────────
            asset_total = 0.0
            asset_details: list[str] = []
            tracked_bases = set()
            for pair in (config.trading.pairs or []):
                base = pair.split("/")[0] if "/" in pair else pair
                tracked_bases.add(base)

            for asset, qty in holdings.items():
                if asset in ("USDT", "USD", "USDC", "INR"):
                    continue
                if asset not in tracked_bases:
                    continue
                qty_f = float(qty)
                if qty_f <= 0:
                    continue
                try:
                    ticker = await exchange.fetch_ticker(f"{asset}/USDT")
                    price = ticker.get("last", 0) or 0
                    if price and price > 0:
                        value = qty_f * price
                        if value > 0.50:
                            asset_total += value
                            asset_details.append(f"{asset}={qty_f:.6f}@${price:.2f}=${value:.2f}")
                except Exception:
                    pass

            # ── Delta Exchange India: INR → USD conversion ─────────────────
            inr_total = 0.0
            inr_raw = 0.0
            inr_val = total_map.get("INR") or free_map.get("INR")
            if inr_val is not None and float(inr_val) > 0:
                inr_raw = float(inr_val)
                # Try to get live INR/USD rate from Binance
                inr_rate = await self._get_inr_usd_rate()
                inr_total = inr_raw / inr_rate

            # ── Delta: add unrealized P&L from open futures positions ──────
            unrealized_pnl_usd = 0.0
            if ex_id == "delta" and exchange:
                try:
                    positions = await exchange.fetch_positions()
                    for pos in positions:
                        contracts = float(pos.get("contracts", 0) or 0)
                        if contracts == 0:
                            continue
                        # ccxt normalizes unrealizedPnl
                        upnl = float(pos.get("unrealizedPnl", 0) or 0)
                        if upnl != 0:
                            unrealized_pnl_usd += upnl
                    if unrealized_pnl_usd != 0:
                        logger.info("  Delta unrealized P&L: $%.4f", unrealized_pnl_usd)
                except Exception as e:
                    logger.debug("Could not fetch Delta positions for P&L: %s", e)

            portfolio_total = stablecoin_total + asset_total + inr_total + unrealized_pnl_usd

            if inr_raw > 0:
                logger.info(
                    "Portfolio %s: USDT=$%.2f + assets=$%.2f%s + INR=₹%.2f ($%.2f) + uPnL=$%.4f = $%.2f",
                    ex_id, stablecoin_total, asset_total,
                    f" ({', '.join(asset_details)})" if asset_details else "",
                    inr_raw, inr_total, unrealized_pnl_usd, portfolio_total,
                )
            else:
                logger.info(
                    "Portfolio %s: USDT=$%.2f + assets=$%.2f%s + uPnL=$%.4f = $%.2f",
                    ex_id, stablecoin_total, asset_total,
                    f" ({', '.join(asset_details)})" if asset_details else "",
                    unrealized_pnl_usd, portfolio_total,
                )

            return portfolio_total if portfolio_total > 0 else 0.0

        except Exception as e:
            logger.warning("Could not fetch balance from %s: %s (type: %s)", ex_id, e, type(e).__name__)
            return None

    async def _get_inr_usd_rate(self) -> float:
        """Get current INR/USD exchange rate. Uses cached value, refreshed every hour."""
        now = time.monotonic()
        if hasattr(self, "_inr_rate") and hasattr(self, "_inr_rate_time"):
            if now - self._inr_rate_time < 3600:  # cache for 1 hour
                return self._inr_rate

        # Try fetching from Binance (USDT/INR pair if available)
        rate = 86.5  # fallback default
        try:
            if self.binance:
                # Binance doesn't have USDT/INR directly.
                # Use a well-known approximate rate; update periodically.
                # The user can set DELTA_INR_USD_RATE in env for precision.
                env_rate = config.delta.__dict__.get("inr_usd_rate")
                if env_rate and float(env_rate) > 0:
                    rate = float(env_rate)
                else:
                    rate = 86.5  # current approximate rate as of Feb 2026
        except Exception:
            pass

        self._inr_rate = rate
        self._inr_rate_time = now
        logger.debug("INR/USD rate: %.2f", rate)
        return rate


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
