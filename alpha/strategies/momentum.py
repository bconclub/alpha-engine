"""Momentum/scalping strategy — RSI + MACD crossover entries with trailing stop.

Runs an active check loop every 60 seconds. Logs every tick so you can see
it working. Heartbeat log every 5 minutes.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pandas as pd
import ta

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor


class MomentumStrategy(BaseStrategy):
    """
    Enters on RSI oversold + MACD crossover confirmation.

    - RSI < 35 -> potential long entry (widened from 30 for more signals)
    - RSI > 70 -> potential exit
    - MACD line crossing above signal line = bullish confirmation
    - Trailing stop-loss at 1.5%
    - Take profit at 2.5%
    - Only one position at a time with small capital
    """

    name = StrategyName.MOMENTUM
    check_interval_sec = 60  # check every 60 seconds

    # Entry / exit thresholds
    RSI_ENTRY = 35       # widened from 30 — triggers more often
    RSI_EXIT = 70
    TRAILING_STOP_PCT = 1.5
    TAKE_PROFIT_PCT = 2.5

    def __init__(self, pair: str, executor: TradeExecutor, risk_manager: RiskManager) -> None:
        super().__init__(pair, executor, risk_manager)
        self.in_position = False
        self.entry_price: float = 0.0
        self.highest_since_entry: float = 0.0
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0

    async def on_start(self) -> None:
        self.in_position = False
        self.entry_price = 0.0
        self.highest_since_entry = 0.0
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        self.logger.info(
            "[%s] Momentum strategy ACTIVE — checking every %ds, RSI entry < %d",
            self.pair, self.check_interval_sec, self.RSI_ENTRY,
        )

    async def check(self) -> list[Signal]:
        signals: list[Signal] = []
        self._tick_count += 1

        ohlcv = await self.executor.exchange.fetch_ohlcv(
            self.pair, config.trading.candle_timeframe, limit=config.trading.candle_limit,
        )
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        current_price = float(close.iloc[-1])

        # Indicators
        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_indicator = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_indicator.macd()
        signal_line = macd_indicator.macd_signal()

        rsi_now = float(rsi_series.iloc[-1])
        macd_now = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_now = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])

        macd_crossed_up = macd_prev <= signal_prev and macd_now > signal_now

        # ── Heartbeat every 5 minutes ────────────────────────────────────────
        now = time.monotonic()
        if now - self._last_heartbeat >= 300:
            self._last_heartbeat = now
            if self.in_position:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                self.logger.info(
                    "[%s] Heartbeat — momentum active, IN POSITION (entry=$%.2f, now=$%.2f, PnL=%+.2f%%)",
                    self.pair, self.entry_price, current_price, pnl_pct,
                )
            else:
                self.logger.info(
                    "[%s] Heartbeat — momentum active, watching for RSI < %d (currently %.1f)",
                    self.pair, self.RSI_ENTRY, rsi_now,
                )

        # ── In position: check exit ──────────────────────────────────────────
        if self.in_position:
            self.highest_since_entry = max(self.highest_since_entry, current_price)
            trailing_stop_price = self.highest_since_entry * (1 - self.TRAILING_STOP_PCT / 100)
            take_profit_price = self.entry_price * (1 + self.TAKE_PROFIT_PCT / 100)
            pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100

            self.logger.info(
                "[%s] Tick #%d — IN POSITION | price=$%.2f | entry=$%.2f | PnL=%+.2f%% | "
                "TP=$%.2f | SL=$%.2f (trailing from high=$%.2f)",
                self.pair, self._tick_count, current_price, self.entry_price, pnl_pct,
                take_profit_price, trailing_stop_price, self.highest_since_entry,
            )

            # Exit: trailing stop hit
            if current_price <= trailing_stop_price:
                signals.append(self._exit_signal(
                    current_price,
                    f"Trailing stop hit (high={self.highest_since_entry:.2f}, "
                    f"stop={trailing_stop_price:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

            # Exit: take profit hit
            elif current_price >= take_profit_price:
                signals.append(self._exit_signal(
                    current_price,
                    f"Take profit hit at {current_price:.2f} (target={take_profit_price:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

            # Exit: RSI overbought — take the gain
            elif rsi_now > self.RSI_EXIT:
                signals.append(self._exit_signal(
                    current_price,
                    f"RSI overbought ({rsi_now:.1f}), taking profit (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

        # ── No position: check entry ─────────────────────────────────────────
        else:
            # Log every tick with current indicator values
            self.logger.info(
                "[%s] Tick #%d — NO POSITION | price=$%.2f | RSI=%.1f (need <%d) | "
                "MACD=%.4f vs Signal=%.4f | Cross up=%s",
                self.pair, self._tick_count, current_price,
                rsi_now, self.RSI_ENTRY,
                macd_now, signal_now, macd_crossed_up,
            )

            # Entry: RSI oversold + MACD bullish crossover
            if rsi_now < self.RSI_ENTRY and macd_crossed_up:
                capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
                amount = capital / current_price
                stop_loss = current_price * (1 - config.trading.per_trade_stop_loss_pct / 100)

                self.logger.info(
                    "[%s] ENTRY SIGNAL — RSI=%.1f + MACD crossover! Opening BUY $%.2f",
                    self.pair, rsi_now, capital,
                )

                signals.append(Signal(
                    side="buy",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"RSI={rsi_now:.1f} (<{self.RSI_ENTRY}) + MACD crossover (bullish entry)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 + self.TAKE_PROFIT_PCT / 100),
                ))
                self.in_position = True
                self.entry_price = current_price
                self.highest_since_entry = current_price

        return signals

    # -- Helpers ---------------------------------------------------------------

    def _exit_signal(self, price: float, reason: str) -> Signal:
        capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
        amount = capital / price
        return Signal(
            side="sell",
            price=price,
            amount=amount,
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.pair,
        )

    def _reset_position(self) -> None:
        self.in_position = False
        self.entry_price = 0.0
        self.highest_since_entry = 0.0
