"""Scalping strategy — fast 1m RSI + Bollinger Band + Volume overlay.

Runs as an independent parallel task alongside whatever primary strategy
is active. Checks every 15 seconds on 1m candles. Designed for small,
frequent gains with tight TP/SL and time-based exits.

Works on both Binance (spot) and Delta (futures with leverage).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt
import pandas as pd
import ta

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName
from alpha.utils import setup_logger

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor

logger = setup_logger("scalp")


class ScalpStrategy(BaseStrategy):
    """
    Fast scalping overlay — runs alongside primary strategies.

    Entry (ALL 3 required):
      LONG:  RSI(14) on 1m < 38 + price touches lower BB(20,2) + volume > 1.2x avg
      SHORT: RSI(14) on 1m > 62 + price touches upper BB(20,2) + volume > 1.2x avg

    Exit:
      - TP: 0.4%
      - SL: 0.2%
      - Trailing: after 0.2% profit, trail at 0.15%
      - Time: force close after 15 minutes

    Risk:
      - 40% of capital per scalp
      - Max 1 position per pair, max 2 total
      - Max 15 trades/hour, pause 30min after 3 consecutive losses
      - Separate 5% daily scalp loss limit
    """

    name = StrategyName.SCALP
    check_interval_sec = 15  # 15 second ticks

    # Entry thresholds
    RSI_LONG_ENTRY = 38
    RSI_SHORT_ENTRY = 62
    VOL_RATIO_MIN = 1.2

    # Exit thresholds
    TAKE_PROFIT_PCT = 0.4
    STOP_LOSS_PCT = 0.2
    TRAILING_ACTIVATE_PCT = 0.2   # start trailing after this profit
    TRAILING_DISTANCE_PCT = 0.15  # trail at this distance
    MAX_HOLD_SECONDS = 15 * 60    # 15 minutes

    # Position sizing
    CAPITAL_PCT = 40.0  # use 40% of capital per scalp
    MAX_POSITIONS_PER_PAIR = 1
    MAX_POSITIONS_TOTAL = 2

    # Rate limiting / risk
    MAX_TRADES_PER_HOUR = 15
    CONSECUTIVE_LOSS_PAUSE = 3
    PAUSE_DURATION_SEC = 30 * 60  # 30 minutes
    DAILY_LOSS_LIMIT_PCT = 5.0    # separate from main daily limit
    MAX_SPREAD_PCT = 0.1

    def __init__(
        self,
        pair: str,
        executor: TradeExecutor,
        risk_manager: RiskManager,
        exchange: Any = None,
        is_futures: bool = False,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.trade_exchange: ccxt.Exchange | None = exchange
        self.is_futures = is_futures
        self.leverage: int = min(config.delta.leverage, 10) if is_futures else 1

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")

        # Rate limiting
        self._hourly_trades: list[float] = []  # timestamps of trades this hour
        self._consecutive_losses: int = 0
        self._paused_until: float = 0.0
        self._daily_scalp_loss: float = 0.0

        # Stats for hourly summary
        self.hourly_wins: int = 0
        self.hourly_losses: int = 0
        self.hourly_pnl: float = 0.0

        # Tick tracking
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0

    async def on_start(self) -> None:
        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        tag = f"{self.leverage}x futures" if self.is_futures else "spot"
        self.logger.info(
            "[%s] Scalp strategy ACTIVE (%s) — checking every %ds, "
            "LONG RSI<%d + BB + Vol>%.1fx, SHORT RSI>%d + BB + Vol>%.1fx",
            self.pair, tag, self.check_interval_sec,
            self.RSI_LONG_ENTRY, self.VOL_RATIO_MIN,
            self.RSI_SHORT_ENTRY, self.VOL_RATIO_MIN,
        )

    async def on_stop(self) -> None:
        """Log scalp stats on stop."""
        self.logger.info(
            "[%s] Scalp stopped — %dW/%dL, P&L=$%.4f",
            self.pair, self.hourly_wins, self.hourly_losses, self.hourly_pnl,
        )

    async def check(self) -> list[Signal]:
        """One scalping tick — fetch 1m candles, check entry/exit conditions."""
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.trade_exchange or self.executor.exchange

        # ── Pause check ──────────────────────────────────────────────────────
        now = time.monotonic()
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            if self._tick_count % 20 == 0:  # log every ~5 min
                self.logger.info(
                    "[%s] Scalp PAUSED (%d consecutive losses) — resuming in %dm",
                    self.pair, self._consecutive_losses, remaining // 60,
                )
            return signals

        # ── Daily scalp loss limit ───────────────────────────────────────────
        if self._daily_scalp_loss <= -(self.risk_manager.capital * self.DAILY_LOSS_LIMIT_PCT / 100):
            if self._tick_count % 20 == 0:
                self.logger.info(
                    "[%s] Scalp STOPPED — daily scalp loss limit hit ($%.4f)",
                    self.pair, self._daily_scalp_loss,
                )
            return signals

        # ── Rate limit check ─────────────────────────────────────────────────
        cutoff = time.time() - 3600
        self._hourly_trades = [t for t in self._hourly_trades if t > cutoff]
        if len(self._hourly_trades) >= self.MAX_TRADES_PER_HOUR:
            if self._tick_count % 20 == 0:
                self.logger.info(
                    "[%s] Scalp rate limited — %d trades this hour (max %d)",
                    self.pair, len(self._hourly_trades), self.MAX_TRADES_PER_HOUR,
                )
            return signals

        # ── Fetch 1m candles ─────────────────────────────────────────────────
        ohlcv = await exchange.fetch_ohlcv(self.pair, "1m", limit=50)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # Indicators
        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = float(bb.bollinger_hband().iloc[-1])
        bb_lower = float(bb.bollinger_lband().iloc[-1])
        bb_mid = float(bb.bollinger_mavg().iloc[-1])

        rsi_now = float(rsi_series.iloc[-1])

        # Volume ratio: current vs avg of last 10
        avg_vol = float(volume.iloc[-11:-1].mean()) if len(volume) >= 11 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # BB position: 0% = lower band, 100% = upper band
        bb_range = bb_upper - bb_lower
        bb_position = ((current_price - bb_lower) / bb_range * 100) if bb_range > 0 else 50

        # Price touching bands
        candle_low = float(low.iloc[-1])
        candle_high = float(high.iloc[-1])
        touches_lower_bb = candle_low <= bb_lower
        touches_upper_bb = candle_high >= bb_upper

        # ── Heartbeat every 5 minutes ────────────────────────────────────────
        if now - self._last_heartbeat >= 300:
            self._last_heartbeat = now
            tag = f"{self.leverage}x" if self.is_futures else "spot"
            if self.in_position:
                hold_sec = time.monotonic() - self.entry_time
                self.logger.info(
                    "[%s] Scalp heartbeat (%s) — %s @ $%.2f for %ds, RSI=%.1f, BB=%.0f%%",
                    self.pair, tag, self.position_side, self.entry_price,
                    int(hold_sec), rsi_now, bb_position,
                )
            else:
                self.logger.info(
                    "[%s] Scalp heartbeat (%s) — no position, RSI=%.1f, BB=%.0f%%, Vol=%.1fx, "
                    "trades/hr=%d, losses=%dW/%dL",
                    self.pair, tag, rsi_now, bb_position, vol_ratio,
                    len(self._hourly_trades), self.hourly_wins, self.hourly_losses,
                )

        # ── In position: check exit ──────────────────────────────────────────
        if self.in_position:
            hold_seconds = time.monotonic() - self.entry_time

            if self.position_side == "long":
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                self.highest_since_entry = max(self.highest_since_entry, current_price)

                # Trailing stop (after reaching TRAILING_ACTIVATE_PCT profit)
                if pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                    trail_stop = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                else:
                    trail_stop = self.entry_price * (1 - self.STOP_LOSS_PCT / 100)

                tp_price = self.entry_price * (1 + self.TAKE_PROFIT_PCT / 100)

                self.logger.info(
                    "[%s] Scalp tick #%d — LONG | $%.2f | PnL=%+.3f%% | hold=%ds | SL=$%.2f | TP=$%.2f",
                    self.pair, self._tick_count, current_price, pnl_pct,
                    int(hold_seconds), trail_stop, tp_price,
                )

                # Check exits
                if current_price >= tp_price:
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp TP hit +{pnl_pct:.3f}% in {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)
                elif current_price <= trail_stop:
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp SL hit {pnl_pct:+.3f}% in {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)
                elif hold_seconds >= self.MAX_HOLD_SECONDS:
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp time exit {pnl_pct:+.3f}% after {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)

            elif self.position_side == "short":
                pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                self.lowest_since_entry = min(self.lowest_since_entry, current_price)

                if pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                    trail_stop = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                else:
                    trail_stop = self.entry_price * (1 + self.STOP_LOSS_PCT / 100)

                tp_price = self.entry_price * (1 - self.TAKE_PROFIT_PCT / 100)

                self.logger.info(
                    "[%s] Scalp tick #%d — SHORT | $%.2f | PnL=%+.3f%% | hold=%ds | SL=$%.2f | TP=$%.2f",
                    self.pair, self._tick_count, current_price, pnl_pct,
                    int(hold_seconds), trail_stop, tp_price,
                )

                if current_price <= tp_price:
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp TP hit +{pnl_pct:.3f}% in {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)
                elif current_price >= trail_stop:
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp SL hit {pnl_pct:+.3f}% in {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)
                elif hold_seconds >= self.MAX_HOLD_SECONDS:
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp time exit {pnl_pct:+.3f}% after {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct)

        # ── No position: check entry ─────────────────────────────────────────
        else:
            # Log every tick
            self.logger.info(
                "[%s] Scalp tick #%d — NO POS | $%.2f | RSI=%.1f | BB=%.0f%% | "
                "Vol=%.1fx | Lower BB touch=%s | Upper BB touch=%s",
                self.pair, self._tick_count, current_price,
                rsi_now, bb_position, vol_ratio,
                touches_lower_bb, touches_upper_bb,
            )

            # Check if primary strategy already has an open position on this pair
            if self.risk_manager.has_position(self.pair):
                return signals

            # Count total scalp positions across all pairs
            scalp_positions = sum(
                1 for p in self.risk_manager.open_positions
                if p.strategy == "scalp"
            )
            if scalp_positions >= self.MAX_POSITIONS_TOTAL:
                return signals

            # Spread check (skip if bid-ask too wide)
            try:
                ticker = await exchange.fetch_ticker(self.pair)
                bid = ticker.get("bid", 0) or 0
                ask = ticker.get("ask", 0) or 0
                if bid > 0 and ask > 0:
                    spread_pct = ((ask - bid) / bid) * 100
                    if spread_pct > self.MAX_SPREAD_PCT:
                        self.logger.info(
                            "[%s] Scalp skip — spread %.3f%% > max %.1f%%",
                            self.pair, spread_pct, self.MAX_SPREAD_PCT,
                        )
                        return signals
            except Exception:
                pass  # skip spread check if ticker fails

            capital = self.risk_manager.capital * (self.CAPITAL_PCT / 100)
            amount = capital / current_price
            if self.is_futures:
                amount *= self.leverage

            # LONG: RSI < 38 + lower BB touch + volume confirmation
            if (rsi_now < self.RSI_LONG_ENTRY
                    and touches_lower_bb
                    and vol_ratio >= self.VOL_RATIO_MIN):
                self.logger.info(
                    "[%s] SCALP LONG ENTRY — RSI=%.1f, BB touch, Vol=%.1fx @ $%.2f",
                    self.pair, rsi_now, vol_ratio, current_price,
                )
                sl = current_price * (1 - self.STOP_LOSS_PCT / 100)
                tp = current_price * (1 + self.TAKE_PROFIT_PCT / 100)
                signals.append(Signal(
                    side="buy",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"Scalp LONG: RSI={rsi_now:.1f} + lower BB touch + Vol={vol_ratio:.1f}x",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=sl,
                    take_profit=tp,
                    leverage=self.leverage if self.is_futures else 1,
                    position_type="long" if self.is_futures else "spot",
                    exchange_id="delta" if self.is_futures else "binance",
                ))
                self._open_position("long", current_price)

            # SHORT: RSI > 62 + upper BB touch + volume confirmation (futures only)
            elif (self.is_futures
                    and config.delta.enable_shorting
                    and rsi_now > self.RSI_SHORT_ENTRY
                    and touches_upper_bb
                    and vol_ratio >= self.VOL_RATIO_MIN):
                self.logger.info(
                    "[%s] SCALP SHORT ENTRY — RSI=%.1f, BB touch, Vol=%.1fx @ $%.2f",
                    self.pair, rsi_now, vol_ratio, current_price,
                )
                sl = current_price * (1 + self.STOP_LOSS_PCT / 100)
                tp = current_price * (1 - self.TAKE_PROFIT_PCT / 100)
                signals.append(Signal(
                    side="sell",
                    price=current_price,
                    amount=amount,
                    order_type="market",
                    reason=f"Scalp SHORT: RSI={rsi_now:.1f} + upper BB touch + Vol={vol_ratio:.1f}x",
                    strategy=self.name,
                    pair=self.pair,
                    stop_loss=sl,
                    take_profit=tp,
                    leverage=self.leverage,
                    position_type="short",
                    exchange_id="delta",
                ))
                self._open_position("short", current_price)

        return signals

    # -- Position management ---------------------------------------------------

    def _open_position(self, side: str, price: float) -> None:
        self.in_position = True
        self.position_side = side
        self.entry_price = price
        self.entry_time = time.monotonic()
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self._hourly_trades.append(time.time())

    def _record_scalp_result(self, pnl_pct: float) -> None:
        """Track win/loss and enforce consecutive loss pause."""
        actual_pnl = self.entry_price * (pnl_pct / 100) * (self.CAPITAL_PCT / 100)
        self.hourly_pnl += actual_pnl
        self._daily_scalp_loss += actual_pnl if actual_pnl < 0 else 0

        if pnl_pct >= 0:
            self.hourly_wins += 1
            self._consecutive_losses = 0
        else:
            self.hourly_losses += 1
            self._consecutive_losses += 1

        self.logger.info(
            "[%s] Scalp closed %+.3f%% — W/L: %d/%d, streak: %d loss(es), daily scalp P&L: $%.4f",
            self.pair, pnl_pct, self.hourly_wins, self.hourly_losses,
            self._consecutive_losses, self._daily_scalp_loss,
        )

        # Pause after consecutive losses
        if self._consecutive_losses >= self.CONSECUTIVE_LOSS_PAUSE:
            self._paused_until = time.monotonic() + self.PAUSE_DURATION_SEC
            self.logger.warning(
                "[%s] Scalp PAUSING for 30min — %d consecutive losses",
                self.pair, self._consecutive_losses,
            )

        # Reset position
        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0

    def _exit_signal(self, price: float, side: str, reason: str) -> Signal:
        """Generate exit signal for a scalp position."""
        capital = self.risk_manager.capital * (self.CAPITAL_PCT / 100)
        amount = capital / price
        if self.is_futures:
            amount *= self.leverage

        exit_side = "sell" if side == "long" else "buy"
        return Signal(
            side=exit_side,
            price=price,
            amount=amount,
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.pair,
            leverage=self.leverage if self.is_futures else 1,
            position_type=side if self.is_futures else "spot",
            reduce_only=self.is_futures,
            exchange_id="delta" if self.is_futures else "binance",
        )

    # -- Stats -----------------------------------------------------------------

    def reset_hourly_stats(self) -> dict[str, Any]:
        """Reset hourly counters and return stats for summary."""
        stats = {
            "pair": self.pair,
            "wins": self.hourly_wins,
            "losses": self.hourly_losses,
            "pnl": self.hourly_pnl,
            "trades": self.hourly_wins + self.hourly_losses,
        }
        self.hourly_wins = 0
        self.hourly_losses = 0
        self.hourly_pnl = 0.0
        return stats

    def reset_daily_stats(self) -> None:
        """Reset daily scalp loss tracker at midnight."""
        self._daily_scalp_loss = 0.0
        self._consecutive_losses = 0
        self._paused_until = 0.0
