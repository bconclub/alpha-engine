"""Futures momentum strategy — bidirectional RSI + MACD on Delta Exchange.

Supports both LONG and SHORT positions with configurable leverage.
Runs an active check loop every 60 seconds with full tick logging
and 5-minute heartbeat.
"""

from __future__ import annotations

import time
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

    - RSI < 35 + MACD cross up  -> LONG  (widened from 30)
    - RSI > 65 + MACD cross down -> SHORT (widened from 70)
    - Take profit: 2.5%
    - Stop loss: 1.5% (trailing)
    - Leverage configurable (default 5x, capped at 10x for safety)
    """

    name = StrategyName.FUTURES_MOMENTUM
    check_interval_sec = 60  # check every 60 seconds

    # Entry / exit thresholds (widened for more signals)
    RSI_LONG_ENTRY = 35     # widened from 30
    RSI_SHORT_ENTRY = 65    # widened from 70
    RSI_LONG_EXIT = 70      # exit long when overbought
    RSI_SHORT_EXIT = 30     # exit short when oversold
    TRAILING_STOP_PCT = 1.5
    TAKE_PROFIT_PCT = 2.5

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

        # Tick tracking
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0

    async def on_start(self) -> None:
        self.position_side = None
        self.entry_price = 0.0
        self.highest_since_entry = 0.0
        self.lowest_since_entry = float("inf")
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        self.logger.info(
            "[%s] Futures momentum ACTIVE — %dx leverage, checking every %ds, "
            "LONG RSI < %d, SHORT RSI > %d",
            self.pair, self.leverage, self.check_interval_sec,
            self.RSI_LONG_ENTRY, self.RSI_SHORT_ENTRY,
        )

    async def check(self) -> list[Signal]:
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.delta_exchange or self.executor.exchange

        ohlcv = await exchange.fetch_ohlcv(
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
        macd_crossed_down = macd_prev >= signal_prev and macd_now < signal_now

        # ── Heartbeat every 5 minutes ────────────────────────────────────────
        now = time.monotonic()
        if now - self._last_heartbeat >= 300:
            self._last_heartbeat = now
            if self.position_side:
                if self.position_side == "long":
                    pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                else:
                    pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                self.logger.info(
                    "[%s] Heartbeat — futures momentum active, %s %dx (entry=$%.2f, now=$%.2f, PnL=%+.2f%%)",
                    self.pair, self.position_side.upper(), self.leverage,
                    self.entry_price, current_price, pnl_pct,
                )
            else:
                # Show which condition is closest to triggering
                long_dist = rsi_now - self.RSI_LONG_ENTRY
                short_dist = self.RSI_SHORT_ENTRY - rsi_now
                closest = f"LONG in {long_dist:.0f} RSI pts" if long_dist < short_dist else f"SHORT in {short_dist:.0f} RSI pts"
                self.logger.info(
                    "[%s] Heartbeat — futures momentum active, no position, RSI=%.1f, closest: %s",
                    self.pair, rsi_now, closest,
                )

        # ── In LONG position: check exit ─────────────────────────────────────
        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)
            trailing_stop = self.highest_since_entry * (1 - self.TRAILING_STOP_PCT / 100)
            take_profit_price = self.entry_price * (1 + self.TAKE_PROFIT_PCT / 100)
            pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100

            self.logger.info(
                "[%s] Tick #%d — LONG %dx | price=$%.2f | entry=$%.2f | PnL=%+.2f%% | "
                "TP=$%.2f | SL=$%.2f (trail from $%.2f)",
                self.pair, self._tick_count, self.leverage, current_price,
                self.entry_price, pnl_pct,
                take_profit_price, trailing_stop, self.highest_since_entry,
            )

            if current_price <= trailing_stop:
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG trailing stop hit (high={self.highest_since_entry:.2f}, "
                    f"stop={trailing_stop:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif current_price >= take_profit_price:
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG take profit hit at {current_price:.2f} (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif rsi_now > self.RSI_LONG_EXIT:
                signals.append(self._close_long_signal(
                    current_price,
                    f"LONG exit — RSI overbought ({rsi_now:.1f}), PnL={pnl_pct:+.2f}%",
                ))
                self._reset_position()

        # ── In SHORT position: check exit ────────────────────────────────────
        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)
            trailing_stop = self.lowest_since_entry * (1 + self.TRAILING_STOP_PCT / 100)
            take_profit_price = self.entry_price * (1 - self.TAKE_PROFIT_PCT / 100)
            pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100

            self.logger.info(
                "[%s] Tick #%d — SHORT %dx | price=$%.2f | entry=$%.2f | PnL=%+.2f%% | "
                "TP=$%.2f | SL=$%.2f (trail from $%.2f)",
                self.pair, self._tick_count, self.leverage, current_price,
                self.entry_price, pnl_pct,
                take_profit_price, trailing_stop, self.lowest_since_entry,
            )

            if current_price >= trailing_stop:
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT trailing stop hit (low={self.lowest_since_entry:.2f}, "
                    f"stop={trailing_stop:.2f}, PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif current_price <= take_profit_price:
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT take profit hit at {current_price:.2f} (PnL={pnl_pct:+.2f}%)",
                ))
                self._reset_position()
            elif rsi_now < self.RSI_SHORT_EXIT:
                signals.append(self._close_short_signal(
                    current_price,
                    f"SHORT exit — RSI oversold ({rsi_now:.1f}), PnL={pnl_pct:+.2f}%",
                ))
                self._reset_position()

        # ── No position: check entry ─────────────────────────────────────────
        else:
            # Log every tick with full indicator state
            self.logger.info(
                "[%s] Tick #%d — NO POSITION | price=$%.2f | RSI=%.1f | "
                "MACD=%.4f vs Signal=%.4f | Cross up=%s | Cross down=%s | "
                "Need: LONG RSI<%d, SHORT RSI>%d",
                self.pair, self._tick_count, current_price,
                rsi_now, macd_now, signal_now,
                macd_crossed_up, macd_crossed_down,
                self.RSI_LONG_ENTRY, self.RSI_SHORT_ENTRY,
            )

            capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
            amount = (capital / current_price) * self.leverage

            # LONG entry: RSI oversold + MACD bullish crossover
            if rsi_now < self.RSI_LONG_ENTRY and macd_crossed_up:
                stop_loss = current_price * (1 - self.TRAILING_STOP_PCT / 100)

                self.logger.info(
                    "[%s] LONG ENTRY SIGNAL — RSI=%.1f + MACD crossover! Opening %dx LONG $%.2f",
                    self.pair, rsi_now, self.leverage, capital,
                )

                signals.append(Signal(
                    side="buy",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"LONG: RSI={rsi_now:.1f} (<{self.RSI_LONG_ENTRY}) + MACD crossover (bullish)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 + self.TAKE_PROFIT_PCT / 100),
                    leverage=self.leverage,
                    position_type="long",
                    exchange_id="delta",
                ))
                self.position_side = "long"
                self.entry_price = current_price
                self.highest_since_entry = current_price

            # SHORT entry: RSI overbought + MACD bearish crossover
            elif rsi_now > self.RSI_SHORT_ENTRY and macd_crossed_down and config.delta.enable_shorting:
                stop_loss = current_price * (1 + self.TRAILING_STOP_PCT / 100)

                self.logger.info(
                    "[%s] SHORT ENTRY SIGNAL — RSI=%.1f + MACD cross down! Opening %dx SHORT $%.2f",
                    self.pair, rsi_now, self.leverage, capital,
                )

                signals.append(Signal(
                    side="sell",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"SHORT: RSI={rsi_now:.1f} (>{self.RSI_SHORT_ENTRY}) + MACD crossover (bearish)",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=stop_loss,
                    take_profit=current_price * (1 - self.TAKE_PROFIT_PCT / 100),
                    leverage=self.leverage,
                    position_type="short",
                    exchange_id="delta",
                ))
                self.position_side = "short"
                self.entry_price = current_price
                self.lowest_since_entry = current_price

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
