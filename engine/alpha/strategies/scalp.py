"""Alpha v5.5 — SMART TREND: soft 15m weight, per-pair streaks, multi-TF momentum.

PHILOSOPHY: Two strong positions beat four weak ones. Focus capital on the
best signals. After a loss, pause THAT PAIR and let it settle — don't punish
all pairs for one pair's bad luck. Use the 15m trend as a soft bias, not a wall.
SL/TP adapt to each asset's actual volatility via 1-minute ATR.

ENTRY — 2-of-4 with 15m SOFT WEIGHT:
  Signals (up to 6, counted against N/4 threshold):
  1. Momentum 60s: 0.15%+ move in 60s
  2. Volume: 1.2x average spike
  3. RSI: < 40 (oversold → long) or > 60 (overbought → short)
  4. BB mean-reversion: price near lower BB → long, upper → short
  5. Momentum 5m: 0.30%+ move over 5 candles (slow bleed detection)
  6. Trend continuation: new 15-candle low/high + volume > average

  15m TREND SOFT WEIGHT (not a blocker):
  - Bearish 15m: SHORT needs 2/4, LONG needs 3/4 (counter-trend harder)
  - Bullish 15m: LONG needs 2/4, SHORT needs 3/4 (counter-trend harder)
  - Neutral: both need 2/4

POSITION MANAGEMENT:
  - Max 2 simultaneous positions (focused capital)
  - 2nd position only if 1st is breakeven or profitable AND signal is 3/4+
  - After SL hit: 2 min cooldown (PER PAIR — BTC SL doesn't pause XRP)
  - After 3 consecutive losses on SAME pair: 5 min pause (that pair only)
  - First trade after streak pause: requires 3/4 signals (re-entry gate)

Risk Management (20x leverage) — ATR-DYNAMIC:
  - SL = max(pair_floor, ATR_1m * 1.5) — avoids noise stops
  - TP = max(pair_floor, ATR_1m * 4.0) — reward > 2.5x risk
  - Per-pair floors: BTC/ETH 0.35%/1.5%, SOL 0.50%/2.0%, XRP 0.60%/2.0%
  - SL cap: 1.5%, TP cap: 5.0%
  - Trailing: activates at +0.20% — protect profits EARLY
  - Max hold: 5 min — scalping is quick

TICK SPEED — DYNAMIC:
  - 1s ticks when in position (fetch_ticker for speed)
  - 5s ticks when scanning (full OHLCV for indicators)
  - Full OHLCV refresh every 5th in-position tick (ATR/RSI update)

Exit — FAST, PROTECT PROFITS:
  1. Stop loss — ATR-dynamic, cut losers fast
  2. Breakeven SL — if not profitable by 60s, tighten SL to entry
  3. Profit pullback — if peak > 0.50% and drops 40% from peak, EXIT
  3.5. Profit decay — if peak was > 0.30% and drops to < 0.10%, EXIT
  4. Signal reversal — exit at +0.30% profit if RSI/momentum reverses
  5. Trailing stop — activates at +0.20%, dynamic trail widens with profit
  6. Timeout — 5 min max (only if not trailing)
  7. Flatline — 2 min with < 0.05% move = dead momentum, exit
"""

from __future__ import annotations

