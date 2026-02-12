"""Momentum/scalping strategy — RSI + MACD crossover entries with trailing stop."""

from __future__ import annotations

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

    - RSI < 30 → potential long entry
    - RSI > 70 → potential exit (or short if enabled)
    - MACD line crossing above signal line = bullish confirmation
    - Trailing stop-loss at 1.5%
    - Take profit at 2-3%
    - Only one position at a time with small capital
    """

    name = StrategyName.MOMENTUM
    check_interval_sec = config.trading.momentum_check_interval_sec

    def __init__(self, pair: str, executor: TradeExecutor, risk_manager: RiskManager) -> None:
        super().__init__(pair, executor, risk_manager)
        self.in_position = False
        self.entry_price: float = 0.0
        self.highest_since_entry: float = 0.0
        self.trailing_stop_pct: float = 1.5
        self.take_profit_pct: float = 2.5

    async def on_start(self) -> None:
        self.in_position = False
        self.entry_price = 0.0
        self.highest_since_entry = 0.0

    async def check(self) -> list[Signal]:
        signals: list[Signal] = []

        ohlcv = await self.executor.exchange.fetch_ohlcv(
            self.pair, config.trading.candle_timeframe, limit=config.trading.candle_limit
        )
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        current_price = float(close.iloc[-1])

        # Indicators
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_indicator = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_indicator.macd()
        signal_line = macd_indicator.macd_signal()

        rsi_now = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])
        macd_now = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_now = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])

        macd_crossed_up = macd_prev <= signal_prev and macd_now > signal_now

        if self.in_position:
            # Track highest price since entry for trailing stop
            self.highest_since_entry = max(self.highest_since_entry, current_price)
            trailing_stop_price = self.highest_since_entry * (1 - self.trailing_stop_pct / 100)
            take_profit_price = self.entry_price * (1 + self.take_profit_pct / 100)

            # Exit: trailing stop hit
            if current_price <= trailing_stop_price:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._exit_signal(
                    current_price,
                    f"Trailing stop hit (high={self.highest_since_entry:.2f}, "
                    f"stop={trailing_stop_price:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

            # Exit: take profit hit
            elif current_price >= take_profit_price:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._exit_signal(
                    current_price,
                    f"Take profit hit at {current_price:.2f} (target={take_profit_price:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

            # Exit: RSI overbought — take the gain
            elif rsi_now > 70:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._exit_signal(
                    current_price,
                    f"RSI overbought ({rsi_now:.1f}), taking profit (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()

        else:
            # Entry: RSI oversold + MACD bullish crossover
            if rsi_now < 30 and macd_crossed_up:
                capital = config.trading.starting_capital * (config.trading.max_position_pct / 100)
                amount = capital / current_price
                stop_loss = current_price * (1 - config.trading.per_trade_stop_loss_pct / 100)

                signals.append(Signal(
                    side="buy",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"RSI={rsi_now:.1f} (<30) + MACD crossover (bullish entry)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 + self.take_profit_pct / 100),
                ))
                self.in_position = True
                self.entry_price = current_price
                self.highest_since_entry = current_price

            self.logger.debug(
                "No entry — RSI=%.1f, MACD_cross_up=%s, price=%.2f",
                rsi_now, macd_crossed_up, current_price,
            )

        return signals

    # -- Helpers ---------------------------------------------------------------

    def _exit_signal(self, price: float, reason: str) -> Signal:
        capital = config.trading.starting_capital * (config.trading.max_position_pct / 100)
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
