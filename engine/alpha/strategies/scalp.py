"""Momentum scalping strategy — spot momentum, get in, get out.

Core principle: detect momentum, jump on it, ride it, exit fast.
Runs every 5 seconds. ONE strong condition is enough to enter.

Entry — Momentum detection:
  1. Price acceleration: current candle faster than last 3 avg
  2. Volume confirmation: current volume > 1.5x recent avg
  3. RSI extreme: RSI < 30 or > 70 = instant entry
  4. BB breakout: price breaks outside Bollinger Bands
  Direction: FOLLOW the momentum (not fade it)
    - Price up + volume = LONG
    - Price down + volume = SHORT

Exit — Get out fast:
  - TP: 1.5% (= 30% capital at 20x)
  - SL: 0.75% (= 15% capital at 20x, liq at ~5%)
  - Trailing: activate 0.80%, trail 0.40%
  - Profit lock: if +1.0%, move SL to breakeven
  - Timeout: 30 min max
  - Risk/reward: 2:1 — need 34% win rate to profit

Leverage: 20x on Delta futures
Position size: 2 contracts per trade ($2.08 collateral)
Max concurrent: 3 positions
Tick speed: 5 seconds
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
    """Momentum scalp — spot momentum, get in, get out.

    Detects momentum via price acceleration + volume, follows it.
    ONE strong signal is enough. 5-second ticks. 20x leverage on Delta.
    """

    name = StrategyName.SCALP
    check_interval_sec = 5  # 5 second ticks — fast momentum hunting

    # ── Exit thresholds (2:1 R/R) ─────────────────────────────────────────
    TAKE_PROFIT_PCT = 1.5         # 1.5% price move (30% capital at 20x)
    STOP_LOSS_PCT = 0.75          # 0.75% price move (15% capital at 20x)
    TRAILING_ACTIVATE_PCT = 0.80  # start trailing after 0.80%
    TRAILING_DISTANCE_PCT = 0.40  # trail at 0.40% from high/low
    PROFIT_LOCK_PCT = 1.0         # move SL to breakeven after +1.0%
    MAX_HOLD_SECONDS = 30 * 60    # 30 minutes max
    FLATLINE_SECONDS = 15 * 60    # close if flat for 15 min
    FLATLINE_MIN_MOVE_PCT = 0.1   # "flat" means < 0.1% total move

    # ── Momentum thresholds ──────────────────────────────────────────────
    RSI_EXTREME_LONG = 30         # instant long entry
    RSI_EXTREME_SHORT = 70        # instant short entry
    VOL_SPIKE_RATIO = 1.5         # volume > 1.5x average
    ACCEL_MIN_PCT = 0.05          # minimum candle move to count as momentum
    ACCEL_MULTIPLIER = 1.5        # current candle must be 1.5x avg of last 3

    # ── Position sizing ───────────────────────────────────────────────────
    CAPITAL_PCT_SPOT = 50.0       # 50% for spot (Binance $5 min)
    CAPITAL_PCT_FUTURES = 30.0    # 30% for futures (leverage handles the rest)
    TARGET_CONTRACTS = 2          # 2 contracts per trade
    MAX_CONTRACTS = 3             # hard cap per trade
    MAX_POSITIONS = 3             # max concurrent positions
    MAX_SPREAD_PCT = 0.15         # skip if spread > 0.15%

    # ── Rate limiting / risk ──────────────────────────────────────────────
    MAX_TRADES_PER_HOUR = 30
    CONSECUTIVE_LOSS_PAUSE = 5    # pause after 5 consecutive losses
    PAUSE_DURATION_SEC = 5 * 60   # 5 minutes pause (was 15 — get back faster)
    DAILY_LOSS_LIMIT_PCT = 5.0

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
        self.leverage: int = min(config.delta.leverage, 20) if is_futures else 1
        self.capital_pct: float = self.CAPITAL_PCT_FUTURES if is_futures else self.CAPITAL_PCT_SPOT
        self._exchange_id: str = "delta" if is_futures else "binance"

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_amount: float = 0.0
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")
        self._breakeven_locked: bool = False  # profit lock engaged

        # Rate limiting
        self._hourly_trades: list[float] = []
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
        # Don't reset position state — it may have been injected by _restore_strategy_state
        if not self.in_position:
            self.position_side = None
            self.entry_price = 0.0
            self.entry_amount = 0.0
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        tag = f"{self.leverage}x futures" if self.is_futures else "spot"
        pos_info = ""
        if self.in_position:
            pos_info = f" | RESTORED {self.position_side} @ ${self.entry_price:.2f}"
        self.logger.info(
            "[%s] Scalp ACTIVE (%s) — MOMENTUM-BASED, tick=%ds, "
            "TP=%.2f%% SL=%.2f%% Trail=%.2f/%.2f%% ProfitLock=%.1f%% Timeout=%dm%s",
            self.pair, tag, self.check_interval_sec,
            self.TAKE_PROFIT_PCT, self.STOP_LOSS_PCT,
            self.TRAILING_ACTIVATE_PCT, self.TRAILING_DISTANCE_PCT,
            self.PROFIT_LOCK_PCT, self.MAX_HOLD_SECONDS // 60,
            pos_info,
        )

    async def on_stop(self) -> None:
        self.logger.info(
            "[%s] Scalp stopped — %dW/%dL, P&L=$%.4f",
            self.pair, self.hourly_wins, self.hourly_losses, self.hourly_pnl,
        )

    # ======================================================================
    # MAIN CHECK LOOP
    # ======================================================================

    async def check(self) -> list[Signal]:
        """One scalping tick — fetch candles, detect momentum, manage exits."""
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.trade_exchange or self.executor.exchange
        now = time.monotonic()

        # ── Pause check (5 consecutive losses → 5 min cooldown) ──────────
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            if self._tick_count % 60 == 0:  # log every 5 min at 5s ticks
                self.logger.info(
                    "[%s] PAUSED (%d losses) — resuming in %ds",
                    self.pair, self._consecutive_losses, remaining,
                )
            return signals

        # ── Daily loss limit ─────────────────────────────────────────────
        exchange_cap = self.risk_manager.get_exchange_capital(self._exchange_id)
        if exchange_cap > 0 and self._daily_scalp_loss <= -(exchange_cap * self.DAILY_LOSS_LIMIT_PCT / 100):
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] STOPPED — daily loss limit $%.2f",
                    self.pair, self._daily_scalp_loss,
                )
            return signals

        # ── Rate limit ───────────────────────────────────────────────────
        cutoff = time.time() - 3600
        self._hourly_trades = [t for t in self._hourly_trades if t > cutoff]
        if len(self._hourly_trades) >= self.MAX_TRADES_PER_HOUR:
            return signals

        # ── Fetch 1m candles ─────────────────────────────────────────────
        ohlcv = await exchange.fetch_ohlcv(self.pair, "1m", limit=30)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # ── Compute indicators ───────────────────────────────────────────
        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
        rsi_now = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = float(bb.bollinger_hband().iloc[-1])
        bb_lower = float(bb.bollinger_lband().iloc[-1])

        # Volume ratio (current vs avg of last 10)
        avg_vol = float(volume.iloc[-11:-1].mean()) if len(volume) >= 11 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # Price acceleration: how fast is the current candle vs last 3?
        closes = close.values
        candle_changes: list[float] = []
        for i in range(-4, 0):
            if len(closes) >= abs(i) + 1:
                prev = float(closes[i - 1])
                cur = float(closes[i])
                candle_changes.append(((cur - prev) / prev * 100) if prev > 0 else 0)
        current_candle_pct = candle_changes[-1] if candle_changes else 0
        avg_candle_pct = (
            sum(abs(c) for c in candle_changes[:-1]) / max(len(candle_changes) - 1, 1)
        )

        # ── Heartbeat every 60 seconds ───────────────────────────────────
        if now - self._last_heartbeat >= 60:
            self._last_heartbeat = now
            tag = f"{self.leverage}x" if self.is_futures else "spot"
            if self.in_position:
                hold_sec = now - self.entry_time
                pnl_now = self._calc_pnl_pct(current_price)
                be_tag = " [BE-LOCKED]" if self._breakeven_locked else ""
                self.logger.info(
                    "[%s] (%s) %s @ $%.2f | %ds | PnL=%+.2f%% | RSI=%.1f%s",
                    self.pair, tag, self.position_side, self.entry_price,
                    int(hold_sec), pnl_now, rsi_now, be_tag,
                )
            else:
                self.logger.info(
                    "[%s] (%s) SCANNING | $%.2f | RSI=%.1f | Vol=%.1fx | "
                    "candle=%+.3f%% (avg=%.3f%%) | W/L=%d/%d",
                    self.pair, tag, current_price, rsi_now, vol_ratio,
                    current_candle_pct, avg_candle_pct,
                    self.hourly_wins, self.hourly_losses,
                )

        # ── In position: check exit ──────────────────────────────────────
        if self.in_position:
            return self._check_exits(current_price, rsi_now)

        # ── No position: detect momentum ─────────────────────────────────
        # Check position limits
        if self.risk_manager.has_position(self.pair):
            return signals

        total_scalp = sum(
            1 for p in self.risk_manager.open_positions
            if p.strategy == "scalp"
        )
        if total_scalp >= self.MAX_POSITIONS:
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

        # Balance check
        available = self.risk_manager.get_available_capital(self._exchange_id)
        min_balance = 5.50 if self._exchange_id == "binance" else 1.00
        if available < min_balance:
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] Insufficient %s balance: $%.2f",
                    self.pair, self._exchange_id, available,
                )
            return signals

        # Size the position
        amount = self._calculate_position_size(current_price, available)
        if amount is None:
            return signals

        # ── Detect momentum ──────────────────────────────────────────────
        entry = self._detect_momentum(
            current_price, rsi_now, vol_ratio,
            current_candle_pct, avg_candle_pct,
            bb_upper, bb_lower,
        )

        if entry is not None:
            side, reason = entry
            signals.append(self._build_entry_signal(side, current_price, amount, reason))

        return signals

    # ======================================================================
    # MOMENTUM DETECTION — one strong signal is enough
    # ======================================================================

    def _detect_momentum(
        self,
        price: float,
        rsi_now: float,
        vol_ratio: float,
        current_candle_pct: float,
        avg_candle_pct: float,
        bb_upper: float,
        bb_lower: float,
    ) -> tuple[str, str] | None:
        """Detect momentum. Returns (side, reason) or None.

        ONE strong condition triggers entry. Follow the momentum direction.
        """
        can_short = self.is_futures and config.delta.enable_shorting

        # ── 1. RSI Extreme — instant entry, strongest signal ─────────────
        if rsi_now < self.RSI_EXTREME_LONG:
            return ("long",
                    f"MOMENTUM: RSI extreme {rsi_now:.1f} — oversold bounce")
        if rsi_now > self.RSI_EXTREME_SHORT and can_short:
            return ("short",
                    f"MOMENTUM: RSI extreme {rsi_now:.1f} — overbought fade")

        # ── 2. Price acceleration + volume — core momentum signal ────────
        is_accelerating = (
            abs(current_candle_pct) >= self.ACCEL_MIN_PCT
            and avg_candle_pct > 0
            and abs(current_candle_pct) >= avg_candle_pct * self.ACCEL_MULTIPLIER
        )
        has_volume = vol_ratio >= self.VOL_SPIKE_RATIO

        if is_accelerating and has_volume:
            if current_candle_pct > 0:
                return ("long",
                        f"MOMENTUM: accel {current_candle_pct:+.3f}% "
                        f"({self.ACCEL_MULTIPLIER}x avg {avg_candle_pct:.3f}%), "
                        f"Vol {vol_ratio:.1f}x")
            elif can_short:
                return ("short",
                        f"MOMENTUM: accel {current_candle_pct:+.3f}% "
                        f"({self.ACCEL_MULTIPLIER}x avg {avg_candle_pct:.3f}%), "
                        f"Vol {vol_ratio:.1f}x")

        # ── 3. Price acceleration alone (strong move) ────────────────────
        strong_accel = (
            abs(current_candle_pct) >= self.ACCEL_MIN_PCT * 2  # 0.10% min
            and avg_candle_pct > 0
            and abs(current_candle_pct) >= avg_candle_pct * 2.0  # 2x avg
        )
        if strong_accel:
            if current_candle_pct > 0:
                return ("long",
                        f"MOMENTUM: strong accel {current_candle_pct:+.3f}% "
                        f"(2x avg {avg_candle_pct:.3f}%)")
            elif can_short:
                return ("short",
                        f"MOMENTUM: strong accel {current_candle_pct:+.3f}% "
                        f"(2x avg {avg_candle_pct:.3f}%)")

        # ── 4. Volume spike alone (big volume = something happening) ─────
        if vol_ratio >= 2.0 and abs(current_candle_pct) >= self.ACCEL_MIN_PCT:
            if current_candle_pct > 0:
                return ("long",
                        f"MOMENTUM: volume spike {vol_ratio:.1f}x, "
                        f"candle {current_candle_pct:+.3f}%")
            elif can_short:
                return ("short",
                        f"MOMENTUM: volume spike {vol_ratio:.1f}x, "
                        f"candle {current_candle_pct:+.3f}%")

        # ── 5. BB Breakout — price outside bands = momentum confirmed ────
        if price > bb_upper:
            return ("long",
                    f"MOMENTUM: BB breakout (${price:.2f} > upper ${bb_upper:.2f})")
        if price < bb_lower and can_short:
            return ("short",
                    f"MOMENTUM: BB breakdown (${price:.2f} < lower ${bb_lower:.2f})")

        return None

    # ======================================================================
    # EXIT LOGIC — get out fast
    # ======================================================================

    def _check_exits(self, current_price: float, rsi_now: float) -> list[Signal]:
        """Check all exit conditions. Priority: TP > SL/Trail > Profit lock > Timeout > Flatline."""
        signals: list[Signal] = []
        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)

        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)

            # Determine stop level
            if self._breakeven_locked:
                # Profit locked: SL at breakeven (entry price)
                trail_stop = max(
                    self.entry_price,
                    self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100),
                ) if pnl_pct >= self.TRAILING_ACTIVATE_PCT else self.entry_price
            elif pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                trail_stop = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
            else:
                trail_stop = self.entry_price * (1 - self.STOP_LOSS_PCT / 100)

            tp_price = self.entry_price * (1 + self.TAKE_PROFIT_PCT / 100)

            # Profit lock check
            if not self._breakeven_locked and pnl_pct >= self.PROFIT_LOCK_PCT:
                self._breakeven_locked = True
                self.logger.info(
                    "[%s] PROFIT LOCK — PnL +%.2f%% → SL moved to breakeven $%.2f",
                    self.pair, pnl_pct, self.entry_price,
                )

            # Exit checks (priority order)
            if current_price >= tp_price:
                return self._do_exit(current_price, pnl_pct, "long", "TP", hold_seconds)
            if current_price <= trail_stop:
                exit_type = "TRAIL" if pnl_pct >= 0 else "SL"
                if self._breakeven_locked and pnl_pct >= 0:
                    exit_type = "BE-LOCK"
                return self._do_exit(current_price, pnl_pct, "long", exit_type, hold_seconds)
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "long", "TIMEOUT", hold_seconds)
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "long", "FLAT", hold_seconds)

        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

            # Determine stop level
            if self._breakeven_locked:
                trail_stop = min(
                    self.entry_price,
                    self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100),
                ) if pnl_pct >= self.TRAILING_ACTIVATE_PCT else self.entry_price
            elif pnl_pct >= self.TRAILING_ACTIVATE_PCT:
                trail_stop = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
            else:
                trail_stop = self.entry_price * (1 + self.STOP_LOSS_PCT / 100)

            tp_price = self.entry_price * (1 - self.TAKE_PROFIT_PCT / 100)

            # Profit lock check
            if not self._breakeven_locked and pnl_pct >= self.PROFIT_LOCK_PCT:
                self._breakeven_locked = True
                self.logger.info(
                    "[%s] PROFIT LOCK — PnL +%.2f%% → SL moved to breakeven $%.2f",
                    self.pair, pnl_pct, self.entry_price,
                )

            # Exit checks (priority order)
            if current_price <= tp_price:
                return self._do_exit(current_price, pnl_pct, "short", "TP", hold_seconds)
            if current_price >= trail_stop:
                exit_type = "TRAIL" if pnl_pct >= 0 else "SL"
                if self._breakeven_locked and pnl_pct >= 0:
                    exit_type = "BE-LOCK"
                return self._do_exit(current_price, pnl_pct, "short", exit_type, hold_seconds)
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "short", "TIMEOUT", hold_seconds)
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "short", "FLAT", hold_seconds)

        return signals

    def _do_exit(
        self, price: float, pnl_pct: float, side: str,
        exit_type: str, hold_seconds: float,
    ) -> list[Signal]:
        """Execute an exit: build signal, record result, log."""
        cap_pct = pnl_pct * self.leverage
        reason = (
            f"Scalp {exit_type} {pnl_pct:+.2f}% price "
            f"({cap_pct:+.1f}% capital at {self.leverage}x)"
        )
        self._record_scalp_result(pnl_pct, exit_type.lower())
        return [self._exit_signal(price, side, reason)]

    def _calc_pnl_pct(self, current_price: float) -> float:
        """Calculate unrealized P&L percentage."""
        if self.entry_price <= 0:
            return 0.0
        if self.position_side == "long":
            return ((current_price - self.entry_price) / self.entry_price) * 100
        elif self.position_side == "short":
            return ((self.entry_price - current_price) / self.entry_price) * 100
        return 0.0

    # ======================================================================
    # POSITION SIZING — 2 contracts target, 3 max
    # ======================================================================

    def _calculate_position_size(self, current_price: float, available: float) -> float | None:
        """Calculate position amount in coin terms. Returns None if can't size."""
        exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
        capital = exchange_capital * (self.capital_pct / 100)
        capital = min(capital, available)

        if self.is_futures:
            from alpha.trade_executor import DELTA_CONTRACT_SIZE
            contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0)
            if contract_size <= 0:
                self.logger.warning("[%s] Unknown Delta contract size — skipping", self.pair)
                return None

            one_contract_collateral = (contract_size * current_price) / self.leverage
            if one_contract_collateral > available:
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] 1 contract needs $%.2f collateral > $%.2f — skipping",
                        self.pair, one_contract_collateral, available,
                    )
                return None

            # Target 2 contracts, max 3
            contracts = self.TARGET_CONTRACTS
            total_collateral = contracts * one_contract_collateral
            if total_collateral > available:
                contracts = max(1, int(available / one_contract_collateral))
            contracts = min(contracts, self.MAX_CONTRACTS)
            total_collateral = contracts * one_contract_collateral
            amount = contracts * contract_size

            self.logger.debug(
                "[%s] Sizing: %d contracts × %.4f = %.6f coin, "
                "collateral=$%.2f (%dx)",
                self.pair, contracts, contract_size, amount,
                total_collateral, self.leverage,
            )
        else:
            amount = capital / current_price
            self.logger.debug(
                "[%s] Sizing (spot): $%.2f → %.8f",
                self.pair, capital, amount,
            )

        return amount

    # ======================================================================
    # SIGNAL BUILDERS
    # ======================================================================

    def _build_entry_signal(self, side: str, price: float, amount: float, reason: str) -> Signal:
        """Build an entry signal for detected momentum."""
        self.logger.info("[%s] %s → %s entry", self.pair, reason, side.upper())

        if side == "long":
            sl = price * (1 - self.STOP_LOSS_PCT / 100)
            tp = price * (1 + self.TAKE_PROFIT_PCT / 100)
            return Signal(
                side="buy",
                price=price,
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
            )
        else:  # short
            sl = price * (1 + self.STOP_LOSS_PCT / 100)
            tp = price * (1 - self.TAKE_PROFIT_PCT / 100)
            return Signal(
                side="sell",
                price=price,
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
            )

    def _exit_signal(self, price: float, side: str, reason: str) -> Signal:
        """Build an exit signal for the current position."""
        amount = self.entry_amount
        if amount <= 0:
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            capital = exchange_capital * (self.capital_pct / 100)
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

    # ======================================================================
    # ORDER FILL / REJECTION CALLBACKS
    # ======================================================================

    def on_fill(self, signal: Signal, order: dict) -> None:
        """Called by _run_loop when an order fills — NOW safe to track position."""
        pending_side = signal.metadata.get("pending_side")
        pending_amount = signal.metadata.get("pending_amount", 0.0)
        if pending_side:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or pending_amount or signal.amount
            self._open_position(pending_side, fill_price, filled_amount)
            self.logger.info(
                "[%s] FILLED — %s @ $%.2f, %.6f, %dx",
                self.pair, pending_side.upper(), fill_price, filled_amount, self.leverage,
            )

    def on_rejected(self, signal: Signal) -> None:
        """Called by _run_loop when an order fails — do NOT track position."""
        pending_side = signal.metadata.get("pending_side")
        if pending_side:
            self.logger.warning(
                "[%s] REJECTED — NOT tracking %s (phantom prevention)",
                self.pair, pending_side,
            )

    # ======================================================================
    # POSITION MANAGEMENT
    # ======================================================================

    def _open_position(self, side: str, price: float, amount: float = 0.0) -> None:
        self.in_position = True
        self.position_side = side
        self.entry_price = price
        self.entry_amount = amount
        self.entry_time = time.monotonic()
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self._breakeven_locked = False
        self._hourly_trades.append(time.time())

    def _record_scalp_result(self, pnl_pct: float, exit_type: str) -> None:
        # Gross P&L
        notional = self.entry_price * self.entry_amount
        gross_pnl = notional * (pnl_pct / 100)

        # Fees: ~0.05% per side on Delta, ~0.1% on Binance
        fee_rate = 0.001 if self._exchange_id == "delta" else 0.002
        est_fees = notional * fee_rate
        net_pnl = gross_pnl - est_fees

        capital_pnl_pct = pnl_pct * self.leverage

        self.hourly_pnl += net_pnl
        self._daily_scalp_loss += net_pnl if net_pnl < 0 else 0

        if pnl_pct >= 0:
            self.hourly_wins += 1
            self._consecutive_losses = 0
        else:
            self.hourly_losses += 1
            self._consecutive_losses += 1

        # Duration
        hold_sec = int(time.monotonic() - self.entry_time)
        duration = f"{hold_sec // 60}m{hold_sec % 60:02d}s" if hold_sec >= 60 else f"{hold_sec}s"

        self.logger.info(
            "[%s] CLOSED %s %+.2f%% price (%+.1f%% capital at %dx) | "
            "Gross=$%.4f Net=$%.4f fees=$%.4f | %s | W/L=%d/%d streak=%d",
            self.pair, exit_type.upper(), pnl_pct, capital_pnl_pct, self.leverage,
            gross_pnl, net_pnl, est_fees, duration,
            self.hourly_wins, self.hourly_losses, self._consecutive_losses,
        )

        if self._consecutive_losses >= self.CONSECUTIVE_LOSS_PAUSE:
            self._paused_until = time.monotonic() + self.PAUSE_DURATION_SEC
            self.logger.warning(
                "[%s] PAUSING %ds — %d consecutive losses",
                self.pair, self.PAUSE_DURATION_SEC, self._consecutive_losses,
            )

        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self.entry_amount = 0.0
        self._breakeven_locked = False

    # ======================================================================
    # STATS
    # ======================================================================

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
