"""Alpha v4.1 — TREND-ALIGNED SNIPER: Trade WITH the trend, looser entries.

PHILOSOPHY: Only enter in the direction of the 15-minute trend.
Lower entry thresholds so the bot actually trades.
Adaptive widening: if idle 30+ min, thresholds loosen 20% more.

CRITICAL RULE: Check 15-minute trend BEFORE entering.
  - If 15m trend is bearish → only SHORT, never long
  - If 15m trend is bullish → only LONG, never short
  - If 15m trend is neutral → either direction (2-of-4 decides)
  This single rule prevents most losses from counter-trend entries.

Risk Management (20x leverage):
  - Leverage: 20x — 0.35% against = 7% loss
  - SL: 0.35% price (7% capital at 20x) — cut fast
  - TP: 1.50% minimum (30% capital at 20x)
  - R:R = 1.50/0.35 = 4.3:1
  - Max contracts: ETH 2, BTC 1 (smaller positions while improving)
  - Daily loss limit: 20% of capital → stop for the day

Entry — TREND + 2-of-4 (loosened thresholds):
  0. CHECK 15-MINUTE TREND FIRST (bullish/bearish/neutral)
  1. Momentum: 0.15%+ in 60s (was 0.30%) — easier to trigger
  2. Volume: 1.2x+ average (was 2.0x) — more volume signals fire
  3. RSI: <40 for long, >60 for short (was <30/>70) — wider zone
  4. BB breakout — price outside Bollinger Bands (unchanged)
  Must have 2+ AND align with 15m trend.

Adaptive Widening (idle 30+ min):
  After 30 min with no entry, thresholds loosen 20%:
  - Momentum: 0.15% → 0.12%
  - Volume: 1.2x → ~1.0x
  - RSI: <44 for long, >56 for short
  Resets to normal after trade closes.

Exit — TP MUST BE BIGGER THAN SL (4.3:1 R:R):
  1. Stop loss — 0.35% price (cut fast, at 10x = 3.5% capital)
  2. Trailing stop — activates at +1.50%, trails 0.35% behind peak
  3. Signal reversal — only exit if profit >= 1.50%
  4. NEVER exit a winner early. Hold for 1.50% minimum.
  5. Timeout — 30 min max
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
        principles = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or (stripped and stripped[0].isdigit() and "**" in stripped):
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
            "I don't trade for the sake of trading. Every trade must beat the fees by 5x.",
            "Let winners run and cut losers fast.",
            "Quality over quantity. Fewer but better trades.",
        ]
        _SOUL_LOADED = True
    return _SOUL_PRINCIPLES


def _soul_check(context: str) -> str:
    """Get relevant soul principle for the current decision context."""
    principles = _load_soul()
    context_lower = context.lower()
    if "exit" in context_lower or "tp" in context_lower or "take profit" in context_lower:
        for p in principles:
            if "ride" in p.lower() or "winner" in p.lower() or "fixed" in p.lower():
                return p
    if "loss" in context_lower or "sl" in context_lower or "stop" in context_lower:
        for p in principles:
            if "cut" in p.lower() or "loss" in p.lower():
                return p
    if "fee" in context_lower or "quality" in context_lower or "skip" in context_lower:
        for p in principles:
            if "fee" in p.lower() or "trade for the sake" in p.lower() or "quality" in p.lower():
                return p
    if "momentum" in context_lower:
        for p in principles:
            if "momentum" in p.lower():
                return p
    return principles[0] if principles else "Trade with conviction."


class ScalpStrategy(BaseStrategy):
    """Trend-Aligned Sniper v4.1 — trade WITH the 15m trend, looser entries.

    TP > SL (4.3:1 R:R). Checks 15m trend before entry.
    Lowered entry thresholds so the bot actually trades.
    Adaptive widening: if idle 30+ min, thresholds loosen 20%.
    20x leverage, tighter SL (0.35%), smaller positions.
    """

    name = StrategyName.SCALP
    check_interval_sec = 5  # 5 second ticks — patient, not frantic

    # ── Exit thresholds — TP MUST BE BIGGER THAN SL (4.3:1 R:R) ──────
    STOP_LOSS_PCT = 0.35              # 0.35% price SL (7% capital at 20x) — cut FAST
    MIN_TP_PCT = 1.50                 # minimum 1.5% target (30% capital at 20x)
    TRAILING_ACTIVATE_PCT = 1.50      # activate trail at +1.5% — the minimum target
    TRAILING_DISTANCE_PCT = 0.35      # trail 0.35% behind peak — tight trail
    MAX_HOLD_SECONDS = 30 * 60        # 30 min max — free capital if flat
    FLATLINE_SECONDS = 15 * 60        # 15 min flat = exit
    FLATLINE_MIN_MOVE_PCT = 0.10      # "flat" means < 0.10% total move

    # ── Signal reversal thresholds ──────────────────────────────────────
    RSI_REVERSAL_LONG = 70            # RSI > 70 while long → overbought, exit
    RSI_REVERSAL_SHORT = 30           # RSI < 30 while short → oversold, exit
    MOMENTUM_REVERSAL_PCT = -0.10     # strong momentum reversal against position

    # ── Entry thresholds — TREND + 2-of-4 confirmation ─────────────────
    # Loosened so the bot actually fires (was 0.30/2.0/30/70)
    MOMENTUM_MIN_PCT = 0.15           # 0.15%+ move in 60s (was 0.30%)
    VOL_SPIKE_RATIO = 1.2             # volume > 1.2x average (was 2.0x)
    RSI_EXTREME_LONG = 40             # RSI < 40 = oversold zone → long (was 30)
    RSI_EXTREME_SHORT = 60            # RSI > 60 = overbought zone → short (was 70)
    BB_BREAKOUT = True                # price outside BB = breakout

    # ── Adaptive widening (if idle too long, loosen by 20%) ──────────
    IDLE_WIDEN_SECONDS = 30 * 60      # after 30 min idle, widen thresholds
    IDLE_WIDEN_FACTOR = 0.80          # multiply thresholds by 0.80 (20% looser)

    # ── Fee awareness (Delta India incl 18% GST) ──────────────────────
    MIN_EXPECTED_MOVE_PCT = 0.30      # lowered from 0.50% to match new momentum
    FEE_MULTIPLIER_MIN = 13.0         # 1.5% TP / 0.083% RT mixed = 18x

    # ── Position sizing (REDUCED — smaller positions while improving) ──
    CAPITAL_PCT_SPOT = 50.0
    CAPITAL_PCT_FUTURES = 80.0
    MAX_CONTRACTS_ETH = 2             # ETH: max 2 contracts (was 5)
    MAX_CONTRACTS_BTC = 1             # BTC: max 1 contract (was 5)
    MAX_POSITIONS = 3
    MAX_SPREAD_PCT = 0.15

    # ── Rate limiting / risk ──────────────────────────────────────────────
    MAX_TRADES_PER_HOUR = 10          # keep trading aggressively
    DAILY_LOSS_LIMIT_PCT = 20.0       # stop at 20% daily drawdown

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
        market_analyzer: Any = None,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.trade_exchange: ccxt.Exchange | None = exchange
        self.is_futures = is_futures
        self.leverage: int = min(config.delta.leverage, 20) if is_futures else 1  # CAP at 20x
        self.capital_pct: float = self.CAPITAL_PCT_FUTURES if is_futures else self.CAPITAL_PCT_SPOT
        self._exchange_id: str = "delta" if is_futures else "binance"
        self._market_analyzer = market_analyzer  # for 15m trend direction

        # Per-pair contract limits
        if "BTC" in pair:
            self._max_contracts = self.MAX_CONTRACTS_BTC
        else:
            self._max_contracts = self.MAX_CONTRACTS_ETH

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_amount: float = 0.0
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")
        self._trailing_active: bool = False

        # Previous RSI for reversal detection
        self._prev_rsi: float = 50.0

        # Rate limiting
        self._hourly_trades: list[float] = []
        self._daily_scalp_loss: float = 0.0

        # No more forced entries — we wait for quality setups
        self._last_position_exit: float = 0.0

        # Stats for hourly summary
        self.hourly_wins: int = 0
        self.hourly_losses: int = 0
        self.hourly_pnl: float = 0.0
        self.hourly_skipped: int = 0  # track skipped low-quality signals

        # Tick tracking
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0

        # Load soul on init
        _load_soul()

    async def on_start(self) -> None:
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
        soul_msg = _soul_check("quality")
        # Log fee structure on startup
        if self._exchange_id == "delta":
            rt_mixed = config.delta.mixed_round_trip * 100
            rt_taker = config.delta.taker_round_trip * 100
        else:
            rt_mixed = 0.2
            rt_taker = 0.2
        trend_source = "15m analyzer" if self._market_analyzer else "NONE (no trend filter!)"
        self.logger.info(
            "[%s] TREND SNIPER v4.1 ACTIVE (%s) — tick=%ds, "
            "TP=%.1f%% SL=%.2f%% R:R=%.1f:1 Trail=%.1f%%/%.2f%% "
            "Entry: mom>=%.2f%% vol>=%.1fx RSI<%d/>%d "
            "MaxContracts=%d TrendFilter=%s IdleWiden=%dmin "
            "DailyLossLimit=%.0f%%%s",
            self.pair, tag, self.check_interval_sec,
            self.MIN_TP_PCT, self.STOP_LOSS_PCT,
            self.MIN_TP_PCT / self.STOP_LOSS_PCT,
            self.TRAILING_ACTIVATE_PCT, self.TRAILING_DISTANCE_PCT,
            self.MOMENTUM_MIN_PCT, self.VOL_SPIKE_RATIO,
            self.RSI_EXTREME_LONG, self.RSI_EXTREME_SHORT,
            self._max_contracts, trend_source,
            self.IDLE_WIDEN_SECONDS // 60,
            self.DAILY_LOSS_LIMIT_PCT,
            pos_info,
        )
        self.logger.info("[%s] Soul: %s", self.pair, soul_msg)

    async def on_stop(self) -> None:
        self.logger.info(
            "[%s] Scalp stopped — %dW/%dL, P&L=$%.4f, skipped=%d",
            self.pair, self.hourly_wins, self.hourly_losses,
            self.hourly_pnl, self.hourly_skipped,
        )

    # ======================================================================
    # MAIN CHECK LOOP
    # ======================================================================

    def _get_15m_trend(self) -> str:
        """Get 15-minute trend direction from market analyzer.

        Returns 'bullish', 'bearish', or 'neutral'.
        """
        if not self._market_analyzer:
            return "neutral"
        analysis = self._market_analyzer.last_analysis_for(self.pair)
        if analysis is None:
            return "neutral"
        return analysis.direction or "neutral"

    async def check(self) -> list[Signal]:
        """One scalping tick — fetch candles, detect QUALITY momentum, manage exits."""
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.trade_exchange or self.executor.exchange
        now = time.monotonic()

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

        # ── Rate limit — quality over quantity ─────────────────────────
        cutoff = time.time() - 3600
        self._hourly_trades = [t for t in self._hourly_trades if t > cutoff]
        if len(self._hourly_trades) >= self.MAX_TRADES_PER_HOUR:
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] Rate limit: %d/%d trades this hour — waiting",
                    self.pair, len(self._hourly_trades), self.MAX_TRADES_PER_HOUR,
                )
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

        # Volume ratio (current vs 10-candle average)
        avg_vol = float(volume.iloc[-11:-1].mean()) if len(volume) >= 11 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # 60-second momentum (last 1 full candle)
        price_1m_ago = float(close.iloc[-2]) if len(close) >= 2 else current_price
        momentum_60s = ((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago > 0 else 0

        # 2-candle momentum (120 seconds) for trend confirmation
        price_2m_ago = float(close.iloc[-3]) if len(close) >= 3 else price_1m_ago
        momentum_120s = ((current_price - price_2m_ago) / price_2m_ago * 100) if price_2m_ago > 0 else 0

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
                    int(hold_sec), pnl_now, rsi_now, momentum_60s, trail_tag,
                )
            else:
                idle_sec = int(now - self._last_position_exit)
                self.logger.info(
                    "[%s] (%s) SCANNING %ds | $%.2f | RSI=%.1f | Vol=%.1fx | "
                    "mom60=%+.3f%% | W/L=%d/%d skip=%d",
                    self.pair, tag, idle_sec, current_price, rsi_now, vol_ratio,
                    momentum_60s,
                    self.hourly_wins, self.hourly_losses, self.hourly_skipped,
                )

        # ── In position: check exit ────────────────────────────────────
        if self.in_position:
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
            result = self._check_exits(current_price, rsi_now, momentum_60s)
            self._prev_rsi = rsi_now
            return result

        # ── No position: look for QUALITY entry ────────────────────────
        self._prev_rsi = rsi_now

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

        # ── 15-MINUTE TREND CHECK (most important filter) ──────────────
        trend_15m = self._get_15m_trend()

        # ── Adaptive widening: if idle 30+ min, loosen thresholds 20% ─
        idle_seconds = now - self._last_position_exit
        is_widened = idle_seconds >= self.IDLE_WIDEN_SECONDS

        # ── Quality momentum detection (trend + 2-of-4 confirmation) ──
        entry = self._detect_quality_entry(
            current_price, rsi_now, vol_ratio,
            momentum_60s, momentum_120s,
            bb_upper, bb_lower,
            trend_15m,
            widened=is_widened,
        )

        if entry is not None:
            side, reason, use_limit = entry
            soul_msg = _soul_check("quality entry")
            self.logger.info("[%s] TREND ENTRY — %s | 15m=%s | Soul: %s", self.pair, reason, trend_15m, soul_msg)
            order_type = "limit" if use_limit else "market"
            signals.append(self._build_entry_signal(side, current_price, amount, reason, order_type))
        else:
            # Log scanning status every 30 seconds (not every tick)
            if self._tick_count % 6 == 0:
                idle_sec = int(idle_seconds)
                widen_tag = " [WIDENED]" if is_widened else ""
                # Show effective thresholds
                eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(is_widened)
                self.logger.info(
                    "[%s] WAITING %ds%s | 15m=%s | $%.2f | mom60=%+.3f%%/%.2f | RSI=%.1f/%d/%d | "
                    "vol=%.1fx/%.1f | BB[%.2f-%.2f]",
                    self.pair, idle_sec, widen_tag, trend_15m, current_price,
                    momentum_60s, eff_mom,
                    rsi_now, eff_rsi_l, eff_rsi_s,
                    vol_ratio, eff_vol,
                    bb_lower, bb_upper,
                )

        return signals

    # ======================================================================
    # QUALITY ENTRY DETECTION — 2 of 4 confirmation required
    # ======================================================================

    def _effective_thresholds(self, widened: bool = False) -> tuple[float, float, float, float]:
        """Return (momentum, vol_ratio, rsi_long, rsi_short) with optional widening.

        When widened=True (idle 30+ min), thresholds loosen by 20%:
        - Momentum: 0.15% → 0.12%
        - Volume: 1.2x → 0.96x (~1.0x, essentially any volume)
        - RSI long: 40 → 44 (wider range triggers)
        - RSI short: 60 → 56 (wider range triggers)
        """
        f = self.IDLE_WIDEN_FACTOR if widened else 1.0
        mom = self.MOMENTUM_MIN_PCT * f
        vol = self.VOL_SPIKE_RATIO * f
        # RSI: widen the zone — move long threshold UP, short threshold DOWN
        rsi_l = self.RSI_EXTREME_LONG + (50 - self.RSI_EXTREME_LONG) * (1 - f)
        rsi_s = self.RSI_EXTREME_SHORT - (self.RSI_EXTREME_SHORT - 50) * (1 - f)
        return mom, vol, rsi_l, rsi_s

    def _detect_quality_entry(
        self,
        price: float,
        rsi_now: float,
        vol_ratio: float,
        momentum_60s: float,
        momentum_120s: float,
        bb_upper: float,
        bb_lower: float,
        trend_15m: str = "neutral",
        widened: bool = False,
    ) -> tuple[str, str, bool] | None:
        """Detect quality momentum aligned with 15m trend.

        Returns (side, reason, use_limit) or None.

        CRITICAL: Only enter in the direction of the 15-minute trend.
        - 15m bullish → only LONG entries allowed
        - 15m bearish → only SHORT entries allowed
        - 15m neutral → either direction (2-of-4 decides)

        Requires AT LEAST 2 of these 4 conditions:
        1. Price moved 0.15%+ in last 60s (widened: 0.12%)
        2. Volume spike 1.2x+ (widened: ~1.0x)
        3. RSI < 40 or > 60 (widened: < 44 or > 56)
        4. BB breakout (price outside bands)

        If idle 30+ min, thresholds loosen 20% (adaptive widening).
        """
        can_short = self.is_futures and config.delta.enable_shorting

        # ── Get effective thresholds (may be widened) ────────────────────
        eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(widened)
        widen_tag = " WIDE" if widened else ""

        # ── Count bullish and bearish signals ────────────────────────────
        bull_signals: list[str] = []
        bear_signals: list[str] = []

        # 1. Momentum (60s move)
        if momentum_60s >= eff_mom:
            bull_signals.append(f"MOM:{momentum_60s:+.2f}%")
        elif momentum_60s <= -eff_mom:
            bear_signals.append(f"MOM:{momentum_60s:+.2f}%")

        # 2. Volume spike
        if vol_ratio >= eff_vol:
            # Volume confirms direction based on candle
            if momentum_60s > 0:
                bull_signals.append(f"VOL:{vol_ratio:.1f}x")
            elif momentum_60s < 0:
                bear_signals.append(f"VOL:{vol_ratio:.1f}x")
            else:
                # Neutral volume — add to both, will be filtered by 2-of-4
                bull_signals.append(f"VOL:{vol_ratio:.1f}x")
                bear_signals.append(f"VOL:{vol_ratio:.1f}x")

        # 3. RSI extreme (using effective thresholds)
        if rsi_now < eff_rsi_l:
            bull_signals.append(f"RSI:{rsi_now:.0f}<{eff_rsi_l:.0f}")
        if rsi_now > eff_rsi_s:
            bear_signals.append(f"RSI:{rsi_now:.0f}>{eff_rsi_s:.0f}")

        # 4. BB breakout
        if price > bb_upper:
            bull_signals.append(f"BB:breakout>{bb_upper:.0f}")
        if price < bb_lower:
            bear_signals.append(f"BB:breakdown<{bb_lower:.0f}")

        # ── TREND FILTER: block entries against 15m trend ────────────────
        # This is the #1 rule — prevents most losses
        allow_long = trend_15m in ("bullish", "neutral")
        allow_short = trend_15m in ("bearish", "neutral")

        # ── Check 2-of-4 requirement (LONG) ──────────────────────────────
        if len(bull_signals) >= 2 and allow_long:
            # Verify expected move is worth the fees
            if abs(momentum_60s) < self.MIN_EXPECTED_MOVE_PCT and abs(momentum_120s) < self.MIN_EXPECTED_MOVE_PCT:
                self.hourly_skipped += 1
                if self._tick_count % 10 == 0:
                    soul_msg = _soul_check("fee skip")
                    self.logger.info(
                        "[%s] SKIP LONG — signals=%s but move too small (60s=%+.2f%%, 120s=%+.2f%% < %.1f%%) | %s",
                        self.pair, "+".join(bull_signals),
                        momentum_60s, momentum_120s, self.MIN_EXPECTED_MOVE_PCT, soul_msg,
                    )
                return None

            reason = f"LONG 2-of-4: {' + '.join(bull_signals)} [15m={trend_15m}]{widen_tag}"
            # Use limit order if we have time (RSI signal, not breakout)
            use_limit = "MOM" not in bull_signals[0]  # limit if not urgent momentum
            return ("long", reason, use_limit)

        elif len(bull_signals) >= 2 and not allow_long:
            # Would have entered long but 15m trend is bearish — BLOCK
            if self._tick_count % 10 == 0:
                self.logger.info(
                    "[%s] BLOCKED LONG — 15m trend=%s, signals=%s (would lose against trend)",
                    self.pair, trend_15m, "+".join(bull_signals),
                )
            self.hourly_skipped += 1
            return None

        # ── Check 2-of-4 requirement (SHORT) ─────────────────────────────
        if len(bear_signals) >= 2 and can_short and allow_short:
            if abs(momentum_60s) < self.MIN_EXPECTED_MOVE_PCT and abs(momentum_120s) < self.MIN_EXPECTED_MOVE_PCT:
                self.hourly_skipped += 1
                if self._tick_count % 10 == 0:
                    soul_msg = _soul_check("fee skip")
                    self.logger.info(
                        "[%s] SKIP SHORT — signals=%s but move too small (60s=%+.2f%%, 120s=%+.2f%% < %.1f%%) | %s",
                        self.pair, "+".join(bear_signals),
                        momentum_60s, momentum_120s, self.MIN_EXPECTED_MOVE_PCT, soul_msg,
                    )
                return None

            reason = f"SHORT 2-of-4: {' + '.join(bear_signals)} [15m={trend_15m}]{widen_tag}"
            use_limit = "MOM" not in bear_signals[0]
            return ("short", reason, use_limit)

        elif len(bear_signals) >= 2 and can_short and not allow_short:
            # Would have entered short but 15m trend is bullish — BLOCK
            if self._tick_count % 10 == 0:
                self.logger.info(
                    "[%s] BLOCKED SHORT — 15m trend=%s, signals=%s (would lose against trend)",
                    self.pair, trend_15m, "+".join(bear_signals),
                )
            self.hourly_skipped += 1
            return None

        return None

    # ======================================================================
    # EXIT LOGIC — RIDE WINNERS, CUT LOSERS
    # ======================================================================

    def _check_exits(self, current_price: float, rsi_now: float, momentum_60s: float) -> list[Signal]:
        """Check exit conditions.

        Priority:
        1. Stop loss — 0.35% price (3.5% capital at 10x) — cut losers fast
        2. Signal reversal — when in profit, exit at the top
        3. Trailing stop — activates at +1.5%, trails 0.35% behind peak
        4. Timeout — 30 min, free capital
        5. Flatline — no movement for 15 min
        """
        signals: list[Signal] = []
        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)

        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)

            # ── 1. STOP LOSS — cut losers fast ─────────────────────────
            sl_price = self.entry_price * (1 - self.STOP_LOSS_PCT / 100)
            if current_price <= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] SL HIT at %.2f%% — cutting loss | %s",
                    self.pair, pnl_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "long", "SL", hold_seconds)

            # ── 2. SIGNAL REVERSAL — exit at the top when in profit ────
            if pnl_pct > 0:
                rsi_crossed_70 = rsi_now > self.RSI_REVERSAL_LONG and self._prev_rsi <= self.RSI_REVERSAL_LONG
                momentum_reversed = momentum_60s < self.MOMENTUM_REVERSAL_PCT

                if rsi_crossed_70 and pnl_pct >= self.MIN_TP_PCT:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] RSI %.1f crossed 70 at +%.2f%% — taking profit | %s",
                        self.pair, rsi_now, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-RSI", hold_seconds)

                if momentum_reversed and pnl_pct >= self.MIN_TP_PCT:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] Momentum flipped %+.3f%% at +%.2f%% — taking profit | %s",
                        self.pair, momentum_60s, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-MOM", hold_seconds)

            # ── 3. TRAILING STOP — let winners run ─────────────────────
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                trail_price = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ON at +%.2f%% — SL at $%.2f (%.1f%% behind peak $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self.TRAILING_DISTANCE_PCT,
                    self.highest_since_entry, soul_msg,
                )

            if self._trailing_active:
                trail_stop = self.highest_since_entry * (1 - self.TRAILING_DISTANCE_PCT / 100)
                if current_price <= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] TRAIL HIT at $%.2f (peak $%.2f) PnL=+%.2f%% | %s",
                        self.pair, trail_stop, self.highest_since_entry, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "TRAIL", hold_seconds)
                # Log trail status periodically
                if self._tick_count % 12 == 0:
                    dist = ((self.highest_since_entry - current_price) / self.highest_since_entry * 100)
                    self.logger.info(
                        "[%s] RIDING +%.2f%% | peak=$%.2f trail=$%.2f dist=%.2f%%",
                        self.pair, pnl_pct, self.highest_since_entry, trail_stop, dist,
                    )

            # ── 4. TIMEOUT ──────────────────────────────────────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "long", "TIMEOUT", hold_seconds)

            # ── 5. FLATLINE ─────────────────────────────────────────────
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "long", "FLAT", hold_seconds)

        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

            # ── 1. STOP LOSS ───────────────────────────────────────────
            sl_price = self.entry_price * (1 + self.STOP_LOSS_PCT / 100)
            if current_price >= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] SL HIT at %.2f%% — cutting loss | %s",
                    self.pair, pnl_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "short", "SL", hold_seconds)

            # ── 2. SIGNAL REVERSAL ──────────────────────────────────────
            if pnl_pct > 0:
                rsi_crossed_30 = rsi_now < self.RSI_REVERSAL_SHORT and self._prev_rsi >= self.RSI_REVERSAL_SHORT
                momentum_reversed = momentum_60s > abs(self.MOMENTUM_REVERSAL_PCT)

                if rsi_crossed_30 and pnl_pct >= self.MIN_TP_PCT:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] RSI %.1f crossed below 30 at +%.2f%% — taking short profit | %s",
                        self.pair, rsi_now, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-RSI", hold_seconds)

                if momentum_reversed and pnl_pct >= self.MIN_TP_PCT:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] Momentum flipped +%.3f%% at +%.2f%% — taking short profit | %s",
                        self.pair, momentum_60s, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-MOM", hold_seconds)

            # ── 3. TRAILING STOP ────────────────────────────────────────
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                trail_price = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ON at +%.2f%% — SL at $%.2f (%.1f%% above low $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self.TRAILING_DISTANCE_PCT,
                    self.lowest_since_entry, soul_msg,
                )

            if self._trailing_active:
                trail_stop = self.lowest_since_entry * (1 + self.TRAILING_DISTANCE_PCT / 100)
                if current_price >= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] TRAIL HIT at $%.2f (low $%.2f) PnL=+%.2f%% | %s",
                        self.pair, trail_stop, self.lowest_since_entry, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "TRAIL", hold_seconds)
                if self._tick_count % 12 == 0:
                    dist = ((current_price - self.lowest_since_entry) / self.lowest_since_entry * 100)
                    self.logger.info(
                        "[%s] RIDING SHORT +%.2f%% | low=$%.2f trail=$%.2f dist=%.2f%%",
                        self.pair, pnl_pct, self.lowest_since_entry, trail_stop, dist,
                    )

            # ── 4. TIMEOUT ──────────────────────────────────────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS:
                return self._do_exit(current_price, pnl_pct, "short", "TIMEOUT", hold_seconds)

            # ── 5. FLATLINE ─────────────────────────────────────────────
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

            # Fit as many contracts as budget allows (capped per pair)
            max_affordable = int(budget / one_contract_collateral)
            contracts = max(1, min(max_affordable, self._max_contracts))
            total_collateral = contracts * one_contract_collateral
            amount = contracts * contract_size

            self.logger.info(
                "[%s] Sizing: %d contracts x %.4f = %.6f coin, "
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
                "[%s] Sizing (spot): $%.2f -> %.8f",
                self.pair, capital, amount,
            )

        return amount

    # ======================================================================
    # SIGNAL BUILDERS
    # ======================================================================

    def _build_entry_signal(
        self, side: str, price: float, amount: float, reason: str,
        order_type: str = "market",
    ) -> Signal:
        """Build an entry signal with SL. Trail handles the TP."""
        self.logger.info("[%s] %s -> %s entry (%s)", self.pair, reason, side.upper(), order_type)

        if side == "long":
            sl = price * (1 - self.STOP_LOSS_PCT / 100)
            return Signal(
                side="buy",
                price=price,
                amount=amount,
                order_type=order_type,
                reason=reason,
                strategy=self.name,
                pair=self.pair,
                stop_loss=sl,
                take_profit=None,  # trailing stop handles exit
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
                order_type=order_type,
                reason=reason,
                strategy=self.name,
                pair=self.pair,
                stop_loss=sl,
                take_profit=None,  # trailing stop handles exit
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

        # Fee estimation: maker entry + taker exit (mixed round-trip)
        # Delta India fees include 18% GST
        if self._exchange_id == "delta":
            entry_fee_rate = config.delta.maker_fee_with_gst   # 0.024% (limit entry)
            exit_fee_rate = config.delta.taker_fee_with_gst    # 0.059% (market exit)
        else:
            entry_fee_rate = getattr(self.executor, "_binance_taker_fee", 0.001)
            exit_fee_rate = entry_fee_rate
        est_fees = notional * (entry_fee_rate + exit_fee_rate)
        net_pnl = gross_pnl - est_fees

        capital_pnl_pct = pnl_pct * self.leverage

        self.hourly_pnl += net_pnl
        self._daily_scalp_loss += net_pnl if net_pnl < 0 else 0

        if pnl_pct >= 0:
            self.hourly_wins += 1
        else:
            self.hourly_losses += 1

        hold_sec = int(time.monotonic() - self.entry_time)
        duration = f"{hold_sec // 60}m{hold_sec % 60:02d}s" if hold_sec >= 60 else f"{hold_sec}s"

        # Log with fee breakdown for visibility
        fee_ratio = abs(gross_pnl / est_fees) if est_fees > 0 else 0
        self.logger.info(
            "[%s] CLOSED %s %+.2f%% price (%+.1f%% capital at %dx) | "
            "Gross=$%.4f Net=$%.4f fees=$%.4f (%.1fx) | %s | W/L=%d/%d",
            self.pair, exit_type.upper(), pnl_pct, capital_pnl_pct, self.leverage,
            gross_pnl, net_pnl, est_fees, fee_ratio, duration,
            self.hourly_wins, self.hourly_losses,
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
            "skipped": self.hourly_skipped,
        }
        self.hourly_wins = 0
        self.hourly_losses = 0
        self.hourly_pnl = 0.0
        self.hourly_skipped = 0
        return stats

    def reset_daily_stats(self) -> None:
        self._daily_scalp_loss = 0.0
