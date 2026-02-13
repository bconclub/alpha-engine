"""Futures momentum strategy — bidirectional RSI + MACD on Delta Exchange.

Supports both LONG and SHORT positions with configurable leverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt
import pandas as pd
import ta

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor


class FuturesMomentumStrategy(BaseStrategy):
    """
    Bidirectional momentum on futures:

    - RSI < 30 + MACD cross up  -> LONG (buy futures)
    - RSI > 70 + MACD cross down -> SHORT (sell futures)
    - Take profit: 2.5%
    - Stop loss: 1.5%
    - Leverage configurable (default 5x, capped at 10x for safety)
    """

    name = StrategyName.FUTURES_MOMENTUM
    check_interval_sec = config.trading.futures_check_interval_sec

    def __init__(
        self,
        pair: str,
        executor: TradeExecutor,
        risk_manager: RiskManager,
        exchange: Any = None,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.delta_exchange: ccxt.Exchange | None = exchange
        self.leverage: int = min(config.delta.leverage, 10)  # hard cap at 10x

        # Position tracking
        self.position_side: str | None = None  # "long" or "short" or None
        self.entry_price: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")

        # Thresholds
        self.take_profit_pct: float = 2.5
        self.stop_loss_pct: float = 1.5

    async def on_start(self) -> None:
        self.position_side = None
        self.entry_price = 0.0
        self.highest_since_entry = 0.0
        self.lowest_since_entry = float("inf")

    async def check(self) -> list[Signal]:
        signals: list[Signal] = []
        exchange = self.delta_exchange or self.executor.exchange

        ohlcv = await exchange.fetch_ohlcv(
            self.pair, config.trading.candle_timeframe, limit=config.trading.candle_limit,
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
        macd_now = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_now = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])

        macd_crossed_up = macd_prev <= signal_prev and macd_now > signal_now
        macd_crossed_down = macd_prev >= signal_prev and macd_now < signal_now

        # ── In position: check exit conditions ──────────────────────────────

        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)
            trailing_stop = self.highest_since_entry * (1 - self.stop_loss_pct / 100)
            take_profit_price = self.entry_price * (1 + self.take_profit_pct / 100)

            if current_price <= trailing_stop:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG trailing stop hit (high={self.highest_since_entry:.2f}, "
                    f"stop={trailing_stop:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif current_price >= take_profit_price:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG take profit hit at {current_price:.2f} (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif rsi_now > 70:
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG exit — RSI overbought ({rsi_now:.1f}), PnL={pnl_pct:+.2f}%",
                ))
                self._reset_position()

        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)
            trailing_stop = self.lowest_since_entry * (1 + self.stop_loss_pct / 100)
            take_profit_price = self.entry_price * (1 - self.take_profit_pct / 100)

            if current_price >= trailing_stop:
                pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT trailing stop hit (low={self.lowest_since_entry:.2f}, "
                    f"stop={trailing_stop:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif current_price <= take_profit_price:
                pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT take profit hit at {current_price:.2f} (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif rsi_now < 30:
                pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT exit — RSI oversold ({rsi_now:.1f}), PnL={pnl_pct:+.2f}%",
                ))
                self._reset_position()

        # ── No position: check entry conditions ─────────────────────────────

        else:
            capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
            amount = (capital / current_price) * self.leverage

            # LONG entry: RSI oversold + MACD bullish crossover
            if rsi_now < 30 and macd_crossed_up:
                stop_loss = current_price * (1 - self.stop_loss_pct / 100)
                signals.append(Signal(
                    side="buy",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"LONG: RSI={rsi_now:.1f} (<30) + MACD crossover (bullish)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 + self.take_profit_pct / 100),
                    leverage=self.leverage,
                    position_type="long",
                    exchange_id="delta",
                ))
                self.position_side = "long"
                self.entry_price = current_price
                self.highest_since_entry = current_price

            # SHORT entry: RSI overbought + MACD bearish crossover
            elif rsi_now > 70 and macd_crossed_down and config.delta.enable_shorting:
                stop_loss = current_price * (1 + self.stop_loss_pct / 100)
                signals.append(Signal(
                    side="sell",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"SHORT: RSI={rsi_now:.1f} (>70) + MACD crossover (bearish)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 - self.take_profit_pct / 100),
                    leverage=self.leverage,
                    position_type="short",
                    exchange_id="delta",
                ))
                self.position_side = "short"
                self.entry_price = current_price
                self.lowest_since_entry = current_price

            else:
                self.logger.debug(
                    "No entry — RSI=%.1f, MACD_cross_up=%s, MACD_cross_down=%s, price=%.2f",
                    rsi_now, macd_crossed_up, macd_crossed_down, current_price,
                )

        return signals

    # -- Helpers ---------------------------------------------------------------

    def _close_long_signal(self, price: float, reason: str) -> Signal:
        """Generate a signal to close a long position (sell to close)."""
        capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
        amount = (capital / price) * self.leverage
        return Signal(
            side="sell",
            price=price,
            amount=amount,
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.pair,
            leverage=self.leverage,
            position_type="long",
            reduce_only=True,
            exchange_id="delta",
        )

    def _close_short_signal(self, price: float, reason: str) -> Signal:
        """Generate a signal to close a short position (buy to close)."""
        capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
        amount = (capital / price) * self.leverage
        return Signal(
            side="buy",
            price=price,
            amount=amount,
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.pair,
            leverage=self.leverage,
            position_type="short",
            reduce_only=True,
            exchange_id="delta",
        )

    def _reset_position(self) -> None:
        self.position_side = None
        self.entry_price = 0.0
        self.highest_since_entry = 0.0
        self.lowest_since_entry = float("inf")
