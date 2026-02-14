"""Aggressive scalping strategy — fast 1m RSI + Bollinger Band + Volume overlay.

Runs as an independent parallel task alongside whatever primary strategy
is active. Checks every 10 seconds on 1m candles. Designed for small,
frequent gains with tight TP/SL and time-based exits.

Features:
  - 2-of-3 entry: any 2 of [RSI, BB proximity, volume] triggers entry
  - Momentum burst detection: 0.3%+ move in 1min on 2x volume
  - Quick reversal: flip direction after SL if conditions allow
  - Works on both Binance (spot) and Delta (futures with leverage)
"""

from __future__ import annotations

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
    Scalping overlay — runs alongside primary strategies.

    Entry (2 of 3 conditions required):
      LONG:  2 of [RSI(14) < 40, price within 0.5% of lower BB(20,2), volume > 0.8x avg]
      SHORT: 2 of [RSI(14) > 60, price within 0.5% of upper BB(20,2), volume > 0.8x avg]
      Extreme RSI (<30 / >70) enters regardless — bypasses all conditions.

    Momentum Burst:
      Price moves 0.3%+ in 1 minute on 2x volume -> enter in direction of move.

    Quick Reversal:
      After SL hit, immediately check for opposite direction entry.

    Exit:
      - TP: 1.5% price move (= 7.5% on capital at 5x leverage)
      - SL: 0.75% (= 3.75% on capital at 5x)
      - Trailing: activate at 0.80%, trail at 0.40%
      - Time: force close after 45 minutes
      - Risk/reward: 2:1 — need 34% win rate to profit

    Risk:
      - 30% of capital per scalp (futures), 50% spot
      - Max 2 positions per pair, max 4 total
      - Max 30 trades/hour, pause 15min after 5 consecutive losses
      - Separate 5% daily scalp loss limit
    """

    name = StrategyName.SCALP
    check_interval_sec = 10  # 10 second ticks (aggressive)

    # Entry thresholds (tighter RSI = fewer but higher-quality entries)
    RSI_LONG_ENTRY = 40       # tightened from 45 for stronger signals
    RSI_SHORT_ENTRY = 60      # tightened from 55 for stronger signals
    RSI_EXTREME_LONG = 30     # bypass all conditions
    RSI_EXTREME_SHORT = 70    # bypass all conditions
    VOL_RATIO_MIN = 0.8       # volume above 80% of average
    BB_PROXIMITY_PCT = 0.5    # within 0.5% of BB

    # Momentum burst detection
    BURST_PRICE_PCT = 0.3     # 0.3% move in 1 candle
    BURST_VOL_RATIO = 2.0     # 2x average volume

    # Exit thresholds (wider for 2:1 R/R — need 34% win rate to profit)
    # At 5x leverage: 1.5% price = 7.5% on capital, 0.75% price = 3.75% on capital
    TAKE_PROFIT_PCT = 1.5     # 1.5% price move TP
    STOP_LOSS_PCT = 0.75      # 0.75% price move SL (2:1 R/R)
    TRAILING_ACTIVATE_PCT = 0.80  # start trailing after 0.80% profit
    TRAILING_DISTANCE_PCT = 0.40  # trail at 0.40% from high/low
    MAX_HOLD_SECONDS = 45 * 60    # 45 minutes (bigger moves need more time)

    # Position sizing (spot uses more capital to meet Binance $5 min notional)
    CAPITAL_PCT_SPOT = 50.0      # 50% for spot (Binance $5 minimum)
    CAPITAL_PCT_FUTURES = 30.0   # 30% for futures (leverage handles it)
    MAX_POSITIONS_PER_PAIR = 2   # was 1
    MAX_POSITIONS_TOTAL = 4      # was 2

    # Rate limiting / risk
    MAX_TRADES_PER_HOUR = 30     # was 15
    CONSECUTIVE_LOSS_PAUSE = 5   # was 3
    PAUSE_DURATION_SEC = 15 * 60 # 15 minutes (was 30)
    DAILY_LOSS_LIMIT_PCT = 5.0
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
        self.capital_pct: float = self.CAPITAL_PCT_FUTURES if is_futures else self.CAPITAL_PCT_SPOT
        self._exchange_id: str = "delta" if is_futures else "binance"

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_amount: float = 0.0  # amount used at entry (for consistent exit)
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")
        self._positions_on_pair: int = 0

        # Rate limiting
        self._hourly_trades: list[float] = []
        self._consecutive_losses: int = 0
        self._paused_until: float = 0.0
        self._daily_scalp_loss: float = 0.0

        # Quick reversal state
        self._last_sl_side: str | None = None
        self._last_sl_time: float = 0.0

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
        self.entry_amount = 0.0
        self._positions_on_pair = 0
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        tag = f"{self.leverage}x futures" if self.is_futures else "spot"
        self.logger.info(
            "[%s] Scalp ACTIVE (%s, %.0f%% capital) — tick=%ds, "
            "2-of-3: RSI<%d BB<%.1f%% Vol>%.1fx, "
            "TP=%.2f%% SL=%.2f%% | burst=%.1f%% @ %.1fx vol",
            self.pair, tag, self.capital_pct, self.check_interval_sec,
            self.RSI_LONG_ENTRY, self.BB_PROXIMITY_PCT, self.VOL_RATIO_MIN,
            self.TAKE_PROFIT_PCT, self.STOP_LOSS_PCT,
            self.BURST_PRICE_PCT, self.BURST_VOL_RATIO,
        )

    async def on_stop(self) -> None:
        self.logger.info(
            "[%s] Scalp stopped — %dW/%dL, P&L=$%.4f",
            self.pair, self.hourly_wins, self.hourly_losses, self.hourly_pnl,
        )

    async def check(self) -> list[Signal]:
        """One scalping tick — fetch 1m candles, check entry/exit conditions."""
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.trade_exchange or self.executor.exchange

        # ── Pause check ──────────────────────────────────────────────────
        now = time.monotonic()
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Scalp PAUSED (%d losses) — resuming in %dm",
                    self.pair, self._consecutive_losses, remaining // 60,
                )
            return signals

        # ── Daily scalp loss limit ───────────────────────────────────────
        if self._daily_scalp_loss <= -(self.risk_manager.get_exchange_capital(self._exchange_id) * self.DAILY_LOSS_LIMIT_PCT / 100):
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Scalp STOPPED — daily loss limit hit ($%.4f)",
                    self.pair, self._daily_scalp_loss,
                )
            return signals

        # ── Rate limit check ─────────────────────────────────────────────
        cutoff = time.time() - 3600
        self._hourly_trades = [t for t in self._hourly_trades if t > cutoff]
        if len(self._hourly_trades) >= self.MAX_TRADES_PER_HOUR:
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Scalp rate limited — %d trades/hr (max %d)",
                    self.pair, len(self._hourly_trades), self.MAX_TRADES_PER_HOUR,
                )
            return signals

        # ── Fetch 1m candles ─────────────────────────────────────────────
        ohlcv = await exchange.fetch_ohlcv(self.pair, "1m", limit=50)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # Indicators
        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = float(bb.bollinger_hband().iloc[-1])
        bb_lower = float(bb.bollinger_lband().iloc[-1])

        rsi_now = float(rsi_series.iloc[-1])

        # Volume ratio
        avg_vol = float(volume.iloc[-11:-1].mean()) if len(volume) >= 11 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # BB position
        bb_range = bb_upper - bb_lower
        bb_position = ((current_price - bb_lower) / bb_range * 100) if bb_range > 0 else 50

        # Price proximity to bands (percentage distance)
        lower_dist_pct = ((current_price - bb_lower) / bb_lower * 100) if bb_lower > 0 else 999
        upper_dist_pct = ((bb_upper - current_price) / bb_upper * 100) if bb_upper > 0 else 999

        # Momentum burst: price change in last candle
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else current_price
        candle_change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0

        # ── Heartbeat every 5 minutes ────────────────────────────────────
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
                    "[%s] Scalp heartbeat (%s) — no pos, RSI=%.1f, BB=%.0f%%, Vol=%.1fx, "
                    "trades/hr=%d, W/L=%d/%d, streak=%d",
                    self.pair, tag, rsi_now, bb_position, vol_ratio,
                    len(self._hourly_trades), self.hourly_wins, self.hourly_losses,
                    self._consecutive_losses,
                )

        # ── In position: check exit ──────────────────────────────────────
        if self.in_position:
            hold_seconds = time.monotonic() - self.entry_time

            if self.position_side == "long":
                pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                self.highest_since_entry = max(self.highest_since_entry, current_price)

                if pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                    trail_stop = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                else:
                    trail_stop = self.entry_price * (1 - self.STOP_LOSS_PCT / 100)

                tp_price = self.entry_price * (1 + self.TAKE_PROFIT_PCT / 100)

                self.logger.info(
                    "[%s] Scalp #%d — LONG | $%.2f | PnL=%+.3f%% | %ds | SL=$%.2f | TP=$%.2f",
                    self.pair, self._tick_count, current_price, pnl_pct,
                    int(hold_seconds), trail_stop, tp_price,
                )

                if current_price >= tp_price:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp TP +{pnl_pct:.2f}% price (+{cap_pct:.1f}% capital at {self.leverage}x)"))
                    self._record_scalp_result(pnl_pct, "tp")
                elif current_price <= trail_stop:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp {'trail' if pnl_pct >= self.TRAILING_ACTIVATE_PCT else 'SL'} {pnl_pct:+.2f}% price ({cap_pct:+.1f}% capital at {self.leverage}x)"))
                    self._record_scalp_result(pnl_pct, "trail" if pnl_pct >= 0 else "sl")
                    self._last_sl_side = "long"
                    self._last_sl_time = time.monotonic()
                elif hold_seconds >= self.MAX_HOLD_SECONDS:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "long",
                        f"Scalp timeout {pnl_pct:+.2f}% price ({cap_pct:+.1f}% capital) after {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct, "timeout")

            elif self.position_side == "short":
                pnl_pct = ((self.entry_price - current_price) / self.entry_price) * 100
                self.lowest_since_entry = min(self.lowest_since_entry, current_price)

                if pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                    trail_stop = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                else:
                    trail_stop = self.entry_price * (1 + self.STOP_LOSS_PCT / 100)

                tp_price = self.entry_price * (1 - self.TAKE_PROFIT_PCT / 100)

                self.logger.info(
                    "[%s] Scalp #%d — SHORT | $%.2f | PnL=%+.3f%% | %ds | SL=$%.2f | TP=$%.2f",
                    self.pair, self._tick_count, current_price, pnl_pct,
                    int(hold_seconds), trail_stop, tp_price,
                )

                if current_price <= tp_price:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp TP +{pnl_pct:.2f}% price (+{cap_pct:.1f}% capital at {self.leverage}x)"))
                    self._record_scalp_result(pnl_pct, "tp")
                elif current_price >= trail_stop:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp {'trail' if pnl_pct >= self.TRAILING_ACTIVATE_PCT else 'SL'} {pnl_pct:+.2f}% price ({cap_pct:+.1f}% capital at {self.leverage}x)"))
                    self._record_scalp_result(pnl_pct, "trail" if pnl_pct >= 0 else "sl")
                    self._last_sl_side = "short"
                    self._last_sl_time = time.monotonic()
                elif hold_seconds >= self.MAX_HOLD_SECONDS:
                    cap_pct = pnl_pct * self.leverage
                    signals.append(self._exit_signal(current_price, "short",
                        f"Scalp timeout {pnl_pct:+.2f}% price ({cap_pct:+.1f}% capital) after {int(hold_seconds)}s"))
                    self._record_scalp_result(pnl_pct, "timeout")

        # ── No position: check entry ─────────────────────────────────────
        else:
            self.logger.info(
                "[%s] Scalp #%d — NO POS | $%.2f | RSI=%.1f | BB=%.0f%% | "
                "Vol=%.1fx | LowBB=%.2f%% | UpBB=%.2f%% | Chg=%.2f%%",
                self.pair, self._tick_count, current_price,
                rsi_now, bb_position, vol_ratio,
                lower_dist_pct, upper_dist_pct, candle_change_pct,
            )

            if self.risk_manager.has_position(self.pair):
                return signals

            scalp_positions = sum(
                1 for p in self.risk_manager.open_positions
                if p.strategy == "scalp"
            )
            if scalp_positions >= self.MAX_POSITIONS_TOTAL:
                return signals

            # Spread check
            try:
                ticker = await exchange.fetch_ticker(self.pair)
                bid = ticker.get("bid", 0) or 0
                ask = ticker.get("ask", 0) or 0
                if bid > 0 and ask > 0:
                    spread_pct = ((ask - bid) / bid) * 100
                    if spread_pct > self.MAX_SPREAD_PCT:
                        return signals
            except Exception:
                pass

            # Check available balance before sizing
            available = self.risk_manager.get_available_capital(self._exchange_id)
            min_balance = 5.50 if self._exchange_id == "binance" else 1.00
            if available < min_balance:
                if self._tick_count % 30 == 0:  # Log once every ~5 min, not every tick
                    self.logger.info(
                        "[%s] Insufficient %s balance: $%.2f < $%.2f — skipping",
                        self.pair, self._exchange_id, available, min_balance,
                    )
                return signals

            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            capital = exchange_capital * (self.capital_pct / 100)
            # Don't size more than available
            capital = min(capital, available)

            if self.is_futures:
                # Delta uses INTEGER contracts, not fractional coin amounts
                # ETH contract = 0.01 ETH, BTC contract = 0.001 BTC
                from alpha.trade_executor import DELTA_CONTRACT_SIZE
                contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0)
                if contract_size <= 0:
                    self.logger.warning("[%s] Unknown Delta contract size — skipping", self.pair)
                    return signals

                # Minimum collateral for 1 contract
                one_contract_collateral = (contract_size * current_price) / self.leverage
                if one_contract_collateral > available:
                    if self._tick_count % 30 == 0:
                        self.logger.info(
                            "[%s] 1 contract needs $%.2f collateral > $%.2f available — skipping",
                            self.pair, one_contract_collateral, available,
                        )
                    return signals

                # Calculate contracts: collateral * leverage / (contract_size * price)
                contracts = int(capital * self.leverage / (contract_size * current_price))
                contracts = max(contracts, 1)
                # Verify we can afford the contracts
                total_collateral = contracts * one_contract_collateral
                if total_collateral > available:
                    contracts = max(1, int(available / one_contract_collateral))
                    total_collateral = contracts * one_contract_collateral

                # Convert back to coin amount for signal (trade_executor will re-convert to contracts)
                amount = contracts * contract_size

                self.logger.debug(
                    "[%s] Sizing (futures): %s_capital=$%.2f, avail=$%.2f × %.0f%% = $%.2f "
                    "→ %d contracts (%.6f %s, collateral=$%.2f, notional=$%.2f, %dx)",
                    self.pair, self._exchange_id, exchange_capital, available,
                    self.capital_pct, capital, contracts,
                    amount, self.pair.split("/")[0],
                    total_collateral, total_collateral * self.leverage, self.leverage,
                )
            else:
                amount = capital / current_price
                self.logger.debug(
                    "[%s] Sizing (spot): %s_capital=$%.2f, avail=$%.2f × %.0f%% = $%.2f → amount=%.8f",
                    self.pair, self._exchange_id, exchange_capital, available, self.capital_pct,
                    capital, amount,
                )

            entry_signal = None

            # ── Quick Reversal Entry ─────────────────────────────────
            if self._last_sl_side and (time.monotonic() - self._last_sl_time) < 30:
                if self._last_sl_side == "long" and self.is_futures and rsi_now > 50:
                    self.logger.info(
                        "[%s] SCALP REVERSAL -> SHORT after long SL, RSI=%.1f",
                        self.pair, rsi_now,
                    )
                    entry_signal = ("short", f"Reversal SHORT after long SL: RSI={rsi_now:.1f}")
                elif self._last_sl_side == "short" and rsi_now < 50:
                    self.logger.info(
                        "[%s] SCALP REVERSAL -> LONG after short SL, RSI=%.1f",
                        self.pair, rsi_now,
                    )
                    entry_signal = ("long", f"Reversal LONG after short SL: RSI={rsi_now:.1f}")
                self._last_sl_side = None

            # ── Momentum Burst Detection ─────────────────────────────
            if entry_signal is None and abs(candle_change_pct) >= self.BURST_PRICE_PCT and vol_ratio >= self.BURST_VOL_RATIO:
                if candle_change_pct > 0:
                    self.logger.info(
                        "[%s] SCALP BURST LONG — +%.2f%% in 1m, Vol=%.1fx",
                        self.pair, candle_change_pct, vol_ratio,
                    )
                    entry_signal = ("long", f"Burst LONG: +{candle_change_pct:.2f}% @ {vol_ratio:.1f}x vol")
                elif self.is_futures and config.delta.enable_shorting:
                    self.logger.info(
                        "[%s] SCALP BURST SHORT — %.2f%% in 1m, Vol=%.1fx",
                        self.pair, candle_change_pct, vol_ratio,
                    )
                    entry_signal = ("short", f"Burst SHORT: {candle_change_pct:.2f}% @ {vol_ratio:.1f}x vol")

            # ── Standard Entry: 2 of 3 conditions (RSI + BB + Volume) ──
            if entry_signal is None:
                vol_ok = vol_ratio >= self.VOL_RATIO_MIN
                extreme_long = rsi_now < self.RSI_EXTREME_LONG
                extreme_short = rsi_now > self.RSI_EXTREME_SHORT

                # ── Extreme RSI bypass: enter regardless ──
                if extreme_long:
                    self.logger.info(
                        "[%s] SCALP LONG (extreme) — RSI=%.1f <%d, bypasses other conditions",
                        self.pair, rsi_now, self.RSI_EXTREME_LONG,
                    )
                    entry_signal = ("long", f"Scalp LONG extreme: RSI={rsi_now:.1f} BB={lower_dist_pct:.2f}% Vol={vol_ratio:.1f}x")

                elif extreme_short and self.is_futures and config.delta.enable_shorting:
                    self.logger.info(
                        "[%s] SCALP SHORT (extreme) — RSI=%.1f >%d, bypasses other conditions",
                        self.pair, rsi_now, self.RSI_EXTREME_SHORT,
                    )
                    entry_signal = ("short", f"Scalp SHORT extreme: RSI={rsi_now:.1f} BB={upper_dist_pct:.2f}% Vol={vol_ratio:.1f}x")

                else:
                    # ── 2-of-3 scoring: RSI + BB proximity + Volume ──
                    # LONG check
                    long_rsi_ok = rsi_now < self.RSI_LONG_ENTRY
                    long_bb_ok = lower_dist_pct <= self.BB_PROXIMITY_PCT
                    long_conds = int(long_rsi_ok) + int(long_bb_ok) + int(vol_ok)

                    long_rsi_tag = f"RSI={rsi_now:.1f} ✓" if long_rsi_ok else f"RSI={rsi_now:.1f} ✗"
                    long_bb_tag = f"BB={lower_dist_pct:.2f}% ✓" if long_bb_ok else f"BB={lower_dist_pct:.2f}% ✗"
                    long_vol_tag = f"Vol={vol_ratio:.1f}x ✓" if vol_ok else f"Vol={vol_ratio:.1f}x ✗"

                    if long_conds >= 2:
                        self.logger.info(
                            "[%s] SCALP LONG — %d/3 conditions: %s, %s, %s",
                            self.pair, long_conds, long_rsi_tag, long_bb_tag, long_vol_tag,
                        )
                        entry_signal = ("long", f"Scalp LONG {long_conds}/3: {long_rsi_tag}, {long_bb_tag}, {long_vol_tag}")

                    # SHORT check (only on futures with shorting enabled)
                    if entry_signal is None and self.is_futures and config.delta.enable_shorting:
                        short_rsi_ok = rsi_now > self.RSI_SHORT_ENTRY
                        short_bb_ok = upper_dist_pct <= self.BB_PROXIMITY_PCT
                        short_conds = int(short_rsi_ok) + int(short_bb_ok) + int(vol_ok)

                        short_rsi_tag = f"RSI={rsi_now:.1f} ✓" if short_rsi_ok else f"RSI={rsi_now:.1f} ✗"
                        short_bb_tag = f"BB={upper_dist_pct:.2f}% ✓" if short_bb_ok else f"BB={upper_dist_pct:.2f}% ✗"
                        short_vol_tag = f"Vol={vol_ratio:.1f}x ✓" if vol_ok else f"Vol={vol_ratio:.1f}x ✗"

                        if short_conds >= 2:
                            self.logger.info(
                                "[%s] SCALP SHORT — %d/3 conditions: %s, %s, %s",
                                self.pair, short_conds, short_rsi_tag, short_bb_tag, short_vol_tag,
                            )
                            entry_signal = ("short", f"Scalp SHORT {short_conds}/3: {short_rsi_tag}, {short_bb_tag}, {short_vol_tag}")

            # ── Execute entry ────────────────────────────────────────
            if entry_signal is not None:
                side, reason = entry_signal

                if side == "long":
                    sl = current_price * (1 - self.STOP_LOSS_PCT / 100)
                    tp = current_price * (1 + self.TAKE_PROFIT_PCT / 100)
                    signals.append(Signal(
                        side="buy",
                        price=current_price,
                        amount=amount,
                        order_type="market",
                        reason=reason,
                        strategy=self.name,
                        pair=self.pair,
                        stop_loss=sl,
                        take_profit=tp,
                        leverage=self.leverage if self.is_futures else 1,
                        position_type="long" if self.is_futures else "spot",
                        exchange_id="delta" if self.is_futures else "binance",
                        metadata={"pending_side": "long", "pending_amount": amount},
                    ))
                    # NOTE: position state set in on_fill(), NOT here
                    # This prevents phantom positions when orders fail

                elif side == "short":
                    sl = current_price * (1 + self.STOP_LOSS_PCT / 100)
                    tp = current_price * (1 - self.TAKE_PROFIT_PCT / 100)
                    signals.append(Signal(
                        side="sell",
                        price=current_price,
                        amount=amount,
                        order_type="market",
                        reason=reason,
                        strategy=self.name,
                        pair=self.pair,
                        stop_loss=sl,
                        take_profit=tp,
                        leverage=self.leverage,
                        position_type="short",
                        exchange_id="delta",
                        metadata={"pending_side": "short", "pending_amount": amount},
                    ))
                    # NOTE: position state set in on_fill(), NOT here

        return signals

    # -- Order fill / rejection callbacks -------------------------------------

    def on_fill(self, signal: Signal, order: dict) -> None:
        """Called by _run_loop when an order fills — NOW safe to track position."""
        pending_side = signal.metadata.get("pending_side")
        pending_amount = signal.metadata.get("pending_amount", 0.0)
        if pending_side:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or pending_amount or signal.amount
            self._open_position(pending_side, fill_price, filled_amount)
            self.logger.info(
                "[%s] Order FILLED — tracking %s position @ $%.2f, amount=%.8f",
                self.pair, pending_side, fill_price, filled_amount,
            )

    def on_rejected(self, signal: Signal) -> None:
        """Called by _run_loop when an order fails — do NOT track position."""
        pending_side = signal.metadata.get("pending_side")
        if pending_side:
            self.logger.warning(
                "[%s] Order FAILED — NOT tracking %s position (phantom prevention)",
                self.pair, pending_side,
            )
            # Ensure no stale position state
            # (check() did NOT call _open_position, so nothing to undo)

    # -- Position management ---------------------------------------------------

    def _open_position(self, side: str, price: float, amount: float = 0.0) -> None:
        self.in_position = True
        self.position_side = side
        self.entry_price = price
        self.entry_amount = amount
        self.entry_time = time.monotonic()
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self._positions_on_pair += 1
        self._hourly_trades.append(time.time())

    def _record_scalp_result(self, pnl_pct: float, exit_type: str) -> None:
        # Gross P&L = price change * notional (entry_amount is coin qty)
        notional = self.entry_price * self.entry_amount
        gross_pnl = notional * (pnl_pct / 100)

        # Estimate trading fees (entry + exit): ~0.05% per side on Delta, ~0.1% on Binance
        fee_rate = 0.001 if self._exchange_id == "delta" else 0.002  # round-trip
        est_fees = notional * fee_rate
        net_pnl = gross_pnl - est_fees

        # Capital P&L % (leveraged)
        capital_pnl_pct = pnl_pct * self.leverage

        self.hourly_pnl += net_pnl
        self._daily_scalp_loss += net_pnl if net_pnl < 0 else 0

        if pnl_pct >= 0:
            self.hourly_wins += 1
            self._consecutive_losses = 0
        else:
            self.hourly_losses += 1
            self._consecutive_losses += 1

        # Duration in human-readable format
        hold_sec = int(time.monotonic() - self.entry_time)
        if hold_sec >= 60:
            duration = f"{hold_sec // 60}m{hold_sec % 60:02d}s"
        else:
            duration = f"{hold_sec}s"

        self.logger.info(
            "[%s] SCALP CLOSED — %s hit %+.2f%% price (%+.2f%% capital at %dx) | "
            "Gross=$%.4f, Net=$%.4f (fees~$%.4f) | Duration: %s | "
            "W/L=%d/%d, streak=%d, daily=$%.4f",
            self.pair, exit_type.upper(), pnl_pct, capital_pnl_pct, self.leverage,
            gross_pnl, net_pnl, est_fees, duration,
            self.hourly_wins, self.hourly_losses,
            self._consecutive_losses, self._daily_scalp_loss,
        )

        if self._consecutive_losses >= self.CONSECUTIVE_LOSS_PAUSE:
            self._paused_until = time.monotonic() + self.PAUSE_DURATION_SEC
            self.logger.warning(
                "[%s] Scalp PAUSING %dmin — %d consecutive losses",
                self.pair, self.PAUSE_DURATION_SEC // 60, self._consecutive_losses,
            )

        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self.entry_amount = 0.0
        self._positions_on_pair = max(0, self._positions_on_pair - 1)

    def _exit_signal(self, price: float, side: str, reason: str) -> Signal:
        # Use the same amount as entry for consistent P&L tracking
        amount = self.entry_amount
        if amount <= 0:
            # Fallback: recalculate (shouldn't happen)
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            capital = exchange_capital * (self.capital_pct / 100)
            amount = capital / price
            if self.is_futures:
                amount *= self.leverage

        self.logger.debug(
            "[%s] Exit sizing: amount=%.8f (entry_amount), entry=$%.2f, exit=$%.2f",
            self.pair, amount, self.entry_price, price,
        )

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
        self._daily_scalp_loss = 0.0
        self._consecutive_losses = 0
        self._paused_until = 0.0