import asyncio
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
    """Smart Trend v5.5 — Soft 15m weight, per-pair streaks, multi-TF momentum.

    2-of-4 signals with 15m trend soft weight (counter-trend needs 3/4).
    6 signal types: MOM60s, VOL, RSI, BB, MOM5m, TrendContinuation.
    Per-pair streak tracking: BTC losses don't pause XRP.
    Post-streak re-entry gate: first trade back needs 3/4 on that pair.
    SL/TP computed from 1m ATR: SL = max(floor, ATR*1.5), TP = max(floor, ATR*4).
    Dynamic ticks: 1s in position (ticker), 5s scanning (full OHLCV).
    Trail at +0.20%. Profit decay exit if peak > 0.30% drops to < 0.10%.
    """

    name = StrategyName.SCALP
    check_interval_sec = 5  # 5 second ticks — patient, not frantic

    # ── Exit thresholds — QUICK SCALP: protect profits fast ────────────
    # Default SL/TP (used if ATR not available yet — overridden by dynamic ATR-based SL/TP)
    STOP_LOSS_PCT = 0.35              # default 0.35% price SL — overridden per-pair by ATR
    MIN_TP_PCT = 1.50                 # default 1.5% target — overridden per-pair by ATR

    # ── Per-pair SL/TP floors — wider for volatile alts, tighter for BTC ──
    # SL = max(floor, ATR_1m * 1.5)   TP = max(floor, ATR_1m * 4.0)
    PAIR_SL_FLOOR: dict[str, float] = {
        "BTC": 0.35,   # BTC: low % volatility, 0.35% floor is fine
        "ETH": 0.35,   # ETH: medium volatility, 0.35% floor
        "SOL": 0.50,   # SOL: volatile alt, needs wider SL to avoid noise stops
        "XRP": 0.60,   # XRP: very volatile in % terms, widest SL
    }
    PAIR_TP_FLOOR: dict[str, float] = {
        "BTC": 1.50,   # BTC: 1.5% TP (30% at 20x)
        "ETH": 1.50,   # ETH: 1.5% TP
        "SOL": 2.00,   # SOL: wider TP to match wider SL
        "XRP": 2.00,   # XRP: wider TP
    }
    ATR_SL_MULTIPLIER = 1.5          # SL = ATR_1m_pct * 1.5 (avoid noise stops)
    ATR_TP_MULTIPLIER = 4.0          # TP = ATR_1m_pct * 4.0 (reward > 2.5x risk)
    TRAILING_ACTIVATE_PCT = 0.20      # activate trail at +0.20% — lock profits FAST
    TRAILING_DISTANCE_PCT = 0.15      # initial trail: tight 0.15% behind peak
    MAX_HOLD_SECONDS = 5 * 60         # 5 min max — scalping is quick
    FLATLINE_SECONDS = 2 * 60         # 2 min flat = dead momentum, exit
    FLATLINE_MIN_MOVE_PCT = 0.05      # "flat" means < 0.05% total move

    # ── Breakeven & profit protection ─────────────────────────────────
    BREAKEVEN_AFTER_SECONDS = 60      # 60s — tighten SL to breakeven FAST
    PROFIT_PULLBACK_MIN_PEAK = 0.50   # peak must be > 0.50% to trigger pullback exit
    PROFIT_PULLBACK_PCT = 40.0        # exit if profit drops 40% from peak

    # ── Profit decay — don't let green trades go to zero ────────────
    PROFIT_DECAY_PEAK_MIN = 0.30      # peak must have been > 0.30%
    PROFIT_DECAY_EXIT_AT = 0.10       # exit if decays to < 0.10%

    # ── Signal reversal — exit earlier in quick scalp mode ───────────
    REVERSAL_MIN_PROFIT_PCT = 0.30    # exit on reversal at just +0.30% (was 1.50%)

    # ── Dynamic trailing tiers — widen as profit grows ──────────────
    # (min_profit_pct, trail_distance_pct)
    # Trail distance ONLY increases, never tightens once widened.
    # At 20x leverage: +3% price = +60% capital, trailing 1% = locks +2% min (40% capital)
    TRAIL_TIERS: list[tuple[float, float]] = [
        (0.20, 0.15),   # +0.2% to +0.5%: very tight, lock it in
        (0.50, 0.25),   # +0.5% to +1%: slightly wider
        (1.00, 0.50),   # +1% to +2%: give room to run
        (2.00, 0.70),   # +2% to +3%: widen further
        (3.00, 1.00),   # +3%+: max breathing room, locks +2% min
    ]

    # ── Signal reversal thresholds ──────────────────────────────────────
    RSI_REVERSAL_LONG = 70            # RSI > 70 while long → overbought, exit
    RSI_REVERSAL_SHORT = 30           # RSI < 30 while short → oversold, exit
    MOMENTUM_REVERSAL_PCT = -0.10     # strong momentum reversal against position

    # ── Entry thresholds — 2-of-4 with 15m trend soft weight ────────────
    MOMENTUM_MIN_PCT = 0.15           # 0.15%+ move in 60s
    VOL_SPIKE_RATIO = 1.2             # volume > 1.2x average
    RSI_EXTREME_LONG = 40             # RSI < 40 = oversold → long
    RSI_EXTREME_SHORT = 60            # RSI > 60 = overbought → short
    # BB mean-reversion thresholds (upper = short, lower = long):
    BB_MEAN_REVERT_UPPER = 0.85      # price in top 15% of BB → short signal
    BB_MEAN_REVERT_LOWER = 0.15      # price in bottom 15% of BB → long signal
    # Multi-timeframe momentum: 5-minute (300s) slow bleed detection
    MOMENTUM_5M_MIN_PCT = 0.30       # 0.30%+ move over 5 candles (slow bleed counts)
    # Trend continuation: new 15-candle low/high + volume confirms trend
    TREND_CONT_CANDLES = 15           # look back 15 candles for new low/high
    TREND_CONT_VOL_RATIO = 1.0       # volume must be above average (1.0x+)

    # ── Adaptive widening (if idle too long, loosen by 20%) ──────────
    IDLE_WIDEN_SECONDS = 30 * 60      # after 30 min idle, widen thresholds
    IDLE_WIDEN_FACTOR = 0.80          # multiply thresholds by 0.80 (20% looser)

    # ── Fee awareness (Delta India incl 18% GST) ──────────────────────
    # NOTE: MIN_EXPECTED_MOVE_PCT fee filter REMOVED — it was blocking 385+
    # legitimate entries per hour (RSI+VOL signals with low momentum).
    # The 2-of-4 signal system IS the quality filter. If signals fire, enter.
    FEE_MULTIPLIER_MIN = 13.0         # 1.5% TP / 0.083% RT mixed = 18x

    # ── Position sizing — PERFORMANCE-BASED per-pair allocation ─────────
    CAPITAL_PCT_SPOT = 35.0             # 35% of Binance USDT per trade (middle of 30-40%)
    CAPITAL_PCT_FUTURES = 40.0          # base 40% — modified by PAIR_ALLOC_PCT below
    MIN_NOTIONAL_SPOT = 6.00            # $6 min to avoid dust on exit (Binance $5 min + buffer)

    # Per-pair base allocation (% of exchange capital) — tuned by performance
    PAIR_ALLOC_PCT: dict[str, float] = {
        "XRP": 50.0,   # best performer, highest profit factor — maximize
        "ETH": 30.0,   # mixed but catches big moves
        "BTC": 15.0,   # very low win rate — reduce exposure
        "SOL":  5.0,   # worst performer — minimal allocation
    }
    # Per-pair contract caps
    PAIR_MAX_CONTRACTS: dict[str, int] = {
        "BTC": 1,              # BTC: 1 contract (minimum, diversification only)
        "ETH": 2,              # ETH: 2 contracts
        "SOL": 1,              # SOL: 1 contract (or skip)
        "XRP": 50,             # XRP: 50 contracts (main earner, maximize)
    }
    # Minimum signal strength per pair — weaker performers need stronger signals
    PAIR_MIN_STRENGTH: dict[str, int] = {
        "XRP": 2,    # best performer: standard 2/4 is fine
        "ETH": 2,    # mixed: standard 2/4
        "BTC": 3,    # low win rate: require 3/4 or 4/4 signals only
        "SOL": 3,    # worst performer: require 3/4 or 4/4 signals only
    }
    # Adaptive: track last N trades per pair for win-rate-based adjustment
    PERF_WINDOW = 5                     # look at last 5 trades per pair
    PERF_LOW_WR_THRESHOLD = 0.20        # <20% WR in window → reduce to minimum
    PERF_HIGH_WR_THRESHOLD = 0.60       # >60% WR in window → boost allocation
    MAX_POSITIONS = 2                   # max 2 simultaneous — focus capital
    MAX_SPREAD_PCT = 0.15

    # ── Cooldown / loss protection (PER-PAIR: BTC streak doesn't affect XRP) ─
    SL_COOLDOWN_SECONDS = 2 * 60       # 2 min pause after SL hit (per pair)
    CONSECUTIVE_LOSS_LIMIT = 3          # after 3 consecutive losses on same pair...
    STREAK_PAUSE_SECONDS = 5 * 60      # ...pause that pair for 5 min
    POST_STREAK_STRENGTH = 3            # first trade after streak pause needs 3/4
    MIN_STRENGTH_FOR_2ND = 3            # 2nd position needs 3/4+ signal strength

    # ── Rate limiting / risk ──────────────────────────────────────────────
    MAX_TRADES_PER_HOUR = 10          # keep trading aggressively
    DAILY_LOSS_LIMIT_PCT = 20.0       # stop at 20% daily drawdown

    # ── Class-level shared state ──────────────────────────────────────────
    _live_pnl: dict[str, float] = {}           # pair → current unrealized P&L % (updated every tick)
    _pair_trade_history: dict[str, list[bool]] = {}  # base_asset → list of win/loss booleans (last N)
    # ── Per-pair streak/cooldown (BTC losses don't pause XRP) ────────────
    _pair_last_sl_time: dict[str, float] = {}            # base_asset → monotonic time of last SL
    _pair_consecutive_losses: dict[str, int] = {}        # base_asset → streak count
    _pair_streak_pause_until: dict[str, float] = {}      # base_asset → pause end time
    _pair_post_streak: dict[str, bool] = {}              # base_asset → True if first trade after streak

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

        # Per-pair contract limits (data-driven from PAIR_MAX_CONTRACTS dict)
        base_asset = pair.split("/")[0] if "/" in pair else pair.replace("USD", "").replace(":USD", "")
        self._max_contracts = self.PAIR_MAX_CONTRACTS.get(base_asset, 1)
        self._base_asset = base_asset  # cached for SL/TP lookup

        # Dynamic ATR-based SL/TP — updated every tick from 1m candles
        self._sl_pct: float = self.PAIR_SL_FLOOR.get(base_asset, self.STOP_LOSS_PCT)
        self._tp_pct: float = self.PAIR_TP_FLOOR.get(base_asset, self.MIN_TP_PCT)
        self._last_atr_pct: float = 0.0  # last computed 1m ATR as % of price

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_amount: float = 0.0
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")
        self._trailing_active: bool = False
        self._trail_distance_pct: float = self.TRAILING_DISTANCE_PCT  # dynamic, only widens
        self._peak_unrealized_pnl: float = 0.0  # track peak P&L for decay exit
        self._in_position_tick: int = 0  # counts 1s ticks while in position (for OHLCV refresh)

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

        # Shared signal state (read by options_scalp strategy)
        self.last_signal_state: dict[str, Any] | None = None

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
            # Binance spot: 0.1% per side (check for BNB discount)
            rt_mixed = 0.20  # 0.1% × 2 = 0.2% round trip
            rt_taker = 0.20
        tiers_str = " → ".join(f"+{p}%:{d}%" for p, d in self.TRAIL_TIERS)
        self.logger.info(
            "[%s] SMART TREND v5.5 ACTIVE (%s) — tick=1s/5s(dynamic), "
            "SL=%.2f%%(floor,ATR-dynamic) TP=%.2f%% Trail@%.1f%% [%s] MaxHold=%ds Breakeven@%ds "
            "Pullback=%.0f%%@%.1f%% Decay=peak>%.1f%%→exit<%.1f%% Flatline=%ds/%.2f%% "
            "MaxPos=%d MaxContracts=%d SLcool=%ds LossStreak=%d→%ds "
            "15m=SOFT_WEIGHT DailyLossLimit=%.0f%%%s",
            self.pair, tag,
            self._sl_pct, self._tp_pct,
            self.TRAILING_ACTIVATE_PCT, tiers_str,
            self.MAX_HOLD_SECONDS, self.BREAKEVEN_AFTER_SECONDS,
            self.PROFIT_PULLBACK_PCT, self.PROFIT_PULLBACK_MIN_PEAK,
            self.PROFIT_DECAY_PEAK_MIN, self.PROFIT_DECAY_EXIT_AT,
            self.FLATLINE_SECONDS, self.FLATLINE_MIN_MOVE_PCT,
            self.MAX_POSITIONS, self._max_contracts,
            self.SL_COOLDOWN_SECONDS, self.CONSECUTIVE_LOSS_LIMIT,
            self.STREAK_PAUSE_SECONDS,
            self.DAILY_LOSS_LIMIT_PCT,
            pos_info,
        )
        self.logger.info("[%s] Soul: %s", self.pair, soul_msg)

    def get_tick_interval(self) -> int:
        """Dynamic tick: 1s when holding a position, 5s when scanning."""
        return 1 if self.in_position else 5

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

    def _update_dynamic_sl_tp(self, df: pd.DataFrame, current_price: float) -> None:
        """Compute ATR-based SL/TP from 1m candles.

        Formula:
          SL = max(pair_floor, ATR_1m_pct * 1.5)
          TP = max(pair_floor, ATR_1m_pct * 4.0)

        This avoids noise stops on volatile pairs (SOL, XRP) while keeping
        tight stops on low-vol pairs (BTC).
        """
        try:
            high = df["high"]
            low = df["low"]
            close = df["close"]
            if len(close) < 15:
                return  # not enough data, keep existing values
            atr_indicator = ta.volatility.AverageTrueRange(high, low, close, window=14)
            atr = float(atr_indicator.average_true_range().iloc[-1])
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 0.0
            self._last_atr_pct = atr_pct

            # Per-pair floors
            sl_floor = self.PAIR_SL_FLOOR.get(self._base_asset, self.STOP_LOSS_PCT)
            tp_floor = self.PAIR_TP_FLOOR.get(self._base_asset, self.MIN_TP_PCT)

            # Dynamic: ATR-based, but never below floor
            self._sl_pct = max(sl_floor, atr_pct * self.ATR_SL_MULTIPLIER)
            self._tp_pct = max(tp_floor, atr_pct * self.ATR_TP_MULTIPLIER)

            # Safety cap: SL never wider than 1.5%, TP never wider than 5%
            self._sl_pct = min(self._sl_pct, 1.50)
            self._tp_pct = min(self._tp_pct, 5.00)
        except Exception:
            # Silently keep existing values if ATR calc fails
            pass

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

        # ── FAST PATH: when in position, use fetch_ticker (1s) instead of fetch_ohlcv (5s) ──
        # Full OHLCV only every 5th in-position tick (for indicator refresh) or when scanning
        rsi_now = self._prev_rsi  # default to cached RSI for fast ticks
        bb_upper = 0.0
        bb_lower = 0.0
        vol_ratio = 0.0
        momentum_60s = 0.0
        momentum_120s = 0.0
        momentum_300s = 0.0
        df: pd.DataFrame | None = None
        _need_full_indicators = True

        if self.in_position:
            self._in_position_tick += 1
            if self._in_position_tick % 5 != 0:
                # FAST TICK: just fetch price via ticker (much lighter than OHLCV)
                try:
                    ticker = await exchange.fetch_ticker(self.pair)
                    current_price = float(ticker.get("last", 0) or 0)
                except Exception:
                    return signals  # skip this tick if ticker fails
                _need_full_indicators = False
            else:
                # Every 5th tick: do full OHLCV refresh for indicators + ATR
                _need_full_indicators = True

        if _need_full_indicators:
            # Full OHLCV fetch — for entry detection OR periodic in-position refresh
            ohlcv = await exchange.fetch_ohlcv(self.pair, "1m", limit=30)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            close = df["close"]
            volume = df["volume"]
            current_price = float(close.iloc[-1])

            # Update dynamic ATR-based SL/TP
            self._update_dynamic_sl_tp(df, current_price)

            # Compute indicators
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

            # 5-candle momentum (300 seconds) for slow bleed detection
            price_5m_ago = float(close.iloc[-6]) if len(close) >= 6 else price_2m_ago
            momentum_300s = ((current_price - price_5m_ago) / price_5m_ago * 100) if price_5m_ago > 0 else 0

        # ── Heartbeat every 60 seconds ─────────────────────────────────
        if now - self._last_heartbeat >= 60:
            self._last_heartbeat = now
            tag = f"{self.leverage}x" if self.is_futures else "spot"
            if self.in_position:
                hold_sec = now - self.entry_time
                pnl_now = self._calc_pnl_pct(current_price)
                trail_tag = " [TRAILING]" if self._trailing_active else ""
                self.logger.info(
                    "[%s] (%s) %s @ $%.2f | %ds | PnL=%+.2f%% | SL=%.2f%% | RSI=%.1f | ATR=%.3f%% | mom=%+.3f%%%s",
                    self.pair, tag, self.position_side, self.entry_price,
                    int(hold_sec), pnl_now, self._sl_pct, rsi_now, self._last_atr_pct, momentum_60s, trail_tag,
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
            # Update class-level live P&L (shared, so 2nd position gate can check)
            ScalpStrategy._live_pnl[self.pair] = self._calc_pnl_pct(current_price)

            # Write live position state to DB every 10s (dashboard reads this)
            if self._tick_count % 2 == 0:
                await self._update_position_state_in_db(current_price)

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

        # ── No position: look for PURE 2-of-4 signal entry ────────────
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

        # ── COOLDOWN: pause after SL hit (PER PAIR) ────────────────
        pair_sl_time = ScalpStrategy._pair_last_sl_time.get(self._base_asset, 0.0)
        sl_cooldown_remaining = pair_sl_time + self.SL_COOLDOWN_SECONDS - now
        if sl_cooldown_remaining > 0:
            if self._tick_count % 12 == 0:
                self.logger.info(
                    "[%s] SL COOLDOWN — %.0fs remaining before new entries",
                    self.pair, sl_cooldown_remaining,
                )
            return signals

        # ── STREAK PAUSE: after N consecutive losses on THIS PAIR ───
        pair_pause_until = ScalpStrategy._pair_streak_pause_until.get(self._base_asset, 0.0)
        if now < pair_pause_until:
            remaining = pair_pause_until - now
            pair_losses = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
            if self._tick_count % 12 == 0:
                self.logger.info(
                    "[%s] STREAK PAUSE — %d consecutive losses on %s, %.0fs remaining",
                    self.pair, pair_losses, self._base_asset, remaining,
                )
            return signals

        # ── 2ND POSITION GATE: only if 1st is breakeven+ and signal 3/4+ ─
        if total_scalp == 1:
            # Check if any existing scalp position is losing via class-level tracker
            first_losing = self._is_any_scalp_losing()
            if first_losing:
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "[%s] 2ND POS BLOCKED — 1st position is losing, wait for green",
                        self.pair,
                    )
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
        min_balance = self.MIN_NOTIONAL_SPOT if self._exchange_id == "binance" else 1.00
        if available < min_balance:
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] Insufficient %s balance: $%.2f",
                    self.pair, self._exchange_id, available,
                )
            return signals

        # ── 15m trend (INFO ONLY — logged but never gates entries) ─────
        trend_15m = self._get_15m_trend()

        # ── Adaptive widening: if idle 30+ min, loosen thresholds 20% ─
        idle_seconds = now - self._last_position_exit
        is_widened = idle_seconds >= self.IDLE_WIDEN_SECONDS

        # ── Quality momentum detection (2-of-4 with trend soft weight) ──
        entry = self._detect_quality_entry(
            current_price, rsi_now, vol_ratio,
            momentum_60s, momentum_120s, momentum_300s,
            bb_upper, bb_lower,
            trend_15m,
            widened=is_widened,
            df=df,
        )

        if entry is not None:
            side, reason, use_limit, signal_strength = entry

            # ── PER-PAIR STRENGTH GATE: weak pairs need stronger signals ──
            min_strength = self.PAIR_MIN_STRENGTH.get(self._base_asset, 2)
            if signal_strength < min_strength:
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "[%s] STRENGTH GATE — %s needs %d/4+ but got %d/4, skipping",
                        self.pair, self._base_asset, min_strength, signal_strength,
                    )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                }
                return signals

            # ── 2ND POSITION: require 3/4+ signal strength ────────────
            if total_scalp == 1 and signal_strength < self.MIN_STRENGTH_FOR_2ND:
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "[%s] 2ND POS SKIPPED — strength %d/4 < %d required",
                        self.pair, signal_strength, self.MIN_STRENGTH_FOR_2ND,
                    )
                # Still update signal state for options
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                }
                return signals

            # ── Dynamic capital allocation based on signal strength ────
            amount = self._calculate_position_size_dynamic(
                current_price, available, signal_strength, total_scalp,
            )
            if amount is None:
                return signals

            # Share signal state with options strategy
            self.last_signal_state = {
                "side": side,
                "reason": reason,
                "strength": signal_strength,
                "trend_15m": trend_15m,
                "rsi": rsi_now,
                "momentum_60s": momentum_60s,
                "current_price": current_price,
                "timestamp": time.monotonic(),
            }
            # Clear post-streak gate on successful entry (first trade back done)
            if ScalpStrategy._pair_post_streak.get(self._base_asset, False):
                self.logger.info(
                    "[%s] POST-STREAK GATE PASSED — %s re-entry with %d/4 signals",
                    self.pair, self._base_asset, signal_strength,
                )
                ScalpStrategy._pair_post_streak[self._base_asset] = False

            soul_msg = _soul_check("quality entry")
            self.logger.info(
                "[%s] SIGNAL ENTRY — %s | strength=%d/4 | Soul: %s",
                self.pair, reason, signal_strength, soul_msg,
            )
            order_type = "limit" if use_limit else "market"
            signals.append(self._build_entry_signal(side, current_price, amount, reason, order_type))
        else:
            # Update signal state even when no entry (options can see what's happening)
            self.last_signal_state = {
                "side": None,
                "reason": None,
                "strength": 0,
                "trend_15m": trend_15m,
                "rsi": rsi_now,
                "momentum_60s": momentum_60s,
                "current_price": current_price,
                "timestamp": time.monotonic(),
            }
            # Log scanning status every 30 seconds (not every tick)
            if self._tick_count % 6 == 0:
                idle_sec = int(idle_seconds)
                widen_tag = " [WIDENED]" if is_widened else ""
                eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(is_widened)
                cooldown_tag = ""
                pair_losses = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
                if pair_losses > 0:
                    cooldown_tag = f" streak={pair_losses}"
                self.logger.info(
                    "[%s] WAITING %ds%s | 15m=%s | $%.2f | mom60=%+.3f%%/%.2f | RSI=%.1f/%d/%d | "
                    "vol=%.1fx/%.1f | BB[%.2f-%.2f]%s",
                    self.pair, idle_sec, widen_tag, trend_15m, current_price,
                    momentum_60s, eff_mom,
                    rsi_now, eff_rsi_l, eff_rsi_s,
                    vol_ratio, eff_vol,
                    bb_lower, bb_upper,
                    cooldown_tag,
                )

        return signals

    # ======================================================================
    # SIGNAL ENTRY — 2-of-4 with 15m trend soft weight
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
        momentum_300s: float,
        bb_upper: float,
        bb_lower: float,
        trend_15m: str = "neutral",
        widened: bool = False,
        df: pd.DataFrame | None = None,
    ) -> tuple[str, str, bool, int] | None:
        """Detect quality momentum using 2-of-4 signals with 15m trend soft weight.

        Returns (side, reason, use_limit, signal_count) or None.

        SOFT TREND WEIGHT (15m):
        - Trend-aligned: 2/4 signals required (standard)
        - Counter-trend: 3/4 signals required (harder to go against trend)
        - Neutral: 2/4 for both directions

        Signals (up to 6, but counted as max 4 for threshold):
        1. Momentum 60s: 0.15%+ move in 60s
        2. Volume: 1.2x+ spike
        3. RSI: < 40 oversold (long) or > 60 overbought (short)
        4. BB mean-reversion: price in bottom 15% of BB → long, top 15% → short
        5. Momentum 5m: 0.30%+ move over 5 candles (slow bleed detection)
        6. Trend continuation: new 15-candle low/high + volume > average

        Signals 5 and 6 are BONUS — they count toward the total but the
        threshold is still expressed as N-of-4 scale (2/4 or 3/4).
        """
        can_short = self.is_futures and config.delta.enable_shorting

        # ── Get effective thresholds (may be widened, but SAME for both dirs) ─
        eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(widened)
        widen_tag = " WIDE" if widened else ""

        # ── 15M TREND SOFT WEIGHT: determine required signal count ─────────
        # Trend-aligned = easier (2/4), counter-trend = harder (3/4)
        if trend_15m == "bearish":
            required_long = 3   # counter-trend long: harder
            required_short = 2  # trend-aligned short: standard
        elif trend_15m == "bullish":
            required_long = 2   # trend-aligned long: standard
            required_short = 3  # counter-trend short: harder
        else:  # neutral
            required_long = 2
            required_short = 2

        # ── Post-streak gate: first trade after streak pause needs 3/4 ─────
        if ScalpStrategy._pair_post_streak.get(self._base_asset, False):
            required_long = max(required_long, self.POST_STREAK_STRENGTH)
            required_short = max(required_short, self.POST_STREAK_STRENGTH)

        # ── Count bullish and bearish signals ──────────────────────────────
        bull_signals: list[str] = []
        bear_signals: list[str] = []

        # 1. Momentum (60s move) — same threshold for both directions
        if momentum_60s >= eff_mom:
            bull_signals.append(f"MOM:{momentum_60s:+.2f}%")
        if momentum_60s <= -eff_mom:
            bear_signals.append(f"MOM:{momentum_60s:+.2f}%")

        # 2. Volume spike — direction from candle
        if vol_ratio >= eff_vol:
            if momentum_60s > 0:
                bull_signals.append(f"VOL:{vol_ratio:.1f}x")
            elif momentum_60s < 0:
                bear_signals.append(f"VOL:{vol_ratio:.1f}x")
            else:
                # Flat candle with volume — add to both, signal count decides
                bull_signals.append(f"VOL:{vol_ratio:.1f}x")
                bear_signals.append(f"VOL:{vol_ratio:.1f}x")

        # 3. RSI extreme — same thresholds always
        if rsi_now < eff_rsi_l:
            bull_signals.append(f"RSI:{rsi_now:.0f}<{eff_rsi_l:.0f}")
        if rsi_now > eff_rsi_s:
            bear_signals.append(f"RSI:{rsi_now:.0f}>{eff_rsi_s:.0f}")

        # 4. BB mean-reversion (ONE interpretation only):
        #    Price near lower BB → long (oversold, expect bounce)
        #    Price near upper BB → short (overbought, expect fade)
        bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1.0
        bb_position = (price - bb_lower) / bb_range  # 0.0 = lower, 1.0 = upper

        if bb_position <= self.BB_MEAN_REVERT_LOWER:
            bull_signals.append(f"BB:low@{bb_position:.0%}")
        if bb_position >= self.BB_MEAN_REVERT_UPPER and can_short:
            bear_signals.append(f"BB:high@{bb_position:.0%}")

        # 5. Multi-timeframe momentum (5-min / 300s) — slow bleed detection
        #    Catches moves that are too slow for 60s threshold but significant over 5m
        if momentum_300s >= self.MOMENTUM_5M_MIN_PCT:
            if f"MOM:" not in " ".join(bull_signals):  # don't double-count if 60s already fired
                bull_signals.append(f"MOM5m:{momentum_300s:+.2f}%")
        if momentum_300s <= -self.MOMENTUM_5M_MIN_PCT:
            if f"MOM:" not in " ".join(bear_signals):
                bear_signals.append(f"MOM5m:{momentum_300s:+.2f}%")

        # 6. Trend continuation: new 15-candle low/high + volume > average
        #    "Price making new lows with volume = sellers in control"
        if df is not None and len(df) >= self.TREND_CONT_CANDLES + 1:
            close_arr = df["close"].values
            volume_arr = df["volume"].values
            current_close = float(close_arr[-1])
            lookback = close_arr[-(self.TREND_CONT_CANDLES + 1):-1]  # previous 15 candles
            avg_vol = float(volume_arr[-(self.TREND_CONT_CANDLES + 1):-1].mean())
            current_vol = float(volume_arr[-1])

            # New low in last 15 candles + volume above average → SHORT signal
            if current_close < float(lookback.min()) and current_vol >= avg_vol * self.TREND_CONT_VOL_RATIO:
                if can_short:
                    bear_signals.append(f"TCONT:newLow+vol{current_vol/avg_vol:.1f}x")

            # New high in last 15 candles + volume above average → LONG signal
            if current_close > float(lookback.max()) and current_vol >= avg_vol * self.TREND_CONT_VOL_RATIO:
                bull_signals.append(f"TCONT:newHigh+vol{current_vol/avg_vol:.1f}x")

        # ── Check required signals (LONG) — trend-weighted ────────────────
        # NO fee filter — if 2/4 signals fire, ENTER. The signal system IS the filter.
        # RSI + VOL is a valid entry even when momentum is flat (price about to move).
        if len(bull_signals) >= required_long:
            req_tag = f" req={required_long}/4" if required_long > 2 else ""
            reason = f"LONG {len(bull_signals)}/4: {' + '.join(bull_signals)} [15m={trend_15m}]{req_tag}{widen_tag}"
            use_limit = "MOM" not in bull_signals[0]
            return ("long", reason, use_limit, len(bull_signals))

        # ── Check required signals (SHORT) — trend-weighted ───────────────
        if len(bear_signals) >= required_short and can_short:
            req_tag = f" req={required_short}/4" if required_short > 2 else ""
            reason = f"SHORT {len(bear_signals)}/4: {' + '.join(bear_signals)} [15m={trend_15m}]{req_tag}{widen_tag}"
            use_limit = "MOM" not in bear_signals[0]
            return ("short", reason, use_limit, len(bear_signals))

        return None

    # ======================================================================
    # EXIT LOGIC — RIDE WINNERS, CUT LOSERS
    # ======================================================================

    def _update_trail_distance(self, pnl_pct: float) -> float:
        """Update dynamic trail distance based on current profit level.

        Trail distance ONLY increases (never tightens once widened).
        Returns the current trail distance percentage.
        """
        # Walk through tiers from highest to lowest
        for min_profit, distance in reversed(self.TRAIL_TIERS):
            if pnl_pct >= min_profit and distance > self._trail_distance_pct:
                old = self._trail_distance_pct
                self._trail_distance_pct = distance
                locked_min = pnl_pct - distance
                cap_locked = locked_min * self.leverage
                self.logger.info(
                    "[%s] TRAIL WIDENED %.2f%% → %.2f%% at +%.2f%% profit "
                    "(locks +%.2f%% min = +%.0f%% capital at %dx)",
                    self.pair, old, distance, pnl_pct,
                    locked_min, cap_locked, self.leverage,
                )
                break
        return self._trail_distance_pct

    def _check_exits(self, current_price: float, rsi_now: float, momentum_60s: float) -> list[Signal]:
        """Check exit conditions — QUICK SCALP style.

        Priority:
        1. Stop loss — ATR-dynamic, cut losers fast
        2. Breakeven SL — tighten to entry if not profitable by 60s
        3. Profit pullback — if peak > 0.50% and drops 40% from peak, EXIT
        3.5. Profit decay — if peak was > 0.30% and drops to < 0.10%, EXIT
        4. Signal reversal — exit at +0.30% profit if reversal detected
        5. Trailing stop — activates at +0.20%, dynamic trail widens with profit
        6. Timeout — 5 min max (only if not trailing)
        7. Flatline — 2 min with < 0.05% move = dead momentum
        """
        signals: list[Signal] = []
        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)

        # Track peak unrealized P&L (for decay exit)
        self._peak_unrealized_pnl = max(self._peak_unrealized_pnl, pnl_pct)

        # ── 0. HARD SAFETY EXIT — catch stuck positions ─────────────────
        # If position is past timeout AND losing, force close regardless of SL width.
        # This prevents positions from surviving forever across restarts when
        # SL is ATR-widened but the trade is clearly dead.
        if hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
            self.logger.warning(
                "[%s] SAFETY EXIT — %ds past timeout AND losing %.2f%% — force closing",
                self.pair, int(hold_seconds), pnl_pct,
            )
            return self._do_exit(
                current_price, pnl_pct, self.position_side or "long",
                "SAFETY", hold_seconds,
            )

        if self.position_side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)

            # ── 1. STOP LOSS — ATR-dynamic, cut losers fast ─────────────
            sl_price = self.entry_price * (1 - self._sl_pct / 100)
            if current_price <= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] SL HIT at %.2f%% (SL=%.2f%% ATR=%.3f%%) — cutting loss | %s",
                    self.pair, pnl_pct, self._sl_pct, self._last_atr_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "long", "SL", hold_seconds)

            # ── 2. BREAKEVEN SL — tighten after 2 min if not profitable ─
            if hold_seconds >= self.BREAKEVEN_AFTER_SECONDS and pnl_pct <= 0 and not self._trailing_active:
                self.logger.info(
                    "[%s] BREAKEVEN EXIT — %ds in, PnL=%.2f%% not profitable",
                    self.pair, int(hold_seconds), pnl_pct,
                )
                return self._do_exit(current_price, pnl_pct, "long", "BREAKEVEN", hold_seconds)

            # ── 3. PROFIT PULLBACK — protect gains if fading ────────────
            # If we peaked above 0.50% and current drops 40% from peak → exit
            peak_pnl = ((self.highest_since_entry - self.entry_price) / self.entry_price) * 100
            if peak_pnl >= self.PROFIT_PULLBACK_MIN_PEAK and pnl_pct > 0:
                pullback_pct = ((peak_pnl - pnl_pct) / peak_pnl) * 100 if peak_pnl > 0 else 0
                if pullback_pct >= self.PROFIT_PULLBACK_PCT:
                    self.logger.info(
                        "[%s] PROFIT PULLBACK — peak=+%.2f%% now=+%.2f%% (%.0f%% pullback)",
                        self.pair, peak_pnl, pnl_pct, pullback_pct,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "PULLBACK", hold_seconds)

            # ── 3.5. PROFIT DECAY — don't let green go to zero ────────
            if (self._peak_unrealized_pnl >= self.PROFIT_DECAY_PEAK_MIN
                    and pnl_pct < self.PROFIT_DECAY_EXIT_AT):
                self.logger.info(
                    "[%s] PROFIT DECAY — peak was +%.2f%% but decayed to +%.2f%% (< %.2f%%) | cutting",
                    self.pair, self._peak_unrealized_pnl, pnl_pct, self.PROFIT_DECAY_EXIT_AT,
                )
                return self._do_exit(current_price, pnl_pct, "long", "DECAY", hold_seconds)

            # ── 4. SIGNAL REVERSAL — exit early when in profit ─────────
            if pnl_pct >= self.REVERSAL_MIN_PROFIT_PCT:
                rsi_crossed_70 = rsi_now > self.RSI_REVERSAL_LONG and self._prev_rsi <= self.RSI_REVERSAL_LONG
                momentum_reversed = momentum_60s < self.MOMENTUM_REVERSAL_PCT

                if rsi_crossed_70:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] RSI %.1f crossed 70 at +%.2f%% — taking profit | %s",
                        self.pair, rsi_now, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-RSI", hold_seconds)

                if momentum_reversed:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] Momentum flipped %+.3f%% at +%.2f%% — taking profit | %s",
                        self.pair, momentum_60s, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "REVERSAL-MOM", hold_seconds)

            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                self._update_trail_distance(pnl_pct)
                trail_price = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ON at +%.2f%% — SL at $%.2f (%.2f%% behind peak $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self._trail_distance_pct,
                    self.highest_since_entry, soul_msg,
                )

            if self._trailing_active:
                # Widen trail distance as profit grows (never tightens)
                self._update_trail_distance(pnl_pct)
                trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                if current_price <= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] TRAIL HIT at $%.2f (peak $%.2f, trail=%.2f%%) PnL=+%.2f%% | %s",
                        self.pair, trail_stop, self.highest_since_entry,
                        self._trail_distance_pct, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "long", "TRAIL", hold_seconds)
                # Log trail status periodically
                if self._tick_count % 12 == 0:
                    dist = ((self.highest_since_entry - current_price) / self.highest_since_entry * 100)
                    self.logger.info(
                        "[%s] RIDING +%.2f%% | peak=$%.2f trail=$%.2f (%.2f%%) dist=%.2f%%",
                        self.pair, pnl_pct, self.highest_since_entry, trail_stop,
                        self._trail_distance_pct, dist,
                    )

            # ── 6. TIMEOUT — 5 min max (skip if trailing) ───────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS and not self._trailing_active:
                return self._do_exit(current_price, pnl_pct, "long", "TIMEOUT", hold_seconds)

            # ── 7. FLATLINE — 2 min flat = momentum dead ────────────────
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "long", "FLAT", hold_seconds)

        elif self.position_side == "short":
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

            # ── 1. STOP LOSS — ATR-dynamic ────────────────────────────
            sl_price = self.entry_price * (1 + self._sl_pct / 100)
            if current_price >= sl_price:
                soul_msg = _soul_check("loss")
                self.logger.info(
                    "[%s] SL HIT at %.2f%% (SL=%.2f%% ATR=%.3f%%) — cutting loss | %s",
                    self.pair, pnl_pct, self._sl_pct, self._last_atr_pct, soul_msg,
                )
                return self._do_exit(current_price, pnl_pct, "short", "SL", hold_seconds)

            # ── 2. BREAKEVEN SL — tighten after 2 min if not profitable ─
            if hold_seconds >= self.BREAKEVEN_AFTER_SECONDS and pnl_pct <= 0 and not self._trailing_active:
                self.logger.info(
                    "[%s] BREAKEVEN EXIT — %ds in, PnL=%.2f%% not profitable",
                    self.pair, int(hold_seconds), pnl_pct,
                )
                return self._do_exit(current_price, pnl_pct, "short", "BREAKEVEN", hold_seconds)

            # ── 3. PROFIT PULLBACK — protect gains if fading ────────────
            peak_pnl = ((self.entry_price - self.lowest_since_entry) / self.entry_price) * 100
            if peak_pnl >= self.PROFIT_PULLBACK_MIN_PEAK and pnl_pct > 0:
                pullback_pct = ((peak_pnl - pnl_pct) / peak_pnl) * 100 if peak_pnl > 0 else 0
                if pullback_pct >= self.PROFIT_PULLBACK_PCT:
                    self.logger.info(
                        "[%s] PROFIT PULLBACK — peak=+%.2f%% now=+%.2f%% (%.0f%% pullback)",
                        self.pair, peak_pnl, pnl_pct, pullback_pct,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "PULLBACK", hold_seconds)

            # ── 3.5. PROFIT DECAY — don't let green go to zero ────────
            if (self._peak_unrealized_pnl >= self.PROFIT_DECAY_PEAK_MIN
                    and pnl_pct < self.PROFIT_DECAY_EXIT_AT):
                self.logger.info(
                    "[%s] PROFIT DECAY — peak was +%.2f%% but decayed to +%.2f%% (< %.2f%%) | cutting",
                    self.pair, self._peak_unrealized_pnl, pnl_pct, self.PROFIT_DECAY_EXIT_AT,
                )
                return self._do_exit(current_price, pnl_pct, "short", "DECAY", hold_seconds)

            # ── 4. SIGNAL REVERSAL — exit early when in profit ─────────
            if pnl_pct >= self.REVERSAL_MIN_PROFIT_PCT:
                rsi_crossed_30 = rsi_now < self.RSI_REVERSAL_SHORT and self._prev_rsi >= self.RSI_REVERSAL_SHORT
                momentum_reversed = momentum_60s > abs(self.MOMENTUM_REVERSAL_PCT)

                if rsi_crossed_30:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] RSI %.1f crossed below 30 at +%.2f%% — taking short profit | %s",
                        self.pair, rsi_now, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-RSI", hold_seconds)

                if momentum_reversed:
                    soul_msg = _soul_check("exit reversal")
                    self.logger.info(
                        "[%s] Momentum flipped +%.3f%% at +%.2f%% — taking short profit | %s",
                        self.pair, momentum_60s, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "REVERSAL-MOM", hold_seconds)

            # ── 5. TRAILING STOP (dynamic distance) ──────────────────────
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                self._update_trail_distance(pnl_pct)
                trail_price = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                soul_msg = _soul_check("trailing")
                self.logger.info(
                    "[%s] TRAIL ON at +%.2f%% — SL at $%.2f (%.2f%% above low $%.2f) | %s",
                    self.pair, pnl_pct, trail_price, self._trail_distance_pct,
                    self.lowest_since_entry, soul_msg,
                )

            if self._trailing_active:
                # Widen trail distance as profit grows (never tightens)
                self._update_trail_distance(pnl_pct)
                trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                if current_price >= trail_stop:
                    soul_msg = _soul_check("exit trailing")
                    self.logger.info(
                        "[%s] TRAIL HIT at $%.2f (low $%.2f, trail=%.2f%%) PnL=+%.2f%% | %s",
                        self.pair, trail_stop, self.lowest_since_entry,
                        self._trail_distance_pct, pnl_pct, soul_msg,
                    )
                    return self._do_exit(current_price, pnl_pct, "short", "TRAIL", hold_seconds)
                if self._tick_count % 12 == 0:
                    dist = ((current_price - self.lowest_since_entry) / self.lowest_since_entry * 100)
                    self.logger.info(
                        "[%s] RIDING SHORT +%.2f%% | low=$%.2f trail=$%.2f (%.2f%%) dist=%.2f%%",
                        self.pair, pnl_pct, self.lowest_since_entry, trail_stop,
                        self._trail_distance_pct, dist,
                    )

            # ── 6. TIMEOUT — 5 min max (skip if trailing) ───────────────
            if hold_seconds >= self.MAX_HOLD_SECONDS and not self._trailing_active:
                return self._do_exit(current_price, pnl_pct, "short", "TIMEOUT", hold_seconds)

            # ── 7. FLATLINE — 2 min flat = momentum dead ────────────────
            if hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
                return self._do_exit(current_price, pnl_pct, "short", "FLAT", hold_seconds)

        return signals

    def check_exits_immediate(self, current_price: float) -> None:
        """Price-only exit check — called by WebSocket PriceFeed on every tick.

        Runs SL, breakeven, trailing, pullback, decay, timeout, flatline checks.
        Does NOT check signal reversal (needs RSI/momentum from OHLCV).
        If exit triggered: schedules async execution and marks position closed.
        """
        if not self.in_position or not self.position_side:
            return

        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)
        side = self.position_side

        # Update peak tracking
        self._peak_unrealized_pnl = max(self._peak_unrealized_pnl, pnl_pct)
        if side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)
        else:
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

        # Update live P&L for class-level tracker
        ScalpStrategy._live_pnl[self.pair] = pnl_pct

        exit_type: str | None = None

        # ── 0. HARD SAFETY EXIT — catch stuck positions ─────────────
        if hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
            exit_type = "SAFETY"

        # ── 1. STOP LOSS ──────────────────────────────────────────────
        if not exit_type and side == "long":
            sl_price = self.entry_price * (1 - self._sl_pct / 100)
            if current_price <= sl_price:
                exit_type = "SL"
        elif not exit_type and side == "short":
            sl_price = self.entry_price * (1 + self._sl_pct / 100)
            if current_price >= sl_price:
                exit_type = "SL"

        # ── 2. BREAKEVEN ──────────────────────────────────────────────
        if not exit_type and hold_seconds >= self.BREAKEVEN_AFTER_SECONDS and pnl_pct <= 0 and not self._trailing_active:
            exit_type = "BREAKEVEN"

        # ── 3. PROFIT PULLBACK ────────────────────────────────────────
        if not exit_type and pnl_pct > 0:
            if side == "long":
                peak_pnl = ((self.highest_since_entry - self.entry_price) / self.entry_price) * 100
            else:
                peak_pnl = ((self.entry_price - self.lowest_since_entry) / self.entry_price) * 100
            if peak_pnl >= self.PROFIT_PULLBACK_MIN_PEAK:
                pullback_pct = ((peak_pnl - pnl_pct) / peak_pnl) * 100 if peak_pnl > 0 else 0
                if pullback_pct >= self.PROFIT_PULLBACK_PCT:
                    exit_type = "PULLBACK"

        # ── 3.5. PROFIT DECAY ────────────────────────────────────────
        if not exit_type and self._peak_unrealized_pnl >= self.PROFIT_DECAY_PEAK_MIN and pnl_pct < self.PROFIT_DECAY_EXIT_AT:
            exit_type = "DECAY"

        # ── 5. TRAILING STOP ─────────────────────────────────────────
        if not exit_type:
            if pnl_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                self._update_trail_distance(pnl_pct)

            if self._trailing_active:
                self._update_trail_distance(pnl_pct)
                if side == "long":
                    trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                    if current_price <= trail_stop:
                        exit_type = "TRAIL"
                else:
                    trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                    if current_price >= trail_stop:
                        exit_type = "TRAIL"

        # ── 6. TIMEOUT ───────────────────────────────────────────────
        if not exit_type and hold_seconds >= self.MAX_HOLD_SECONDS and not self._trailing_active:
            exit_type = "TIMEOUT"

        # ── 7. FLATLINE ──────────────────────────────────────────────
        if not exit_type and hold_seconds >= self.FLATLINE_SECONDS and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT:
            exit_type = "FLAT"

        # ── EXECUTE EXIT ─────────────────────────────────────────────
        if exit_type:
            self.logger.info(
                "[%s] WS EXIT %s — %s @ $%.2f PnL=%+.2f%% (%+.1f%% capital at %dx) hold=%ds",
                self.pair, exit_type, side, current_price,
                pnl_pct, pnl_pct * self.leverage, self.leverage,
                int(hold_seconds),
            )
            # Build exit signals and schedule execution
            signals = self._do_exit(current_price, pnl_pct, side, f"WS-{exit_type}", hold_seconds)
            for signal in signals:
                asyncio.get_running_loop().create_task(self._execute_ws_exit(signal))

    async def _execute_ws_exit(self, signal: Signal) -> None:
        """Execute a WS-triggered exit signal."""
        try:
            if self.risk_manager.approve_signal(signal):
                order = await self.executor.execute(signal)
                if order is not None:
                    self.logger.info("[%s] WS exit order filled", self.pair)
                else:
                    self.logger.warning("[%s] WS exit order failed/skipped", self.pair)
        except Exception:
            self.logger.exception("[%s] WS exit execution error", self.pair)

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

    async def _update_position_state_in_db(self, current_price: float) -> None:
        """Write live position state to the trades table so dashboard shows real state.

        Updates: position_state, trail_stop_price, current_pnl, current_price, peak_pnl
        Runs every ~10s (throttled in caller).
        """
        if not self.executor.db or not self.executor.db.is_connected:
            return
        try:
            pnl_pct = self._calc_pnl_pct(current_price)

            # Determine position state
            if self._trailing_active:
                state = "trailing"
            else:
                state = "holding"

            # Calculate trail stop price (from actual peak, not current price)
            trail_stop: float | None = None
            if self._trailing_active:
                if self.position_side == "long":
                    trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                elif self.position_side == "short":
                    trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)

            # Peak P&L (highest/lowest price relative to entry)
            if self.position_side == "long" and self.entry_price > 0:
                peak_pnl = ((self.highest_since_entry - self.entry_price) / self.entry_price) * 100
            elif self.position_side == "short" and self.entry_price > 0:
                peak_pnl = ((self.entry_price - self.lowest_since_entry) / self.entry_price) * 100
            else:
                peak_pnl = 0.0

            # Find the open trade and update it
            open_trade = await self.executor.db.get_open_trade(
                pair=self.pair, exchange=self._exchange_id, strategy="scalp",
            )
            if open_trade:
                await self.executor.db.update_trade(open_trade["id"], {
                    "position_state": state,
                    "trail_stop_price": round(trail_stop, 8) if trail_stop else None,
                    "current_pnl": round(pnl_pct, 4),
                    "current_price": round(current_price, 8),
                    "peak_pnl": round(peak_pnl, 4),
                })
        except Exception:
            # Non-critical — don't crash the trade loop for a dashboard update
            self.logger.debug("[%s] Failed to update position state in DB", self.pair)

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
            # Spot sizing: use CAPITAL_PCT_SPOT of available balance
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            capital = exchange_capital * (self.capital_pct / 100)
            capital = min(capital, available)

            # Enforce minimum notional — skip if below $6 (avoids dust on exit)
            if capital < self.MIN_NOTIONAL_SPOT:
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] Spot order $%.2f < $%.2f min notional — skipping",
                        self.pair, capital, self.MIN_NOTIONAL_SPOT,
                    )
                return None

            amount = capital / current_price
            self.logger.info(
                "[%s] Sizing (spot): $%.2f → %.8f %s (%.0f%% of $%.2f avail)",
                self.pair, capital, amount,
                self.pair.split("/")[0] if "/" in self.pair else self.pair,
                self.capital_pct, available,
            )

        return amount

    def _get_pair_win_rate(self) -> tuple[float, int]:
        """Get win rate for this pair from recent trade history.

        Returns (win_rate 0.0-1.0, total_trades).
        """
        history = ScalpStrategy._pair_trade_history.get(self._base_asset, [])
        if not history:
            return 0.5, 0  # default 50% if no data yet
        wins = sum(1 for w in history if w)
        return wins / len(history), len(history)

    def _get_adaptive_alloc_pct(self, signal_strength: int, total_open: int) -> float:
        """Performance-based allocation — better pairs get more capital.

        Base allocation from PAIR_ALLOC_PCT (tuned by historical performance).
        Adaptive adjustment: if last 5 trades WR < 20%, reduce to minimum.
        If WR > 60%, boost by 20%.
        """
        base_alloc = self.PAIR_ALLOC_PCT.get(self._base_asset, 15.0)
        win_rate, n_trades = self._get_pair_win_rate()

        # Adaptive adjustment based on recent performance
        if n_trades >= 3:  # need at least 3 trades for adjustment
            if win_rate < self.PERF_LOW_WR_THRESHOLD:
                # Very low WR: reduce to 5% minimum
                base_alloc = max(5.0, base_alloc * 0.25)
            elif win_rate > self.PERF_HIGH_WR_THRESHOLD:
                # High WR: boost by 20% (capped at 70%)
                base_alloc = min(70.0, base_alloc * 1.20)

        # 2nd position gets smaller share
        if total_open >= 1:
            base_alloc = base_alloc * 0.6  # 60% of normal for 2nd position

        return max(5.0, min(70.0, base_alloc))

    def _calculate_position_size_dynamic(
        self, current_price: float, available: float,
        signal_strength: int, total_open: int,
    ) -> float | None:
        """Performance-based capital allocation per pair.

        Each pair gets a base allocation % tuned by historical performance:
        - XRP: 50% (best performer), ETH: 30%, BTC: 15%, SOL: 5%
        - Adaptive: if last 5 trades < 20% WR → reduce to minimum
        - Adaptive: if last 5 trades > 60% WR → boost 20%
        - 2nd position: 60% of normal allocation
        """
        exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
        if exchange_capital <= 0:
            return None

        alloc_pct = self._get_adaptive_alloc_pct(signal_strength, total_open)
        win_rate, n_trades = self._get_pair_win_rate()

        if self.is_futures:
            budget = exchange_capital * (alloc_pct / 100)
            budget = min(budget, available)

            from alpha.trade_executor import DELTA_CONTRACT_SIZE
            contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0)
            if contract_size <= 0:
                return None

            one_contract_collateral = (contract_size * current_price) / self.leverage
            if one_contract_collateral > budget:
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] 1 contract needs $%.2f > $%.2f budget (%.0f%% alloc) — skipping",
                        self.pair, one_contract_collateral, budget, alloc_pct,
                    )
                return None

            # Cap at risk manager's max_position_pct
            max_position_value = exchange_capital * (self.risk_manager.max_position_pct / 100)
            budget = min(budget, max_position_value)

            max_affordable = int(budget / one_contract_collateral)
            contracts = max(1, min(max_affordable, self._max_contracts))
            total_collateral = contracts * one_contract_collateral
            amount = contracts * contract_size

            self.logger.info(
                "[%s] Allocation: %s %.0f%% ($%.2f) based on WR=%.0f%% (%d trades) | "
                "%d contracts, collateral=$%.2f (%dx), strength=%d/4",
                self.pair, self._base_asset, alloc_pct,
                total_collateral, win_rate * 100, n_trades,
                contracts, total_collateral, self.leverage, signal_strength,
            )
        else:
            # Spot: same performance-based allocation
            capital = exchange_capital * (alloc_pct / 100)
            capital = min(capital, available)

            if capital < self.MIN_NOTIONAL_SPOT:
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] Spot $%.2f < $%.2f min (%.0f%% alloc) — skipping",
                        self.pair, capital, self.MIN_NOTIONAL_SPOT, alloc_pct,
                    )
                return None

            amount = capital / current_price
            self.logger.info(
                "[%s] Allocation: %s %.0f%% ($%.2f) based on WR=%.0f%% (%d trades) | spot",
                self.pair, self._base_asset, alloc_pct, capital,
                win_rate * 100, n_trades,
            )
            self.logger.info(
                "[%s] DynSize (spot): $%.2f → %.8f %s (%.0f%% of $%.2f, strength=%d/4)",
                self.pair, capital, amount,
                self.pair.split("/")[0] if "/" in self.pair else self.pair,
                alloc_pct, exchange_capital, signal_strength,
            )

        return amount

    # ======================================================================
    # SIGNAL BUILDERS
    # ======================================================================

    def _build_entry_signal(
        self, side: str, price: float, amount: float, reason: str,
        order_type: str = "market",
    ) -> Signal:
        """Build an entry signal with ATR-dynamic SL. Trail handles the TP."""
        self.logger.info(
            "[%s] %s -> %s entry (%s) SL=%.2f%% TP=%.2f%% ATR=%.3f%%",
            self.pair, reason, side.upper(), order_type,
            self._sl_pct, self._tp_pct, self._last_atr_pct,
        )

        if side == "long":
            sl = price * (1 - self._sl_pct / 100)
            tp = price * (1 + self._tp_pct / 100)
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
                metadata={"pending_side": "long", "pending_amount": amount,
                          "tp_price": tp, "sl_price": sl,
                          "sl_pct": self._sl_pct, "tp_pct": self._tp_pct,
                          "atr_pct": self._last_atr_pct},
            )
        else:  # short
            sl = price * (1 + self._sl_pct / 100)
            tp = price * (1 - self._tp_pct / 100)
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
                metadata={"pending_side": "short", "pending_amount": amount,
                          "tp_price": tp, "sl_price": sl,
                          "sl_pct": self._sl_pct, "tp_pct": self._tp_pct,
                          "atr_pct": self._last_atr_pct},
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

    def _is_any_scalp_losing(self) -> bool:
        """Check if any open scalp position is currently in the red.

        Uses the class-level _live_pnl dict which is updated every tick
        by whichever scalp instance is managing that position.
        """
        for pair, pnl in ScalpStrategy._live_pnl.items():
            if pair != self.pair and pnl < 0:
                return True
        return False

    def _open_position(self, side: str, price: float, amount: float = 0.0) -> None:
        self.in_position = True
        self.position_side = side
        self.entry_price = price
        self.entry_amount = amount
        self.entry_time = time.monotonic()
        self.highest_since_entry = price
        self.lowest_since_entry = price
        self._trailing_active = False
        self._trail_distance_pct = self.TRAILING_DISTANCE_PCT  # reset to initial tier
        self._peak_unrealized_pnl = 0.0  # reset peak P&L tracker for decay exit
        self._in_position_tick = 0  # reset tick counter for OHLCV refresh cadence
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

        now = time.monotonic()

        # Track per-pair win/loss history (for adaptive allocation)
        is_win = pnl_pct >= 0
        if self._base_asset not in ScalpStrategy._pair_trade_history:
            ScalpStrategy._pair_trade_history[self._base_asset] = []
        ScalpStrategy._pair_trade_history[self._base_asset].append(is_win)
        # Keep only last PERF_WINDOW trades
        if len(ScalpStrategy._pair_trade_history[self._base_asset]) > self.PERF_WINDOW:
            ScalpStrategy._pair_trade_history[self._base_asset] = \
                ScalpStrategy._pair_trade_history[self._base_asset][-self.PERF_WINDOW:]

        if pnl_pct >= 0:
            self.hourly_wins += 1
            # Win resets consecutive loss streak for THIS PAIR
            ScalpStrategy._pair_consecutive_losses[self._base_asset] = 0
            ScalpStrategy._pair_post_streak[self._base_asset] = False
        else:
            self.hourly_losses += 1
            # Track consecutive losses PER PAIR (BTC losses don't pause XRP)
            prev = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
            ScalpStrategy._pair_consecutive_losses[self._base_asset] = prev + 1

            # SL cooldown: pause THIS PAIR for 2 min after SL
            if exit_type.lower() in ("sl", "ws-sl"):
                ScalpStrategy._pair_last_sl_time[self._base_asset] = now
                self.logger.info(
                    "[%s] SL COOLDOWN SET — no new %s entries for %ds",
                    self.pair, self._base_asset, self.SL_COOLDOWN_SECONDS,
                )

            # Streak pause: after N consecutive losses on THIS PAIR
            pair_losses = ScalpStrategy._pair_consecutive_losses[self._base_asset]
            if pair_losses >= self.CONSECUTIVE_LOSS_LIMIT:
                ScalpStrategy._pair_streak_pause_until[self._base_asset] = now + self.STREAK_PAUSE_SECONDS
                ScalpStrategy._pair_post_streak[self._base_asset] = True  # first trade back needs 3/4
                self.logger.warning(
                    "[%s] STREAK PAUSE — %d consecutive %s losses! Pausing %s for %ds",
                    self.pair, pair_losses, self._base_asset,
                    self._base_asset, self.STREAK_PAUSE_SECONDS,
                )

        hold_sec = int(now - self.entry_time)
        duration = f"{hold_sec // 60}m{hold_sec % 60:02d}s" if hold_sec >= 60 else f"{hold_sec}s"

        # Log with fee breakdown for visibility
        fee_ratio = abs(gross_pnl / est_fees) if est_fees > 0 else 0
        pair_losses = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
        streak_tag = f" streak={pair_losses}" if pair_losses > 0 else ""
        self.logger.info(
            "[%s] CLOSED %s %+.2f%% price (%+.1f%% capital at %dx) | "
            "Gross=$%.4f Net=$%.4f fees=$%.4f (%.1fx) | %s | W/L=%d/%d%s",
            self.pair, exit_type.upper(), pnl_pct, capital_pnl_pct, self.leverage,
            gross_pnl, net_pnl, est_fees, fee_ratio, duration,
            self.hourly_wins, self.hourly_losses, streak_tag,
        )

        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self.entry_amount = 0.0
        self._trailing_active = False
        self._trail_distance_pct = self.TRAILING_DISTANCE_PCT
        self._peak_unrealized_pnl = 0.0  # reset for next trade
        self._in_position_tick = 0  # reset tick counter
        self._last_position_exit = now
        ScalpStrategy._live_pnl.pop(self.pair, None)  # clean up live P&L tracker

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
