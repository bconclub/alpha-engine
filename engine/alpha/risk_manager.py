"""Risk manager — position sizing, exposure limits, win-rate circuit breakers.

Multi-pair + multi-exchange aware: tracks positions per pair, enforces total
exposure cap (accounting for leverage), and monitors liquidation risk on
futures positions.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from alpha.config import config
from alpha.strategies.base import Signal
from alpha.utils import setup_logger, utcnow

_OPTION_SYMBOL_RE = re.compile(r'\d{6}-\d+-[CP]')

logger = setup_logger("risk_manager")


@dataclass
class Position:
    pair: str
    side: str
    entry_price: float
    amount: float
    strategy: str
    opened_at: str
    exchange: str = "binance"
    leverage: int = 1
    position_type: str = "spot"  # "spot", "long", or "short"


class RiskManager:
    """
    Enforces risk rules before any trade is executed.

    Rules:
    - Max position per trade: check COLLATERAL (margin) against 80% of exchange capital
    - Max 3 concurrent positions across ALL pairs/exchanges (3 per exchange)
    - Max 1 position per pair at a time
    - Total exposure capped at 90% of capital (collateral-based for futures)
    - Per-trade stop-loss: 2%
    - Win-rate circuit breaker: if < 40% over last 20 trades -> pause
    """

    def __init__(self, capital: float | None = None) -> None:
        self.capital = capital or config.trading.starting_capital
        self.max_position_pct = config.trading.max_position_pct
        self.max_total_exposure_pct = config.trading.max_total_exposure_pct
        self.max_concurrent = config.trading.max_concurrent_positions
        self.per_trade_sl_pct = config.trading.per_trade_stop_loss_pct

        # Per-exchange capital: strategies size off their own exchange balance
        self.binance_capital: float = 0.0
        self.delta_capital: float = 0.0

        self.open_positions: list[Position] = []
        self._pair_entry_ts: dict[str, float] = {}  # pair -> last entry approval time
        self.daily_pnl: float = 0.0
        self.daily_pnl_scalp: float = 0.0
        self.daily_pnl_options: float = 0.0
        self.daily_pnl_by_pair: dict[str, float] = {}
        self.trade_results: list[bool] = []  # True=win, False=loss (last N)
        self.is_paused = False
        self._pause_reason: str = ""
        self._force_resumed = False  # bypass win-rate breaker until next win

    def update_exchange_balances(self, binance: float | None, delta: float | None) -> None:
        """Update per-exchange capital from live balance fetches."""
        if binance is not None:
            self.binance_capital = binance
        if delta is not None:
            self.delta_capital = delta
        self.capital = self.binance_capital + self.delta_capital
        logger.info(
            "Balances updated: Binance=$%.2f, Delta=$%.2f, Total=$%.2f",
            self.binance_capital, self.delta_capital, self.capital,
        )

    def get_exchange_capital(self, exchange_id: str) -> float:
        """Return total capital for a specific exchange."""
        if exchange_id == "delta":
            return self.delta_capital
        return self.binance_capital

    def get_available_capital(self, exchange_id: str) -> float:
        """Return available (unlocked) capital for an exchange.

        Available = total balance - cost of open positions on that exchange.
        This prevents over-allocating when positions are already open.
        """
        total = self.get_exchange_capital(exchange_id)
        locked = self._locked_capital(exchange_id)
        available = total - locked
        return max(available, 0.0)

    def _locked_capital(self, exchange_id: str) -> float:
        """Sum of cost locked in open positions for a given exchange."""
        locked = 0.0
        for p in self.open_positions:
            if p.exchange != exchange_id:
                continue
            cost = p.entry_price * p.amount
            if p.position_type in ("long", "short") and p.leverage > 1:
                # Futures: locked capital = collateral (cost / leverage)
                locked += cost / p.leverage
            else:
                # Spot: full cost
                locked += cost
        return locked

    def exchange_position_count(self, exchange_id: str) -> int:
        """Count open positions on a specific exchange."""
        return sum(1 for p in self.open_positions if p.exchange == exchange_id)

    # Per-exchange position limits
    MAX_POSITIONS_PER_EXCHANGE = 3  # match scalp MAX_POSITIONS

    # -- Properties ------------------------------------------------------------

    @property
    def win_rate(self) -> float:
        if not self.trade_results:
            return -1.0  # sentinel: no trades yet (display as "N/A")
        recent = self.trade_results[-20:]
        return (sum(recent) / len(recent)) * 100

    @property
    def has_trades(self) -> bool:
        """True when at least one trade result has been recorded."""
        return len(self.trade_results) > 0

    @property
    def daily_loss_pct(self) -> float:
        if self.capital == 0:
            return 0.0
        return abs(min(self.daily_pnl, 0)) / self.capital * 100

    @property
    def total_exposure(self) -> float:
        """Sum of capital at risk (collateral/margin) across all positions.

        Spot: order value (price * amount).
        Futures: collateral = notional / leverage = (price * amount) / leverage.
        signal.amount for futures is the leveraged contract amount, so we must
        divide by leverage to get actual capital at risk.
        """
        total = 0.0
        for p in self.open_positions:
            order_value = p.entry_price * p.amount
            if p.position_type in ("long", "short") and p.leverage > 1:
                # Futures: amount is leveraged, collateral = notional / leverage
                total += order_value / p.leverage
            else:
                # Spot: full order value
                total += order_value
        return total

    @property
    def total_exposure_pct(self) -> float:
        if self.capital == 0:
            return 0.0
        return (self.total_exposure / self.capital) * 100

    @property
    def spot_exposure(self) -> float:
        """Spot positions only."""
        return sum(
            p.entry_price * p.amount
            for p in self.open_positions if p.position_type == "spot"
        )

    @property
    def futures_exposure(self) -> float:
        """Futures collateral (margin) only — NOT notional.

        signal.amount for futures is the leveraged amount, so
        collateral = (price * amount) / leverage.
        """
        total = 0.0
        for p in self.open_positions:
            if p.position_type in ("long", "short"):
                notional = p.entry_price * p.amount
                total += notional / p.leverage if p.leverage > 1 else notional
        return total

    @property
    def futures_notional(self) -> float:
        """Futures notional value (for display/logging only)."""
        return sum(
            p.entry_price * p.amount * p.leverage
            for p in self.open_positions if p.position_type in ("long", "short")
        )

    def pairs_with_positions(self) -> set[str]:
        """Return the set of pairs that currently have an open position."""
        return {p.pair for p in self.open_positions}

    def has_position(self, pair: str) -> bool:
        """Check if there's already an open position for this pair."""
        return pair in self.pairs_with_positions()

    # -- Signal approval -------------------------------------------------------

    def approve_signal(self, signal: Signal) -> bool:
        """Return True if the signal passes all risk checks.

        EXIT orders (reduce_only or spot sell) are ALWAYS approved — we must
        never block a position close.
        """
        # Determine if this signal opens a new position
        is_opening = (
            (signal.position_type == "spot" and signal.side == "buy")
            or (signal.position_type in ("long", "short") and not signal.reduce_only)
        )
        is_exit = not is_opening

        # EXIT orders always approved — never block a close
        if is_exit:
            trade_value = signal.price * signal.amount
            logger.info(
                "Exit approved (always): %s %s %s %.6f @ $%.2f [%s]",
                signal.position_type, signal.side, signal.pair,
                signal.amount, signal.price, signal.exchange_id,
            )
            return True

        if self.is_paused:
            logger.warning("Bot is paused: %s -- rejecting %s %s", self._pause_reason, signal.side, signal.pair)
            return False

        # Win-rate circuit breaker (skipped after force resume)
        if len(self.trade_results) >= 20 and self.win_rate < 40 and not self._force_resumed:
            self._pause("win rate too low (%.1f%% over last 20 trades)" % self.win_rate)
            return False

        # 3. Max concurrent positions (across ALL pairs/exchanges)
        if len(self.open_positions) >= self.max_concurrent:
            logger.info(
                "Max concurrent positions (%d) reached -- rejecting %s %s",
                self.max_concurrent, signal.pair, signal.position_type,
            )
            return False

        # 3b. Max positions per exchange (2 per exchange)
        ex_count = self.exchange_position_count(signal.exchange_id)
        if ex_count >= self.MAX_POSITIONS_PER_EXCHANGE:
            logger.info(
                "Max positions on %s (%d/%d) reached -- rejecting %s",
                signal.exchange_id, ex_count, self.MAX_POSITIONS_PER_EXCHANGE, signal.pair,
            )
            return False

        # 4. Max 1 position per pair
        if self.has_position(signal.pair):
            logger.info("Already have open position on %s -- rejecting", signal.pair)
            return False

        # 4b. Per-pair entry cooldown — prevent duplicate entries from racing scan cycles
        now = time.monotonic()
        last_entry = self._pair_entry_ts.get(signal.pair, 0.0)
        if now - last_entry < 5.0:
            logger.info(
                "Pair %s entry cooldown (%.1fs ago) -- rejecting duplicate",
                signal.pair, now - last_entry,
            )
            return False

        # 5. Available balance check — don't trade with $0
        is_futures = signal.position_type in ("long", "short") and signal.leverage > 1
        notional = signal.price * signal.amount  # amount is leveraged for futures
        # Collateral = actual capital at risk (margin posted)
        # For futures: signal.amount = (collateral * leverage) / price
        #   so price * amount = collateral * leverage = notional
        #   and collateral = notional / leverage
        # For spot: collateral = notional (no leverage)
        collateral = notional / signal.leverage if is_futures else notional
        available = self.get_available_capital(signal.exchange_id)

        # Minimum balance thresholds
        min_balance = 5.50 if signal.exchange_id == "binance" else 1.00
        if available < min_balance:
            logger.info(
                "Insufficient available %s balance: $%.2f < $%.2f — rejecting %s",
                signal.exchange_id, available, min_balance, signal.pair,
            )
            return False

        # 6. Position size limit — check COLLATERAL against max allowed
        exchange_capital = self.get_exchange_capital(signal.exchange_id)
        if exchange_capital <= 0:
            exchange_capital = self.capital  # fallback to total if not set
        max_value = exchange_capital * (self.max_position_pct / 100)

        if collateral > max_value * 1.05:
            logger.info(
                "%s collateral $%.2f exceeds max $%.2f (%.0f%% of $%.2f %s capital) -- rejecting %s%s",
                "Futures" if is_futures else "Spot",
                collateral, max_value, self.max_position_pct, exchange_capital,
                signal.exchange_id, signal.pair,
                f" (notional=${notional:.2f}, {signal.leverage}x)" if is_futures else "",
            )
            return False

        # 7. Total exposure cap — based on collateral, not notional
        new_exposure = self.total_exposure + collateral
        new_exposure_pct = (new_exposure / self.capital) * 100 if self.capital else 0
        if new_exposure_pct > self.max_total_exposure_pct:
            logger.info(
                "Total exposure would be %.1f%% (cap %.1f%%) -- rejecting %s",
                new_exposure_pct, self.max_total_exposure_pct, signal.pair,
            )
            return False

        notional_str = f" notional=${notional:.2f}" if is_futures else ""
        logger.info(
            "Signal approved: %s %s %s %.6f @ $%.2f (collateral=$%.2f, %dx%s) | "
            "%s avail=$%.2f total=$%.2f | positions=%d (%s:%d), exposure=%.1f%%, daily_pnl=$%.2f",
            signal.position_type, signal.side, signal.pair, signal.amount, signal.price,
            collateral, signal.leverage, notional_str,
            signal.exchange_id, available, exchange_capital,
            len(self.open_positions), signal.exchange_id, ex_count,
            self.total_exposure_pct, self.daily_pnl,
        )
        self._pair_entry_ts[signal.pair] = time.monotonic()
        return True

    # -- Position tracking -----------------------------------------------------

    def record_open(self, signal: Signal) -> None:
        """Track a newly opened position."""
        self.open_positions.append(Position(
            pair=signal.pair,
            side=signal.side,
            entry_price=signal.price,
            amount=signal.amount,
            strategy=signal.strategy.value,
            opened_at=utcnow().isoformat(),
            exchange=signal.exchange_id,
            leverage=signal.leverage,
            position_type=signal.position_type,
        ))

    def record_close(self, pair: str, pnl: float) -> None:
        """Record a closed trade's P&L."""
        self.daily_pnl += pnl
        if _OPTION_SYMBOL_RE.search(pair):
            self.daily_pnl_options += pnl
        else:
            self.daily_pnl_scalp += pnl
        self.daily_pnl_by_pair[pair] = self.daily_pnl_by_pair.get(pair, 0.0) + pnl
        is_win = pnl >= 0
        self.trade_results.append(is_win)
        # Clear force-resume bypass after a winning trade
        if is_win and self._force_resumed:
            self._force_resumed = False
            logger.info("Force-resume bypass cleared after winning trade (win_rate=%.1f%%)", self.win_rate)
        # Remove first matching position for this pair
        new_positions: list[Position] = []
        removed = False
        for p in self.open_positions:
            if p.pair == pair and not removed:
                removed = True
                continue
            new_positions.append(p)
        self.open_positions = new_positions
        self.capital += pnl
        logger.info(
            "Trade closed [%s]: PnL=$%.4f | daily=$%.4f | capital=$%.2f | win_rate=%.1f%%",
            pair, pnl, self.daily_pnl, self.capital, self.win_rate,
        )

    # -- Liquidation monitoring ------------------------------------------------

    def check_liquidation_risk(self, pair: str, current_price: float) -> float | None:
        """Return distance-to-liquidation as a percentage, or None if no futures position.

        For long:  liq_price = entry * (1 - 1/leverage)
        For short: liq_price = entry * (1 + 1/leverage)
        """
        for pos in self.open_positions:
            if pos.pair != pair or pos.leverage <= 1:
                continue
            if pos.position_type == "long":
                liq_price = pos.entry_price * (1 - 1 / pos.leverage)
                distance_pct = ((current_price - liq_price) / current_price) * 100
            elif pos.position_type == "short":
                liq_price = pos.entry_price * (1 + 1 / pos.leverage)
                distance_pct = ((liq_price - current_price) / current_price) * 100
            else:
                continue
            return distance_pct
        return None

    # -- Daily reset -----------------------------------------------------------

    def reset_daily(self) -> None:
        """Called at midnight to reset daily counters."""
        logger.info("Daily reset -- previous daily PnL: $%.4f (scalp=$%.4f, options=$%.4f)",
                     self.daily_pnl, self.daily_pnl_scalp, self.daily_pnl_options)
        self.daily_pnl = 0.0
        self.daily_pnl_scalp = 0.0
        self.daily_pnl_options = 0.0
        self.daily_pnl_by_pair.clear()
        # No daily loss auto-pause — just reset counters
        logger.info("Daily PnL counters reset")

    # -- Pause control ---------------------------------------------------------

    def unpause(self, force: bool = False) -> None:
        """Manually unpause the bot.

        If force=True, also bypass the win-rate circuit breaker so the bot
        doesn't immediately re-pause on the next entry check. The bypass
        auto-clears after a winning trade proves conditions have improved.
        """
        self.is_paused = False
        self._pause_reason = ""
        if force:
            self._force_resumed = True
            logger.info("Bot FORCE resumed — win-rate breaker bypassed until next win")
        else:
            logger.info("Bot manually unpaused")

    def _pause(self, reason: str) -> None:
        self.is_paused = True
        self._pause_reason = reason
        logger.warning("BOT PAUSED: %s", reason)

    # -- Status ----------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            "capital": self.capital,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_scalp": self.daily_pnl_scalp,
            "daily_pnl_options": self.daily_pnl_options,
            "daily_loss_pct": self.daily_loss_pct,
            "open_positions": len(self.open_positions),
            "total_exposure_pct": self.total_exposure_pct,
            "spot_exposure": self.spot_exposure,
            "futures_exposure": self.futures_exposure,       # collateral/margin
            "futures_notional": self.futures_notional,       # leveraged value
            "win_rate": self.win_rate,
            "is_paused": self.is_paused,
            "pause_reason": self._pause_reason,
            "pairs_with_positions": list(self.pairs_with_positions()),
            "binance_available": self.get_available_capital("binance"),
            "delta_available": self.get_available_capital("delta"),
            "binance_positions": self.exchange_position_count("binance"),
            "delta_positions": self.exchange_position_count("delta"),
        }
