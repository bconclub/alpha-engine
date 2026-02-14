"""Alpha v2.2 — MOMENTUM RIDER: Enter fast, ride the wave, trail profits.

NEVER IDLE. ZERO COOLDOWN. Every tick without a position is wasted money.
Runs every 3 seconds. ANY SINGLE condition triggers entry.

Entry — see ANY movement? GET IN:
  1. 30s momentum > 0.05% → enter direction
  2. RSI < 45 (long) or > 55 (short) → slight imbalance = opportunity
  3. Volume > 1.2x average → enter candle direction
  4. BB: price within 0.3% of any band → enter direction
  5. Price acceleration > 1.2x avg → enter direction
  If NONE trigger for 2 minutes → force entry on EMA slope

Exit — RIDE THE MOMENTUM, DON'T EXIT EARLY:
  NO fixed TP anymore. Let winners run.
  Exit ONLY when:
    1. Signal reversal — momentum flips (RSI cross 70/30, momentum turns)
    2. Trailing stop — activates at +0.30%, trails 0.20% behind peak
    3. Stop loss — 0.50% (cut losers fast)
    4. Timeout — 30 min max (free capital if nothing happening)

Trailing Stop System:
  - Activate after +0.30% profit
  - Trail at 0.20% behind peak (highest for longs, lowest for shorts)
  - Trail only moves in profitable direction, never backward
  - Example: long entry $2080 → peak $2095 (+0.72%) → trail at $2090.81
    → price drops to trail → exit +0.52% (rode 72% of the move)

Soul Integration:
  - Loads SOUL.md before every trade decision
  - Logs soul-guided reasoning on every exit check
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt
import pandas as pd
import ta

# IST timezone offset (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName
from alpha.utils import setup_logger

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor

logger = setup_logger("scalp")


# ══════════════════════════════════════════════════════════════════════
# SOUL LOADER — read principles before every decision
# ══════════════════════════════════════════════════════════════════════

_SOUL_PRINCIPLES: list[str] = []
_SOUL_LOADED: bool = False


def _load_soul() -> list[str]:
    """Load soul principles from SOUL.md. Cached after first load."""
    global _SOUL_PRINCIPLES, _SOUL_LOADED
    if _SOUL_LOADED:
        return _SOUL_PRINCIPLES

    soul_path = Path(__file__).resolve().parent.parent.parent / "SOUL.md"
    try:
        text = soul_path.read_text(encoding="utf-8")
        # Extract key principles (lines starting with - or numbered)
        principles = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or (stripped and stripped[0].isdigit() and "**" in stripped):
                # Clean markdown
                clean = stripped.lstrip("- ").lstrip("0123456789. ")
                clean = clean.replace("**", "")
                if clean:
                    principles.append(clean)
        _SOUL_PRINCIPLES = principles
        _SOUL_LOADED = True
        logger.info("Soul loaded: %d principles from %s", len(principles), soul_path)
    except Exception as e:
        logger.warning("Could not load SOUL.md: %s", e)
        _SOUL_PRINCIPLES = [
            "Ride momentum until it dies. Don't exit at a fixed number.",
            "Let winners run and cut losers fast.",
            "A good trade catches 60-80% of the move, not just 1%.",
        ]
        _SOUL_LOADED = True
    return _SOUL_PRINCIPLES


def _soul_check(context: str) -> str:
    """Get relevant soul principle for the current decision context."""
    principles = _load_soul()
    # Match context to most relevant principle
    context_lower = context.lower()
    if "exit" in context_lower or "tp" in context_lower or "take profit" in context_lower:
        for p in principles:
            if "ride" in p.lower() or "winner" in p.lower() or "fixed" in p.lower():
                return p
    if "loss" in context_lower or "sl" in context_lower or "stop" in context_lower:
        for p in principles:
            if "cut" in p.lower() or "loss" in p.lower():
                return p
    if "idle" in context_lower or "hunt" in context_lower:
        for p in principles:
            if "idle" in p.lower() or "hunt" in p.lower():
                return p
    if "momentum" in context_lower:
        for p in principles:
            if "momentum" in p.lower():
                return p
    # Default
    return principles[0] if principles else "Trade with conviction."


class ScalpStrategy(BaseStrategy):
    """Momentum Rider — enter fast, ride the wave, trail profits.

    Enters on the slightest momentum. Stays in as long as momentum continues.
    No fixed TP. Trail profits. Cut losses fast. Read the soul.
    """

    name = StrategyName.SCALP
    check_interval_sec = 3  # 3 second ticks — aggressive

    # ── Exit thresholds — RIDE THE MOMENTUM ────────────────────────────
    # NO FIXED TP — we trail profits instead
    STOP_LOSS_PCT = 0.50              # 0.50% price SL (10% capital at 20x) — cut fast
    TRAILING_ACTIVATE_PCT = 0.30      # activate trail after +0.30% profit
    TRAILING_DISTANCE_PCT = 0.20      # trail 0.20% behind peak
    MAX_HOLD_SECONDS = 30 * 60        # 30 min max — free capital if flat
    FLATLINE_SECONDS = 15 * 60        # 15 min flat = exit
    FLATLINE_MIN_MOVE_PCT = 0.05      # "flat" means < 0.05% total move

    # ── Signal reversal thresholds ──────────────────────────────────────
    RSI_REVERSAL_LONG = 70            # RSI > 70 while long → overbought, exit
    RSI_REVERSAL_SHORT = 30           # RSI < 30 while short → oversold, exit
    MOMENTUM_REVERSAL_PCT = -0.03     # momentum turns against position → exit

    # ── Momentum thresholds (ULTRA-AGGRESSIVE) ───────────────────────────
    RSI_EXTREME_LONG = 45             # RSI < 45 = slight oversold → long
    RSI_EXTREME_SHORT = 55            # RSI > 55 = slight overbought → short
    VOL_SPIKE_RATIO = 1.2             # volume > 1.2x average = above normal
    ACCEL_MIN_PCT = 0.02              # minimum candle move for momentum
    ACCEL_MULTIPLIER = 1.2            # current candle 1.2x avg → momentum
    MOMENTUM_WINDOW_PCT = 0.05        # 0.05% move in 30s = momentum
    BB_PROXIMITY_PCT = 0.30           # enter if within 0.3% of any BB band

    # ── Position sizing ───────────────────────────────────────────────────
    CAPITAL_PCT_SPOT = 50.0           # unused in Delta-only mode
    CAPITAL_PCT_FUTURES = 80.0        # 80% of exchange capital (aggressive)
    MAX_CONTRACTS = 5                 # hard cap per trade
    MAX_POSITIONS = 3                 # max 3 concurrent
    MAX_SPREAD_PCT = 0.15             # skip if spread > 0.15%

    # ── Rate limiting / risk ──────────────────────────────────────────────
    MAX_TRADES_PER_HOUR = 40
    CONSECUTIVE_LOSS_PAUSE = 5        # pause after 5 consecutive losses
    PAUSE_DURATION_SEC = 60           # 1 minute pause — get back FAST
    DAILY_LOSS_LIMIT_PCT = 5.0

    # ── Daily expiry (Delta India) ──────────────────────────────────────
    EXPIRY_HOUR_IST = 17
    EXPIRY_MINUTE_IST = 30
    NO_NEW_ENTRY_MINUTES = 30
    FORCE_CLOSE_MINUTES = 5

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
        self._trailing_active: bool = False  # trail activated

        # Previous RSI for reversal detection
        self._prev_rsi: float = 50.0

        # Rate limiting
        self._hourly_trades: list[float] = []
        self._consecutive_losses: int = 0
        self._paused_until: float = 0.0
        self._daily_scalp_loss: float = 0.0

        # Idle tracking — force entry if idle too long
        self._last_position_exit: float = 0.0
        self.FORCE_ENTRY_AFTER_SEC = 2 * 60     # force entry after 2 min idle

        # Stats for hourly summary
        self.hourly_wins: int = 0
        self.hourly_losses: int = 0
        self.hourly_pnl: float = 0.0

        # Tick tracking
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0

        # Load soul on init
        _load_soul()

    async def on_start(self) -> None:
        # Don't reset position state — it may have been injected by _restore_strategy_state
        if not self.in_position:
            self.position_side = None
            self.entry_price = 0.0
            self.entry_amount = 0.0
        self._tick_count = 0
        self._last_heartbeat = time.monotonic()
        self._last_position_exit = time.monotonic()
        tag = f"{self.leverage}x futures" if self.is_futures else "spot"
        pos_info = ""
        if self.in_position:
            pos_info = f" | RESTORED {self.position_side} @ ${self.entry_price:.2f}"
        soul_msg = _soul_check("momentum")
        self.logger.info(
            "[%s] Scalp ACTIVE (%s) — MOMENTUM RIDER v2.2, tick=%ds, "
            "NO FIXED TP, Trail=%.2f%%/%.2f%% SL=%.2f%% "
            "Mom=%.2f%% RSI=%d/%d Vol=%.1fx BB=%.1f%% "
            "ForceEntry=%ds Timeout=%dm%s",
            self.pair, tag, self.check_interval_sec,
            self.TRAILING_ACTIVATE_PCT, self.TRAILING_DISTANCE_PCT,
            self.STOP_LOSS_PCT,
            self.MOMENTUM_WINDOW_PCT, self.RSI_EXTREME_LONG, self.RSI_EXTREME_SHORT,
            self.VOL_SPIKE_RATIO, self.BB_PROXIMITY_PCT,
            self.FORCE_ENTRY_AFTER_SEC, self.MAX_HOLD_SECONDS // 60,
            pos_info,
        )
        self.logger.info("[%s] Soul: %s", self.pair, soul_msg)

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

        # ── Pause check (5 consecutive losses → 1 min cooldown) ────────
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] PAUSED (%d losses) — resuming in %ds",
                    self.pair, self._consecutive_losses, remaining,
                )
            return signals

        # ── Daily expiry check (5:30 PM IST) ───────────────────────────
        _expiry_no_new = False
        _expiry_force_close = False
        _mins_to_expiry = 999.0
        if self.is_futures:
            _expiry_no_new, _expiry_force_close, _mins_to_expiry = self._is_near_expiry()
            if _expiry_no_new and not self.in_position:
                if self._tick_count % 20 == 0:
                    self.logger.info(
                        "[%s] EXPIRY in %.0f min — no new entries", self.pair, _mins_to_expiry,
                    )
                return signals

        # ── Daily loss limit ───────────────────────────────────────────
        exchange_cap = self.risk_manager.get_exchange_capital(self._exchange_id)
        if exchange_cap > 0 and self._daily_scalp_loss <= -(exchange_cap * self.DAILY_LOSS_LIMIT_PCT / 100):
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] STOPPED — daily loss limit $%.2f",
                    self.pair, self._daily_scalp_loss,
                )
            return signals

        # ── Rate limit ─────────────────────────────────────────────────
        cutoff = time.time() - 3600
        self._hourly_trades = [t for t in self._hourly_trades if t > cutoff]
        if len(self._hourly_trades) >= self.MAX_TRADES_PER_HOUR:
            return signals

        # ── Fetch 1m candles ───────────────────────────────────────────
        ohlcv = await exchange.fetch_ohlcv(self.pair, "1m", limit=30)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # ── Compute indicators ─────────────────────────────────────────
        rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
        rsi_now = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = float(bb.bollinger_hband().iloc[-1])
        bb_lower = float(bb.bollinger_lband().iloc[-1])

        # Volume ratio
        avg_vol = float(volume.iloc[-11:-1].mean()) if len(volume) >= 11 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # Price acceleration
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

        # 30-second momentum
        price_prev = float(close.iloc[-2]) if len(close) >= 2 else current_price
        momentum_30s = ((current_price - price_prev) / price_prev * 100) if price_prev > 0 else 0

        # BB proximity
        bb_dist_upper = ((bb_upper - current_price) / current_price * 100) if current_price > 0 else 999
        bb_dist_lower = ((current_price - bb_lower) / current_price * 100) if current_price > 0 else 999

        # EMA slope for forced entry
        ema_5 = float(close.ewm(span=5, adjust=False).mean().iloc[-1])
        ema_5_prev = float(close.ewm(span=5, adjust=False).mean().iloc[-2]) if len(close) >= 2 else ema_5
        ema_slope = "up" if ema_5 > ema_5_prev else "down"

        # ── Heartbeat every 60 seconds ─────────────────────────────────
        if now - self._last_heartbeat >= 60:
            self._last_heartbeat = now
            tag = f"{self.leverage}x" if self.is_futures else "spot"
            if self.in_position:
                hold_sec = now - self.entry_time
                pnl_now = self._calc_pnl_pct(current_price)
                trail_tag = " [TRAILING]" if self._trailing_active else ""
                self.logger.info(
                    "[%s] (%s) %s @ $%.2f | %ds | PnL=%+.2f%% | RSI=%.1f | mom=%+.3f%%%s",
                    self.pair, tag, self.position_side, self.entry_price,
                    int(hold_sec), pnl_now, rsi_now, momentum_30s, trail_tag,
                )
            else:
                self.logger.info(
                    "[%s] (%s) SCANNING | $%.2f | RSI=%.1f | Vol=%.1fx | "
                    "candle=%+.3f%% | W/L=%d/%d",
                    self.pair, tag, current_price, rsi_now, vol_ratio,
                    current_candle_pct,
                    self.hourly_wins, self.hourly_losses,
                )

        # ── In position: check exit (SOUL-GUIDED) ─────────────────────
        if self.in_position:
            # Force close before daily expiry
            if _expiry_force_close:
                pnl_pct = self._calc_pnl_pct(current_price)
                self.logger.warning(
                    "[%s] EXPIRY in %.1f min — FORCE CLOSING %s @ $%.2f (PnL=%+.2f%%)",
                    self.pair, _mins_to_expiry, self.position_side, current_price, pnl_pct,
                )
                return self._do_exit(
                    current_price, pnl_pct, self.position_side or "long",
                    "EXPIRY", time.monotonic() - self.entry_time,
                )
            result = self._check_exits(current_price, rsi_now, momentum_30s)
            self._prev_rsi = rsi_now
            return result

        # ── No position: detect momentum ───────────────────────────────
        self._prev_rsi = rsi_now  # track RSI for reversal detection

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

        # ── Detect momentum ────────────────────────────────────────────
        idle_seconds = int(now - self._last_position_exit)
        entry = self._detect_momentum(
            current_price, rsi_now, vol_ratio,
            current_candle_pct, avg_candle_pct,
            bb_upper, bb_lower, momentum_30s,
            bb_dist_upper, bb_dist_lower,
        )

        if entry is not None:
            side, reason = entry
            soul_msg = _soul_check("entry momentum")
            self.logger.info("[%s] Soul check: %s", self.pair, soul_msg)
            signals.append(self._build_entry_signal(side, current_price, amount, reason))
        elif idle_seconds >= self.FORCE_ENTRY_AFTER_SEC:
            # IDLE TOO LONG — force entry in dominant direction
            side = "long" if ema_slope == "up" else "short"
            can_short = self.is_futures and config.delta.enable_shorting
            if side == "short" and not can_short:
                side = "long"
            reason = (
                f"IDLE {idle_seconds}s — forcing {side.upper()} based on "
                f"EMA slope ({ema_slope}), momentum={momentum_30s:+.3f}%"
            )
            soul_msg = _soul_check("idle hunting")
            self.logger.info("[%s] %s | Soul: %s", self.pair, reason, soul_msg)
            signals.append(self._build_entry_signal(side, current_price, amount, reason))
        else:
            # Log EVERY TICK while hunting
            self.logger.info(
                "[%s] HUNT %ds | $%.2f | m30s=%+.3f%%/%.2f | RSI=%.1f/%d/%d | "
                "vol=%.1fx/%.1f | accel=%+.3f%%/%.3f×%.1f | BB↑%.2f%%/↓%.2f%%/%.1f%% | EMA=%s",
                self.pair, idle_seconds, current_price,
                momentum_30s, self.MOMENTUM_WINDOW_PCT,
                rsi_now, self.RSI_EXTREME_LONG, self.RSI_EXTREME_SHORT,
                vol_ratio, self.VOL_SPIKE_RATIO,
                current_candle_pct, avg_candle_pct, self.ACCEL_MULTIPLIER,
                bb_dist_upper, bb_dist_lower, self.BB_PROXIMITY_PCT,
                ema_slope,
            )

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
        momentum_30s: float = 0.0,
        bb_dist_upper: float = 999.0,
        bb_dist_lower: float = 999.0,
    ) -> tuple[str, str] | None:
        """Detect ANY momentum. Returns (side, reason) or None."""
        can_short = self.is_futures and config.delta.enable_shorting

        # ── 1. 30-second momentum ─────────────────────────────────────
        if abs(momentum_30s) >= self.MOMENTUM_WINDOW_PCT:
            if momentum_30s > 0:
                return ("long", f"MOM: {momentum_30s:+.3f}% in 30s → LONG")
            elif can_short:
                return ("short", f"MOM: {momentum_30s:+.3f}% in 30s → SHORT")

        # ── 2. RSI imbalance ───────────────────────────────────────────
        if rsi_now < self.RSI_EXTREME_LONG:
            return ("long", f"RSI: {rsi_now:.1f} < {self.RSI_EXTREME_LONG} → LONG")
        if rsi_now > self.RSI_EXTREME_SHORT and can_short:
            return ("short", f"RSI: {rsi_now:.1f} > {self.RSI_EXTREME_SHORT} → SHORT")

        # ── 3. Volume above average ───────────────────────────────────
        if vol_ratio >= self.VOL_SPIKE_RATIO and abs(current_candle_pct) >= self.ACCEL_MIN_PCT:
            if current_candle_pct > 0:
                return ("long", f"VOL: {vol_ratio:.1f}x, candle {current_candle_pct:+.3f}% → LONG")
            elif can_short:
                return ("short", f"VOL: {vol_ratio:.1f}x, candle {current_candle_pct:+.3f}% → SHORT")

        # ── 4. BB proximity ───────────────────────────────────────────
        if bb_dist_upper <= self.BB_PROXIMITY_PCT:
            return ("long", f"BB: {bb_dist_upper:.2f}% from upper → breakout LONG")
        if bb_dist_lower <= self.BB_PROXIMITY_PCT and can_short:
            return ("short", f"BB: {bb_dist_lower:.2f}% from lower → breakdown SHORT")
        if price > bb_upper:
            return ("long", f"BB: breakout ${price:.2f} > ${bb_upper:.2f} → LONG")
        if price < bb_lower and can_short:
            return ("short", f"BB: breakdown ${price:.2f} < ${bb_lower:.2f} → SHORT")

        # ── 5. Price acceleration ─────────────────────────────────────
        if (abs(current_candle_pct) >= self.ACCEL_MIN_PCT
                and avg_candle_pct > 0
                and abs(current_candle_pct) >= avg_candle_pct * self.ACCEL_MULTIPLIER):
            if current_candle_pct > 0:
                return ("long",
                        f"ACCEL: {current_candle_pct:+.3f}% "
                        f"({abs(current_candle_pct)/avg_candle_pct:.1f}x avg) → LONG")
            elif can_short:
                return ("short",
                        f"ACCEL: {current_candle_pct:+.3f}% "
                        f"({abs(current_candle_pct)/avg_candle_pct:.1f}x avg) → SHORT")

        return None

    # ======================================================================
    # EXIT LOGIC — RIDE MOMENTUM, TRAIL PROFITS
    # ======================================================================

    def _check_exits(self, current_price: float, rsi_now: float, momentum_30s: float) -> list[Signal]:
        """Check exit conditions. Soul-guided: ride momentum, trail profits.

        Priority:
        1. Signal reversal (RSI cross / momentum flip) — catch the top/bottom
        2. Trailing stop — protect profits that were made
        3. Stop loss — cut losers fast (0.50%)
        4. Timeout — 30 min, free capital
        5. Flatline — no movement for 15 min
        """
        signals: list[Signal] = []
        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)

        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)

            # ── 1. SIGNAL REVERSAL — momentum died, exit at the top ────
            rsi_crossed_70 = rsi_now > self.RSI_REVERSAL_LONG and self._prev_rsi <= self.RSI_REVERSAL_LONG
            momentum_reversed = momentum_30s < self.MOMENTUM_REVERSAL_PCT and pnl_pct > 0

            if rsi_crossed_70 and pnl_pct > 0:
                soul_msg = _soul_check("exit reversal")
                self.logger.info(
                    "[%s] Soul check: RSI %.1f crossed 70 — momentum dying, exit now | %s",
                    self.pair, rsi_now, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-RSI", hold_seconds)

            if momentum_reversed:
                soul_msg = _soul_check("exit reversal")
                self.logger.info(
                    "[%s] Soul check: momentum flipped %+.3f%% — exit with profit | %s",
                    self.pair, momentum_30s, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-MOM", hold_seconds)

            # ── 2. TRAILING STOP — protect profits ─────────────────────
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                trail_price = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ACTIVATED at +%.2f%% — trailing SL at $%.2f (%.2f%% behind peak $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self.TRAILING_DISTANCE_PCT,
                    self.highest_since_entry, soul_msg,
                )

            if self._trailing_active:
                trail_stop = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                if current_price <= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] Soul check: trail stop hit at $%.2f (peak was $%.2f) | %s",
                        self.pair, trail_stop, self.highest_since_entry, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "TRAIL", hold_seconds)
                # Log trail status on heartbeats
                if self._tick_count % 20 == 0:
                    dist_from_peak = ((self.highest_since_entry - current_price) / self.highest_since_entry * 100)
                    self.logger.info(
                        "[%s] Soul check: momentum still positive, holding | "
                        "PnL=%+.2f%% peak=$%.2f trail=$%.2f dist=%.2f%%",
                        self.pair, pnl_pct, self.highest_since_entry, trail_stop, dist_from_peak,
                    )
            else:
                # Not yet trailing — check soul: should we hold or cut?
                if pnl_pct > 0 and self._tick_count % 20 == 0:
                    soul_msg = _soul_check("momentum")
                    self.logger.info(
                        "[%s] Soul check: momentum still positive +%.2f%%, riding | %s",
                        self.pair, pnl_pct, soul_msg,
                    )

            # ── 3. STOP LOSS — cut losers fast ─────────────────────────
            sl_price = self.entry_price * (1 - self.STOP_LOSS_PCT / 100)
            if current_price <= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] Soul check: loss hit %.2f%%, cutting fast | %s",
                    self.pair, pnl_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "long", "SL", hold_seconds)

            # ── 4. TIMEOUT — 30 min ───────────────────────────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "long", "TIMEOUT", hold_seconds)

            # ── 5. FLATLINE — 15 min with < 0.05% move ────────────────
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "long", "FLAT", hold_seconds)

        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

            # ── 1. SIGNAL REVERSAL ─────────────────────────────────────
            rsi_crossed_30 = rsi_now < self.RSI_REVERSAL_SHORT and self._prev_rsi >= self.RSI_REVERSAL_SHORT
            momentum_reversed = momentum_30s > abs(self.MOMENTUM_REVERSAL_PCT) and pnl_pct > 0

            if rsi_crossed_30 and pnl_pct > 0:
                soul_msg = _soul_check("exit reversal")
                self.logger.info(
                    "[%s] Soul check: RSI %.1f crossed below 30 — oversold, exit short | %s",
                    self.pair, rsi_now, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-RSI", hold_seconds)

            if momentum_reversed:
                soul_msg = _soul_check("exit reversal")
                self.logger.info(
                    "[%s] Soul check: momentum flipped +%+.3f%% — exit short with profit | %s",
                    self.pair, momentum_30s, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-MOM", hold_seconds)

            # ── 2. TRAILING STOP ───────────────────────────────────────
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                trail_price = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ACTIVATED at +%.2f%% — trailing SL at $%.2f (%.2f%% above low $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self.TRAILING_DISTANCE_PCT,
                    self.lowest_since_entry, soul_msg,
                )

            if self._trailing_active:
                trail_stop = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                if current_price >= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] Soul check: trail stop hit at $%.2f (low was $%.2f) | %s",
                        self.pair, trail_stop, self.lowest_since_entry, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "TRAIL", hold_seconds)
                if self._tick_count % 20 == 0:
                    dist_from_low = ((current_price - self.lowest_since_entry) / self.lowest_since_entry * 100)
                    self.logger.info(
                        "[%s] Soul check: momentum still negative, holding short | "
                        "PnL=%+.2f%% low=$%.2f trail=$%.2f dist=%.2f%%",
                        self.pair, pnl_pct, self.lowest_since_entry, trail_stop, dist_from_low,
                    )
            else:
                if pnl_pct > 0 and self._tick_count % 20 == 0:
                    soul_msg = _soul_check("momentum")
                    self.logger.info(
                        "[%s] Soul check: momentum still negative +%.2f%%, riding short | %s",
                        self.pair, pnl_pct, soul_msg,
                    )

            # ── 3. STOP LOSS ───────────────────────────────────────────
            sl_price = self.entry_price * (1 + self.STOP_LOSS_PCT / 100)
            if current_price >= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] Soul check: loss hit %.2f%%, cutting fast | %s",
                    self.pair, pnl_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "short", "SL", hold_seconds)

            # ── 4. TIMEOUT ─────────────────────────────────────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "short", "TIMEOUT", hold_seconds)

            # ── 5. FLATLINE ────────────────────────────────────────────
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

    def _minutes_to_expiry(self) -> float:
        """Minutes until next daily expiry (5:30 PM IST)."""
        now_ist = datetime.now(IST)
        expiry_today = now_ist.replace(
            hour=self.EXPIRY_HOUR_IST,
            minute=self.EXPIRY_MINUTE_IST,
            second=0, microsecond=0,
        )
        diff = (expiry_today - now_ist).total_seconds() / 60.0
        return diff

    def _is_near_expiry(self) -> tuple[bool, bool, float]:
        """Check if near daily expiry. Returns (no_new, force_close, mins)."""
        mins = self._minutes_to_expiry()
        if mins < 0:
            return False, False, mins
        no_new = mins <= self.NO_NEW_ENTRY_MINUTES
        force = mins <= self.FORCE_CLOSE_MINUTES
        return no_new, force, mins

    # ======================================================================
    # POSITION SIZING
    # ======================================================================

    def _calculate_position_size(self, current_price: float, available: float) -> float | None:
        """Calculate position amount in coin terms. Returns None if can't size.

        Smart sizing: fit contracts into the LOWER of available capital and
        risk manager's max_position_pct limit. This prevents sizing above
        what the risk manager will approve.
        """
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
                        "[%s] 1 contract needs $%.2f collateral > $%.2f avail — skipping",
                        self.pair, one_contract_collateral, available,
                    )
                return None

            # Cap at risk manager's max_position_pct to avoid rejection
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            max_position_value = exchange_capital * (self.risk_manager.max_position_pct / 100)
            budget = min(available, max_position_value)

            # Fit as many contracts as budget allows
            max_affordable = int(budget / one_contract_collateral)
            contracts = max(1, min(max_affordable, self.MAX_CONTRACTS))
            total_collateral = contracts * one_contract_collateral
            amount = contracts * contract_size

            self.logger.info(
                "[%s] Sizing: %d contracts × %.4f = %.6f coin, "
                "collateral=$%.2f (%dx), budget=$%.2f (avail=$%.2f, max=%.0f%%)",
                self.pair, contracts, contract_size, amount,
                total_collateral, self.leverage, budget, available,
                self.risk_manager.max_position_pct,
            )
        else:
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            capital = exchange_capital * (self.capital_pct / 100)
            capital = min(capital, available)
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
        """Build an entry signal — NO fixed TP, only SL. Trail does the rest."""
        self.logger.info("[%s] %s → %s entry", self.pair, reason, side.upper())

        if side == "long":
            sl = price * (1 - self.STOP_LOSS_PCT / 100)
            return Signal(
                side="buy",
                price=price,
                amount=amount,
                order_type="market",
                reason=reason,
                strategy=self.name,
                pair=self.pair,
                stop_loss=sl,
                take_profit=None,  # NO FIXED TP — we trail profits
                leverage=self.leverage if self.is_futures else 1,
                position_type="long" if self.is_futures else "spot",
                exchange_id="delta" if self.is_futures else "binance",
                metadata={"pending_side": "long", "pending_amount": amount},
            )
        else:  # short
            sl = price * (1 + self.STOP_LOSS_PCT / 100)
            return Signal(
                side="sell",
                price=price,
                amount=amount,
                order_type="market",
                reason=reason,
                strategy=self.name,
                pair=self.pair,
                stop_loss=sl,
                take_profit=None,  # NO FIXED TP — we trail profits
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
        """Called by _run_loop when an order fills."""
        pending_side = signal.metadata.get("pending_side")
        pending_amount = signal.metadata.get("pending_amount", 0.0)
        if pending_side:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or pending_amount or signal.amount
            self._open_position(pending_side, fill_price, filled_amount)
            soul_msg = _soul_check("momentum")
            self.logger.info(
                "[%s] FILLED — %s @ $%.2f, %.6f, %dx | Soul: %s",
                self.pair, pending_side.upper(), fill_price, filled_amount,
                self.leverage, soul_msg,
            )

    def on_rejected(self, signal: Signal) -> None:
        """Called by _run_loop when an order fails."""
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
        self._trailing_active = False
        self._hourly_trades.append(time.time())

    def _record_scalp_result(self, pnl_pct: float, exit_type: str) -> None:
        # Convert contracts to coin amount for correct P&L
        coin_amount = self.entry_amount
        if self.is_futures:
            from alpha.trade_executor import DELTA_CONTRACT_SIZE
            contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0.01)
            coin_amount = self.entry_amount * contract_size

        notional = self.entry_price * coin_amount
        gross_pnl = notional * (pnl_pct / 100)

        fee_rate = getattr(self.executor, "_delta_taker_fee", 0.0005) if self._exchange_id == "delta" else getattr(self.executor, "_binance_taker_fee", 0.001)
        est_fees = notional * fee_rate * 2
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
        self._trailing_active = False
        self._last_position_exit = time.monotonic()

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
