"""Alpha v6.3 — 11-SIGNAL ARSENAL + SETUP TRACKING.

PHILOSOPHY: Two strong positions beat four weak ones. Focus capital on the
best signals. After a loss, pause THAT PAIR. Track which setup made each trade.
SL/TP adapt per-pair. SOL DISABLED (0% win rate).

CHANGES FROM v6.2:
  - Keltner Channel indicator for BB Squeeze detection
  - Signal #8: BB Squeeze Breakout (BB inside KC → squeeze, breakout + volume)
  - Signal #9: Liquidity Sweep (sweep swing H/L, reclaim + RSI divergence)
  - Signal #10: Fair Value Gap fill (3-candle imbalance gap + price retracing)
  - Signal #11: Volume Divergence (price vs volume trend divergence)
  - 4 new setup types: BB_SQUEEZE, LIQ_SWEEP, FVG_FILL, VOL_DIVERGENCE

CHANGES FROM v6.1 (in v6.2):
  - VWAP indicator: 30-candle session VWAP for price-value alignment
  - 9/21 EMA momentum ribbon for trend confirmation
  - 7th signal: VWAP Reclaim (price above VWAP + bullish EMA ribbon)
  - Setup tracking: each trade classified (VWAP_RECLAIM, MOMENTUM_BURST, etc.)
  - setup_type stored in DB for performance analysis by setup

CHANGES FROM v5.9 (in v6.0/v6.1):
  - Entry gate: 2/4 → 3/4 minimum for ALL pairs (XRP, ETH, BTC)
  - Warmup gate: 2/4 → 3/4 (no more free passes on startup)
  - 2/4 entries were coin flips — losers at 20x leverage = -7% capital per SL hit
  - 3/4 filters out weak signals, fewer trades but higher quality

CHANGES FROM v5.8 (in v5.9):
  - Trail activates at +0.15% (was 0.35%) — any green trade gets trailed
  - Initial trail distance: 0.15% (was 0.20%) — tighter scalp lock
  - Phase 1 skip at +0.5% peak (was 1.0%) — graduate faster
  - Move SL to entry at +0.20% (was 0.30%) — breakeven protection earlier
  - Pullback exit at 30% retracement (was 40%) — don't give back profits
  - ALL trail tiers tightened: +2%=0.40, +3%=0.50, +5%=0.75
  - New +0.15% tier for instant green protection

CHANGES FROM v5.7 (in v5.8):
  - Trail tier split: +3% tier tightened, new +5% tier

CHANGES FROM v5.6 (in v5.7):
  - Momentum threshold: 0.15% → 0.08% (catch moves earlier)
  - Volume threshold: 1.2x → 0.8x (most vol is <1x)
  - BTC strength gate: 3/4 → 2/4 (same as all pairs)
  - RSI EXTREME OVERRIDE: RSI <30 or >70 = enter regardless of other signals
  - Signal scan logging: every tick shows pass/fail per condition
  - Binance spot: custom SL/TP/trail (wider for no-leverage spot)

ENTRY — 3-of-4 STRICT GATE with 11-signal arsenal:
  Signals (up to 11, counted against N/4 threshold):
  1. Momentum 60s: 0.08%+ move
  2. Volume: 0.8x average spike
  3. RSI: < 35 (long) or > 65 (short)
  4. BB mean-reversion: price near band edge
  5. Momentum 5m: 0.30%+ slow bleed
  6. Trend continuation: new 15-candle extreme + volume
  7. VWAP Reclaim: price above VWAP + 9 EMA > 21 EMA
  8. BB Squeeze: BB inside Keltner Channel → breakout + volume
  9. Liquidity Sweep: sweep swing H/L, reclaim + RSI divergence
  10. FVG Fill: 3-candle imbalance gap + price retracing into gap
  11. Volume Divergence: price vs volume trend divergence (hollow moves)
  OVERRIDE: RSI < 30 or > 70 → enter immediately (strong extreme)
  GATE: ALL pairs require 3/4+ signals (no 2/4 coin flips)
  SETUP: Each entry classified by dominant signal pattern

EXIT — 3-PHASE SYSTEM (AGGRESSIVE PROFIT LOCK):
  PHASE 1 (0-30s): HANDS OFF
    - ONLY exit on hard SL (-0.35% for ETH, -0.40% for XRP, -0.30% for BTC)
    - EXCEPTION: if peak PnL >= +0.5%, skip to Phase 2 immediately
    - 30s is enough for fill bounce to settle, SL still protects us
  PHASE 2 (30s-10 min): WATCH + TRAIL
    - If PnL > +0.20% → move SL to entry (breakeven protection)
    - If PnL > +0.15% → activate trailing (0.15% tight distance)
    - Trail tiers tightened: +2%=0.40%, +3%=0.50%, +5%=0.75%
    - Pullback exit at 30% retracement from peak
  PHASE 3 (10-30 min): TRAIL OR CUT
    - Trailing active → let it trail with tight distance
    - Still negative after 10 min → FLAT exit
    - Hard timeout at 30 min
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
    """Phase-based v6.3 — 11-signal arsenal + setup tracking.

    3-phase exit system prevents instant exits after fill bounce.
    SOL disabled (0% win rate). Per-pair SL distances.
    Binance spot uses wider SL/TP/trail (no leverage, needs room).
    RSI extreme override: <30 or >70 enters regardless of other signals.
    11 entry signals: MOM, VOL, RSI, BB, MOM5m, TCONT, VWAP, BBSQZ, LIQSWEEP, FVG, VOLDIV.
    Setup type tracked per trade (BB_SQUEEZE, LIQ_SWEEP, FVG_FILL, VOL_DIVERGENCE, etc.).
    """

    name = StrategyName.SCALP
    check_interval_sec = 5  # 5 second ticks — patient, not frantic

    # ── Per-pair SL distances — FIXED on entry, locked for 3 min ─────
    STOP_LOSS_PCT = 0.25              # default fallback (was 0.35)
    MIN_TP_PCT = 1.50                 # default TP
    PAIR_SL_FLOOR: dict[str, float] = {
        "BTC": 0.25,   # BTC: 0.25% price = 5% capital at 20x (was 0.40 = 8% capital)
        "ETH": 0.25,   # ETH: 0.25% price = 5% capital at 20x (was 0.45 = 9% capital)
        "XRP": 0.25,   # XRP: 0.25% price = 5% capital at 20x (was 0.50 = 10% capital)
        "SOL": 0.25,
    }
    PAIR_TP_FLOOR: dict[str, float] = {
        "BTC": 1.50,
        "ETH": 1.50,
        "XRP": 2.00,
        "SOL": 2.00,
    }
    ATR_SL_MULTIPLIER = 1.5
    ATR_TP_MULTIPLIER = 4.0

    # ── 3-PHASE EXIT TIMING ──────────────────────────────────────────
    PHASE1_SECONDS = 30               # 0-30s: HANDS OFF — only hard SL (was 60s, fill bounce settles by 30s)
    PHASE1_SKIP_AT_PEAK_PCT = 0.5     # if peak PnL >= +0.5% during Phase 1, skip to Phase 2 immediately
    PHASE2_SECONDS = 10 * 60          # 30s-10 min: WATCH — move SL up if profitable
    MAX_HOLD_SECONDS = 30 * 60        # 30 min hard timeout
    FLATLINE_SECONDS = 10 * 60        # 10 min flat = dead momentum (phase 3 only)
    FLATLINE_MIN_MOVE_PCT = 0.05      # "flat" means < 0.05% total move

    # ── Phase 2: move SL to entry when profitable ─────────────────────
    MOVE_SL_TO_ENTRY_PCT = 0.20       # move SL to entry at +0.20% profit (was 0.30)

    # ── Trailing (activates in phase 2+) ──────────────────────────────
    TRAILING_ACTIVATE_PCT = 0.25      # activate at +0.25% price = +5% capital at 20x (was 0.30)
    TRAILING_DISTANCE_PCT = 0.10      # initial trail: 0.10% behind peak (was 0.15)

    # ── Hard TP safety net (only if NOT trailing — ratchets protect runners) ──
    HARD_TP_CAPITAL_PCT = 10.0        # 10% capital gain → exit only if trail not active

    # ── Ratcheting profit floors — HARD FLOORS that cannot go back down ──
    # Once capital PnL reaches threshold, floor locks in. Cannot be lowered.
    # If current capital PnL drops below floor → EXIT IMMEDIATELY.
    PROFIT_RATCHETS: list[tuple[float, float]] = [
        (5.0,  0.0),    # +5% cap → floor breakeven (was +3%)
        (8.0,  3.0),    # +8% cap → floor +3% (was +5%)
        (12.0, 7.0),    # +12% cap → floor +7%
        (15.0, 10.0),   # +15% cap → floor +10%
    ]

    # ── Profit decay emergency — never give back profits ─────────────
    PROFIT_DECAY_EMERGENCY_PEAK_CAP = 3.0   # min peak capital PnL to activate
    PROFIT_DECAY_EMERGENCY_RATIO = 0.40     # exit if current < peak × 0.40 (lost 60%+)

    # ── Fee-aware minimum — don't exit tiny profits ───────────────────
    FEE_MINIMUM_GROSS_USD = 0.10      # skip exit unless gross > $0.10 (except SL/ratchet)

    # ── Signal reversal (phase 2+ only) ────────────────────────────────
    REVERSAL_MIN_PROFIT_PCT = 0.30

    # ── Dynamic trailing tiers — TIGHT, lock profits fast ─────────────
    TRAIL_TIERS: list[tuple[float, float]] = [
        (0.25, 0.10),   # +0.25% price (+5% cap): trail 0.10% (lock +0.15% = +3% cap)
        (0.50, 0.15),   # +0.50% price (+10% cap): trail 0.15% (lock +0.35% = +7% cap)
        (1.00, 0.20),   # +1.00% price (+20% cap): trail 0.20% (lock +0.80% = +16% cap)
        (2.00, 0.30),   # +2.00% price (+40% cap): trail 0.30% (lock +1.70% = +34% cap)
    ]

    # ── Signal reversal thresholds ──────────────────────────────────────
    RSI_REVERSAL_LONG = 70
    RSI_REVERSAL_SHORT = 30
    MOMENTUM_REVERSAL_PCT = -0.10

    # ── DISABLED PAIRS — skip entirely ────────────────────────────────
    DISABLED_PAIRS: set[str] = set()

    # ── Entry thresholds — 3-of-4 with 15m trend soft weight ────────────
    MOMENTUM_MIN_PCT = 0.08           # 0.08%+ move in 60s (was 0.15 — catches moves earlier)
    VOL_SPIKE_RATIO = 0.8             # volume > 0.8x average (was 1.2 — most vol is under 1x)
    RSI_EXTREME_LONG = 35             # RSI < 35 = oversold → long (was 40, too loose)
    RSI_EXTREME_SHORT = 65            # RSI > 65 = overbought → short (was 60, too loose)
    # BB mean-reversion thresholds (upper = short, lower = long):
    BB_MEAN_REVERT_UPPER = 0.85      # price in top 15% of BB → short signal
    BB_MEAN_REVERT_LOWER = 0.15      # price in bottom 15% of BB → long signal
    # Multi-timeframe momentum: 5-minute (300s) slow bleed detection
    MOMENTUM_5M_MIN_PCT = 0.30       # 0.30%+ move over 5 candles (slow bleed counts)
    # Trend continuation: new 15-candle low/high + volume confirms trend
    TREND_CONT_CANDLES = 15           # look back 15 candles for new low/high
    TREND_CONT_VOL_RATIO = 1.0       # volume must be above average (1.0x+)

    # ── RSI EXTREME OVERRIDE — enter regardless of other signals ─────
    RSI_OVERRIDE_LONG = 30            # RSI < 30 = strong oversold → long immediately
    RSI_OVERRIDE_SHORT = 70           # RSI > 70 = strong overbought → short immediately

    # ── Binance SPOT overrides — wider SL/TP/trail for no-leverage spot ──
    SPOT_SL_PCT = 2.0                 # 2% SL for spot (no leverage, needs room)
    SPOT_TP_PCT = 3.0                 # 3% TP for spot
    SPOT_TRAIL_ACTIVATE_PCT = 0.80    # trail activates at +0.80% (was 1.50 — spot rarely hits 1.5%)
    SPOT_TRAIL_DISTANCE_PCT = 0.40    # 0.40% trail distance (was 0.80 — tighter trail)
    SPOT_CAPITAL_PCT = 50.0           # use 50% of Binance balance (target ~$5)
    SPOT_MAX_POSITIONS = 1            # max 1 spot position (capital too small)

    # ── Spot-specific profit protection exits ──────────────────
    SPOT_PULLBACK_MIN_PEAK_PCT = 0.50    # peak >= 0.50% to activate
    SPOT_PULLBACK_RATIO = 0.50           # exit if current < peak * 0.50
    SPOT_DECAY_MIN_PEAK_PCT = 0.40       # peak >= 0.40% to activate
    SPOT_DECAY_EXIT_BELOW_PCT = 0.15     # exit if current < 0.15%
    SPOT_BREAKEVEN_MIN_PEAK_PCT = 0.30   # peak >= 0.30% to activate
    SPOT_BREAKEVEN_EXIT_BELOW_PCT = 0.05 # exit if current <= 0.05%

    # ── Adaptive widening (if idle too long, loosen by 20%) ──────────
    IDLE_WIDEN_SECONDS = 30 * 60      # after 30 min idle, widen thresholds
    IDLE_WIDEN_FACTOR = 0.80          # multiply thresholds by 0.80 (20% looser)

    # ── Fee awareness (Delta India incl 18% GST) ──────────────────────
    # NOTE: MIN_EXPECTED_MOVE_PCT fee filter REMOVED — it was blocking 385+
    # legitimate entries per hour (RSI+VOL signals with low momentum).
    # The 3-of-4 signal system IS the quality filter. If signals fire, enter.
    FEE_MULTIPLIER_MIN = 13.0         # 1.5% TP / 0.083% RT mixed = 18x

    # ── Position sizing — PERFORMANCE-BASED per-pair allocation ─────────
    CAPITAL_PCT_SPOT = 35.0             # 35% of Binance USDT per trade (middle of 30-40%)
    CAPITAL_PCT_FUTURES = 40.0          # base 40% — modified by PAIR_ALLOC_PCT below
    MIN_NOTIONAL_SPOT = 6.00            # $6 min to avoid dust on exit (Binance $5 min + buffer)

    # Per-pair base allocation (% of exchange capital) — tuned by performance
    PAIR_ALLOC_PCT: dict[str, float] = {
        "XRP": 50.0,   # best performer — maximize
        "ETH": 30.0,   # mixed but catches big moves
        "BTC": 20.0,   # low win rate but diversification
        "SOL": 15.0,
    }
    # Per-pair contract caps
    PAIR_MAX_CONTRACTS: dict[str, int] = {
        "BTC": 1,
        "ETH": 2,
        "XRP": 50,
        "SOL": 1,
    }
    # Minimum signal strength per pair (3/4 — filter out weak coin-flip entries)
    PAIR_MIN_STRENGTH: dict[str, int] = {
        "XRP": 3,    # was 2/4, tightened: 2/4 entries were losers
        "ETH": 3,    # was 2/4, tightened: multiple -8-9% losses on weak signals
        "BTC": 3,    # was 2/4, tightened: consistent with all pairs
        "SOL": 3,    # require 3/4 signals, no weak entries
    }
    # Adaptive: track last N trades per pair for win-rate-based adjustment
    PERF_WINDOW = 5                     # look at last 5 trades per pair
    PERF_LOW_WR_THRESHOLD = 0.20        # <20% WR in window → reduce to minimum
    PERF_HIGH_WR_THRESHOLD = 0.60       # >60% WR in window → boost allocation
    MAX_POSITIONS = 2                   # max 2 simultaneous — focus capital
    MAX_SPREAD_PCT = 0.15

    # ── Warmup — accept weaker signals for first 5 min after startup ────
    WARMUP_SECONDS = 5 * 60            # 5 min warmup: still requires 3/4 (no free passes)
    WARMUP_MIN_STRENGTH = 3            # during warmup, same 3/4 gate (was 2, let junk in)

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
        self.capital_pct: float = self.CAPITAL_PCT_FUTURES if is_futures else self.SPOT_CAPITAL_PCT
        self._exchange_id: str = "delta" if is_futures else "binance"
        self._market_analyzer = market_analyzer  # for 15m trend direction

        # Per-pair contract limits (data-driven from PAIR_MAX_CONTRACTS dict)
        base_asset = pair.split("/")[0] if "/" in pair else pair.replace("USD", "").replace(":USD", "")
        self._max_contracts = self.PAIR_MAX_CONTRACTS.get(base_asset, 1)
        self._base_asset = base_asset  # cached for SL/TP lookup

        # Dynamic ATR-based SL/TP — updated every tick from 1m candles
        # Spot uses wider SL/TP (no leverage, needs more room)
        if not is_futures:
            self._sl_pct: float = self.SPOT_SL_PCT
            self._tp_pct: float = self.SPOT_TP_PCT
        else:
            self._sl_pct = self.PAIR_SL_FLOOR.get(base_asset, self.STOP_LOSS_PCT)
            self._tp_pct = self.PAIR_TP_FLOOR.get(base_asset, self.MIN_TP_PCT)
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
        # Spot uses wider trail distance and activation (no leverage, needs room)
        if not is_futures:
            self.TRAILING_ACTIVATE_PCT = self.SPOT_TRAIL_ACTIVATE_PCT  # override for spot
            self.TRAILING_DISTANCE_PCT = self.SPOT_TRAIL_DISTANCE_PCT
        _init_trail_dist = self.TRAILING_DISTANCE_PCT
        self._trail_distance_pct: float = _init_trail_dist  # dynamic, only widens
        self._peak_unrealized_pnl: float = 0.0  # track peak P&L for decay exit
        self._profit_floor_pct: float = -999.0  # ratcheting capital floor (not yet locked)
        self._in_position_tick: int = 0  # counts 1s ticks while in position (for OHLCV refresh)

        # ── Dashboard-driven config (loaded from DB by main.py) ──────
        self._pair_enabled: bool = True       # can be disabled via pair_config
        self._allocation_pct: float = self.PAIR_ALLOC_PCT.get(base_asset, 15.0)
        self._setup_config: dict[str, bool] = {}  # {setup_type: enabled}

        # Previous RSI for reversal detection
        self._prev_rsi: float = 50.0

        # Rate limiting
        self._hourly_trades: list[float] = []
        self._daily_scalp_loss: float = 0.0

        # No more forced entries — we wait for quality setups
        self._last_position_exit: float = 0.0
        # Phantom cooldown: no new entries until this time (set by orphan reconciliation)
        self._phantom_cooldown_until: float = 0.0
        # Rate limit rejection logs
        self._last_reject_log: float = 0.0
        # Periodic SL check logging (every 10s while in position)
        self._last_ws_sl_log: float = 0.0

        # Stats for hourly summary
        self.hourly_wins: int = 0
        self.hourly_losses: int = 0
        self.hourly_pnl: float = 0.0
        self.hourly_skipped: int = 0  # track skipped low-quality signals

        # Tick tracking
        self._tick_count: int = 0
        self._last_heartbeat: float = 0.0
        self._strategy_start_time: float = 0.0  # set in on_start()

        # Shared signal state (read by options_scalp strategy)
        self.last_signal_state: dict[str, Any] | None = None
        # Directional signal breakdown (set by _evaluate_signals, spread into last_signal_state)
        self._last_signal_breakdown: dict[str, Any] = {}

        # BB Squeeze tracking (signal #8)
        self._squeeze_tick_count: int = 0

        # Load soul on init
        _load_soul()

    async def on_start(self) -> None:
        if not self.in_position:
            self.position_side = None
            self.entry_price = 0.0
            self.entry_amount = 0.0
        self._tick_count = 0
        self._strategy_start_time = time.monotonic()
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
        disabled_tag = f" DISABLED={','.join(self.DISABLED_PAIRS)}" if self.DISABLED_PAIRS else ""
        max_pos = self.SPOT_MAX_POSITIONS if not self.is_futures else self.MAX_POSITIONS
        self.logger.info(
            "[%s] PHASE-BASED v6.1 ACTIVE (%s) — tick=1s/5s(dynamic), "
            "SL=%.2f%% TP=%.2f%% Phase1=%ds(skip@+%.1f%%) Phase2=%ds MaxHold=%ds "
            "Trail@+%.1f%%(%.2f%%) MoveToEntry@+%.1f%% Flat=%ds "
            "MaxPos=%d MaxContracts=%d SLcool=%ds LossStreak=%d→%ds "
            "Mom=%.2f%% Vol=%.1fx RSI-Override=%d/%d "
            "DailyLoss=%.0f%%%s%s",
            self.pair, tag,
            self._sl_pct, self._tp_pct,
            self.PHASE1_SECONDS, self.PHASE1_SKIP_AT_PEAK_PCT,
            self.PHASE2_SECONDS, self.MAX_HOLD_SECONDS,
            self.TRAILING_ACTIVATE_PCT, self.TRAILING_DISTANCE_PCT,
            self.MOVE_SL_TO_ENTRY_PCT, self.FLATLINE_SECONDS,
            max_pos, self._max_contracts,
            self.SL_COOLDOWN_SECONDS, self.CONSECUTIVE_LOSS_LIMIT,
            self.STREAK_PAUSE_SECONDS,
            self.MOMENTUM_MIN_PCT, self.VOL_SPIKE_RATIO,
            self.RSI_OVERRIDE_LONG, self.RSI_OVERRIDE_SHORT,
            self.DAILY_LOSS_LIMIT_PCT, disabled_tag,
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

            # Per-pair floors — spot uses wider floors (no leverage)
            if not self.is_futures:
                sl_floor = self.SPOT_SL_PCT
                tp_floor = self.SPOT_TP_PCT
            else:
                sl_floor = self.PAIR_SL_FLOOR.get(self._base_asset, self.STOP_LOSS_PCT)
                tp_floor = self.PAIR_TP_FLOOR.get(self._base_asset, self.MIN_TP_PCT)

            # Dynamic: ATR-based, but never below floor
            self._sl_pct = max(sl_floor, atr_pct * self.ATR_SL_MULTIPLIER)
            self._tp_pct = max(tp_floor, atr_pct * self.ATR_TP_MULTIPLIER)

            # Safety cap: spot allows wider SL/TP (no leverage risk)
            sl_cap = 3.00 if not self.is_futures else 0.50
            tp_cap = 6.00 if not self.is_futures else 5.00
            self._sl_pct = min(self._sl_pct, sl_cap)
            self._tp_pct = min(self._tp_pct, tp_cap)
        except Exception:
            # Silently keep existing values if ATR calc fails
            pass

    async def check(self) -> list[Signal]:
        """One scalping tick — fetch candles, detect QUALITY momentum, manage exits."""
        signals: list[Signal] = []
        self._tick_count += 1
        exchange = self.trade_exchange or self.executor.exchange
        now = time.monotonic()

        # ── DISABLED PAIRS — skip entirely (SOL = 0% win rate) ─────────
        if self._base_asset in self.DISABLED_PAIRS:
            if not self.in_position:
                return signals  # don't enter
            # If somehow in position (restored), still check exits

        # ── Dashboard pair disable (pair_config.enabled = false) ──────
        if not self._pair_enabled:
            if not self.in_position:
                return signals  # pair disabled via dashboard
            # If in position, still allow exit checks

        # ── Daily expiry check (5:30 PM IST) — ONLY for dated contracts ──
        # Perpetual futures (BTC/USD:USD, ETH/USD:USD etc.) never expire.
        # Only dated contracts (symbol contains date like -260216-) have expiry.
        _expiry_no_new = False
        _expiry_force_close = False
        _mins_to_expiry = 999.0
        _is_perpetual = self.is_futures and "-" not in self.pair.split(":")[-1]
        if self.is_futures and not _is_perpetual:
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
        vwap = 0.0
        ema_9 = 0.0
        ema_21 = 0.0
        kc_upper = 0.0
        kc_lower = 0.0
        rsi_series: pd.Series | None = None
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

            # Keltner Channel (for BB Squeeze detection)
            kc = ta.volatility.KeltnerChannel(
                high=df["high"], low=df["low"], close=close,
                window=20, window_atr=10, multiplier=1.5,
                original_version=False,
            )
            kc_upper = float(kc.keltner_channel_hband().iloc[-1])
            kc_lower = float(kc.keltner_channel_lband().iloc[-1])

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

            # VWAP (Volume Weighted Average Price) — session VWAP over 30 candles
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
            cumulative_vol = df["volume"].cumsum()
            vwap_series = cumulative_tp_vol / cumulative_vol.replace(0, float("nan"))
            vwap = float(vwap_series.iloc[-1]) if not vwap_series.empty else current_price

            # 9 EMA and 21 EMA — momentum ribbon
            ema_9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
            ema_21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

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
        max_pos = self.SPOT_MAX_POSITIONS if not self.is_futures else self.MAX_POSITIONS
        if total_scalp >= max_pos:
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

        # ── PHANTOM COOLDOWN: no entries for 60s after phantom clear ──
        if now < self._phantom_cooldown_until:
            remaining = self._phantom_cooldown_until - now
            if self._tick_count % 12 == 0:
                self.logger.info(
                    "[%s] PHANTOM COOLDOWN — %.0fs remaining before new entries",
                    self.pair, remaining,
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

        # ── Quality momentum detection (3-of-4 with setup tracking) ──
        entry = self._detect_quality_entry(
            current_price, rsi_now, vol_ratio,
            momentum_60s, momentum_120s, momentum_300s,
            bb_upper, bb_lower,
            trend_15m,
            widened=is_widened,
            df=df,
            vwap=vwap,
            ema_9=ema_9,
            ema_21=ema_21,
            kc_upper=kc_upper,
            kc_lower=kc_lower,
            rsi_series=rsi_series,
        )

        if entry is not None:
            side, reason, use_limit, signal_strength = entry

            # ── PER-PAIR STRENGTH GATE: weak pairs need stronger signals ──
            # During warmup (first 5 min), accept 2/4 for all pairs incl BTC
            in_warmup = (time.monotonic() - self._strategy_start_time) < self.WARMUP_SECONDS
            if in_warmup:
                min_strength = self.WARMUP_MIN_STRENGTH
            else:
                min_strength = self.PAIR_MIN_STRENGTH.get(self._base_asset, 2)
            if signal_strength < min_strength:
                if self._tick_count % 12 == 0:
                    warmup_tag = " (WARMUP)" if in_warmup else ""
                    self.logger.info(
                        "[%s] STRENGTH GATE%s — %s needs %d/4+ but got %d/4, skipping",
                        self.pair, warmup_tag, self._base_asset, min_strength, signal_strength,
                    )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                    **self._last_signal_breakdown,
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
                    **self._last_signal_breakdown,
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
                **self._last_signal_breakdown,
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
            entry_signal = self._build_entry_signal(side, current_price, amount, reason, order_type)
            if entry_signal is not None:
                signals.append(entry_signal)
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
                **self._last_signal_breakdown,
            }
            # Log scanning status every 30 seconds with pass/fail per condition
            if self._tick_count % 6 == 0:
                eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(is_widened)

                # Build pass/fail indicators for each condition
                # RSI check (long direction for display; show both extremes)
                rsi_long_pass = rsi_now < eff_rsi_l
                rsi_short_pass = rsi_now > eff_rsi_s
                rsi_override = rsi_now < self.RSI_OVERRIDE_LONG or rsi_now > self.RSI_OVERRIDE_SHORT
                if rsi_long_pass or rsi_short_pass:
                    rsi_tag = f"RSI={rsi_now:.0f} \u2713"
                elif rsi_override:
                    rsi_tag = f"RSI={rsi_now:.0f} \u2713\u2713(OVERRIDE)"
                else:
                    rsi_tag = f"RSI={rsi_now:.0f} \u2717({eff_rsi_l:.0f}/{eff_rsi_s:.0f})"

                # Volume check
                vol_pass = vol_ratio >= eff_vol
                vol_tag = f"Vol={vol_ratio:.1f}x \u2713" if vol_pass else f"Vol={vol_ratio:.1f}x \u2717({eff_vol:.1f})"

                # Momentum check
                mom_pass = abs(momentum_60s) >= eff_mom
                mom_tag = f"Mom={momentum_60s:+.2f}% \u2713" if mom_pass else f"Mom={momentum_60s:+.2f}% \u2717({eff_mom:.2f})"

                # BB check
                bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1.0
                bb_pos = (current_price - bb_lower) / bb_range if bb_range > 0 else 0.5
                bb_pass = bb_pos <= self.BB_MEAN_REVERT_LOWER or bb_pos >= self.BB_MEAN_REVERT_UPPER
                bb_label = "low" if bb_pos < 0.3 else ("high" if bb_pos > 0.7 else "mid")
                bb_tag = f"BB={bb_label} \u2713" if bb_pass else f"BB={bb_label} \u2717"

                # Count passing signals
                pass_count = sum([rsi_long_pass or rsi_short_pass, vol_pass, mom_pass, bb_pass])
                # Determine outcome
                action = "ENTER" if pass_count >= 2 or rsi_override else "SKIP"

                self.logger.info(
                    "SCAN %s: %s | %s | %s | %s \u2192 %d/4 %s",
                    self.pair, rsi_tag, vol_tag, mom_tag, bb_tag,
                    pass_count, action,
                )

        # ── Write signal state to DB for dashboard Signal Monitor ─────────
        # Every scan tick (~5s), write current signal values
        if self._tick_count % 5 == 0:  # every 5 ticks = ~25s
            _bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1.0
            _bb_pos = (current_price - bb_lower) / _bb_range if _bb_range > 0 else 0.5
            try:
                await self._write_signal_state(
                    momentum_60s, vol_ratio, rsi_now, _bb_pos, momentum_300s,
                )
            except Exception:
                pass  # non-critical

        return signals

    # ======================================================================
    # SIGNAL ENTRY — 2-of-4 with 15m trend soft weight
    # ======================================================================

    def _effective_thresholds(self, widened: bool = False) -> tuple[float, float, float, float]:
        """Return (momentum, vol_ratio, rsi_long, rsi_short) with optional widening.

        When widened=True (idle 30+ min), thresholds loosen by 20%:
        - Momentum: 0.08% → 0.064%
        - Volume: 0.8x → 0.64x
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
        vwap: float = 0.0,
        ema_9: float = 0.0,
        ema_21: float = 0.0,
        kc_upper: float = 0.0,
        kc_lower: float = 0.0,
        rsi_series: "pd.Series | None" = None,
    ) -> tuple[str, str, bool, int] | None:
        """Detect quality momentum using 3-of-4 signals with setup tracking.

        Returns (side, reason, use_limit, signal_count) or None.

        GATE 0: Momentum must fire (abs(mom_60s) >= threshold).
        No momentum = no trade. Direction locked by momentum sign.

        RSI EXTREME OVERRIDE: RSI < 30 or > 70 enters immediately
        but still requires momentum in the matching direction.

        GATE: All pairs require 3/4+ signals (v6.1).

        Signals (up to 11, but counted against N/4 threshold):
        1. Momentum 60s: 0.08%+ move in 60s
        2. Volume: 0.8x+ spike
        3. RSI: < 35 oversold (long) or > 65 overbought (short)
        4. BB mean-reversion: price in bottom 15% of BB → long, top 15% → short
        5. Momentum 5m: 0.30%+ move over 5 candles (slow bleed detection)
        6. Trend continuation: new 15-candle low/high + volume > average
        7. VWAP Reclaim: price above VWAP + EMA 9 > 21 (long) or below + 9 < 21
        8. BB Squeeze Breakout: BB inside KC → squeeze, then price breaks out
        9. Liquidity Sweep: price sweeps swing H/L then reclaims + RSI divergence
        10. Fair Value Gap: price filling a 3-candle imbalance gap
        11. Volume Divergence: price vs volume trend divergence

        Signals 5-11 are BONUS — they count toward the total but the
        threshold is still expressed as N-of-4 scale (3/4).
        """
        can_short = self.is_futures and config.delta.enable_shorting

        # ── Get effective thresholds (may be widened, but SAME for both dirs) ─
        eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(widened)
        widen_tag = " WIDE" if widened else ""

        # ══════════════════════════════════════════════════════════════
        # GATE 0: MOMENTUM MUST FIRE — no momentum = no trade, period
        # ══════════════════════════════════════════════════════════════
        if abs(momentum_60s) < eff_mom:
            # Still build breakdown for dashboard (empty signals)
            self._last_signal_breakdown = {
                "bull_count": 0, "bear_count": 0,
                "bull_signals": [], "bear_signals": [],
                "bull_mom": False, "bull_vol": False, "bull_rsi": False, "bull_bb": False,
                "bear_mom": False, "bear_vol": False, "bear_rsi": False, "bear_bb": False,
            }
            return None

        # Direction is locked by momentum
        mom_direction = "long" if momentum_60s > 0 else "short"

        # ── SIGNAL GATE: always require 3/4 signals ─────────────────────
        # v6.1: removed 15m trend soft weight that was letting 2/4 entries
        # through for "trend-aligned" trades. 2/4 entries are coin flips at
        # 20x leverage and were the root cause of the 26.6% WR collapse.
        required_long = 3
        required_short = 3

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

        # 7. VWAP Reclaim + EMA ribbon alignment
        #    Price above VWAP with bullish EMA ribbon → momentum confirmation for LONG
        #    Price below VWAP with bearish EMA ribbon → momentum confirmation for SHORT
        if vwap > 0 and ema_9 > 0 and ema_21 > 0:
            if price > vwap and ema_9 > ema_21:
                vwap_dist = (price - vwap) / vwap * 100
                bull_signals.append(f"VWAP:above+EMA↑({vwap_dist:.2f}%)")
            if price < vwap and ema_9 < ema_21 and can_short:
                vwap_dist = (vwap - price) / vwap * 100
                bear_signals.append(f"VWAP:below+EMA↓({vwap_dist:.2f}%)")

        # 8. Bollinger Squeeze Breakout
        #    BB inside Keltner Channel = squeeze (low volatility, coiling).
        #    Price closing outside BB with volume spike = explosive breakout.
        if kc_upper > 0 and kc_lower > 0:
            bb_inside_kc = bb_upper < kc_upper and bb_lower > kc_lower
            if bb_inside_kc:
                self._squeeze_tick_count = 2  # breakout valid for 2 ticks after squeeze
            elif self._squeeze_tick_count > 0:
                self._squeeze_tick_count -= 1

            if bb_inside_kc or self._squeeze_tick_count > 0:
                if price > bb_upper and vol_ratio >= eff_vol:
                    bull_signals.append(f"BBSQZ:breakout+vol{vol_ratio:.1f}x")
                if price < bb_lower and vol_ratio >= eff_vol and can_short:
                    bear_signals.append(f"BBSQZ:breakout+vol{vol_ratio:.1f}x")

        # 9. Liquidity Sweep — Swing Failure Pattern + RSI divergence
        #    Price sweeps past a swing high/low (triggering stops) then reclaims
        #    the level with RSI divergence = fakeout reversal.
        if df is not None and len(df) >= 12 and rsi_series is not None and len(rsi_series) >= 12:
            lows_arr = df["low"].values
            highs_arr = df["high"].values
            rsi_arr = rsi_series.values

            # Swing low/high from candles [-12:-2] (exclude last 2 for reclaim)
            lookback_lows = lows_arr[-12:-2]
            lookback_highs = highs_arr[-12:-2]
            swing_low = float(lookback_lows.min())
            swing_high = float(lookback_highs.max())
            swing_low_idx = int(lookback_lows.argmin())
            swing_high_idx = int(lookback_highs.argmax())

            # Bullish sweep: wicked below swing low then closed above it
            recent_low = float(min(lows_arr[-2], lows_arr[-1]))
            if recent_low < swing_low and price > swing_low:
                rsi_at_swing = float(rsi_arr[-12 + swing_low_idx])
                if rsi_now > rsi_at_swing:  # RSI higher low = bullish divergence
                    bull_signals.append(f"LIQSWEEP:swept{swing_low:.0f}+RSIdiv")

            # Bearish sweep: wicked above swing high then closed below it
            recent_high = float(max(highs_arr[-2], highs_arr[-1]))
            if recent_high > swing_high and price < swing_high and can_short:
                rsi_at_swing = float(rsi_arr[-12 + swing_high_idx])
                if rsi_now < rsi_at_swing:  # RSI lower high = bearish divergence
                    bear_signals.append(f"LIQSWEEP:swept{swing_high:.0f}+RSIdiv")

        # 10. Fair Value Gap (FVG) — price filling an imbalance gap
        #     3-candle pattern: gap between candle A's high and candle C's low.
        #     Price retracing into the gap = high-precision entry.
        if df is not None and len(df) >= 5:
            for i in range(-5, -2):
                candle_a_high = float(df["high"].iloc[i])
                candle_c_low = float(df["low"].iloc[i + 2])
                candle_a_low = float(df["low"].iloc[i])
                candle_c_high = float(df["high"].iloc[i + 2])

                # Bullish FVG: candle C's low > candle A's high (gap up)
                if candle_c_low > candle_a_high:
                    gap_top = candle_c_low
                    gap_bottom = candle_a_high
                    gap_pct = (gap_top - gap_bottom) / gap_bottom * 100 if gap_bottom > 0 else 0
                    if gap_bottom <= price <= gap_top and gap_pct >= 0.05:
                        bull_signals.append(f"FVG:fill+{gap_pct:.2f}%")
                        break

                # Bearish FVG: candle C's high < candle A's low (gap down)
                if candle_c_high < candle_a_low and can_short:
                    gap_top = candle_a_low
                    gap_bottom = candle_c_high
                    gap_pct = (gap_top - gap_bottom) / gap_top * 100 if gap_top > 0 else 0
                    if gap_bottom <= price <= gap_top and gap_pct >= 0.05:
                        bear_signals.append(f"FVG:fill-{gap_pct:.2f}%")
                        break

        # 11. Volume Divergence — hollow moves detection
        #     Rising price + declining volume = no new money behind the move.
        if df is not None and len(df) >= 10:
            recent_closes = df["close"].values[-5:]
            older_closes = df["close"].values[-10:-5]
            recent_vol = float(df["volume"].values[-5:].mean())
            older_vol = float(df["volume"].values[-10:-5].mean())

            price_rising = float(recent_closes[-1]) > float(older_closes[-1])
            price_falling = float(recent_closes[-1]) < float(older_closes[-1])
            vol_declining = older_vol > 0 and recent_vol < older_vol * 0.8  # 20%+ drop

            # Bearish: price up but volume dying = hollow pump
            if price_rising and vol_declining and can_short:
                vol_drop = (1 - recent_vol / older_vol) * 100 if older_vol > 0 else 0
                bear_signals.append(f"VOLDIV:price↑vol↓{vol_drop:.0f}%")

            # Bullish: price down but volume dying = exhausted sellers
            if price_falling and vol_declining:
                vol_drop = (1 - recent_vol / older_vol) * 100 if older_vol > 0 else 0
                bull_signals.append(f"VOLDIV:price↓vol↓{vol_drop:.0f}%")

        # ── Build directional signal breakdown for dashboard ────────────────
        # Stored on self so last_signal_state can spread it in evaluate().
        def _build_breakdown() -> dict[str, Any]:
            return {
                "bull_count": len(bull_signals),
                "bear_count": len(bear_signals),
                "bull_signals": list(bull_signals),
                "bear_signals": list(bear_signals),
                # Core-4 directional booleans (dashboard dots)
                "bull_mom": any(s.startswith(("MOM:", "MOM5m:")) for s in bull_signals),
                "bull_vol": any(s.startswith("VOL:") for s in bull_signals),
                "bull_rsi": any(s.startswith("RSI:") for s in bull_signals),
                "bull_bb": any(s.startswith(("BB:", "BBSQZ:")) for s in bull_signals),
                "bear_mom": any(s.startswith(("MOM:", "MOM5m:")) for s in bear_signals),
                "bear_vol": any(s.startswith("VOL:") for s in bear_signals),
                "bear_rsi": any(s.startswith("RSI:") for s in bear_signals),
                "bear_bb": any(s.startswith(("BB:", "BBSQZ:")) for s in bear_signals),
            }

        # ── RSI EXTREME OVERRIDE: RSI <30 or >70 → enter immediately ─────
        # Strong oversold/overbought overrides all other conditions.
        # Still requires momentum in the matching direction (Gate 0 passed).
        if rsi_now < self.RSI_OVERRIDE_LONG and mom_direction == "long":
            reason = f"LONG RSI-OVERRIDE: RSI={rsi_now:.1f}<{self.RSI_OVERRIDE_LONG} [15m={trend_15m}]{widen_tag}"
            # Add any existing bull signals for context
            if bull_signals:
                reason += f" +{'+'.join(bull_signals)}"
            strength = max(len(bull_signals), 2)  # at least 2/4 equivalent
            self._last_signal_breakdown = _build_breakdown()
            return ("long", reason, True, strength)

        if rsi_now > self.RSI_OVERRIDE_SHORT and can_short and mom_direction == "short":
            reason = f"SHORT RSI-OVERRIDE: RSI={rsi_now:.1f}>{self.RSI_OVERRIDE_SHORT} [15m={trend_15m}]{widen_tag}"
            if bear_signals:
                reason += f" +{'+'.join(bear_signals)}"
            strength = max(len(bear_signals), 2)
            self._last_signal_breakdown = _build_breakdown()
            return ("short", reason, True, strength)

        # ── Check required signals (LONG) — trend-weighted ────────────────
        # NO fee filter — if 2/4 signals fire, ENTER. The signal system IS the filter.
        # RSI + VOL is a valid entry even when momentum is flat (price about to move).
        if len(bull_signals) >= required_long and mom_direction == "long":
            req_tag = f" req={required_long}/4" if required_long > 2 else ""
            reason = f"LONG {len(bull_signals)}/4: {' + '.join(bull_signals)} [15m={trend_15m}]{req_tag}{widen_tag}"
            use_limit = "MOM" not in bull_signals[0]
            self._last_signal_breakdown = _build_breakdown()
            return ("long", reason, use_limit, len(bull_signals))

        # ── Check required signals (SHORT) — trend-weighted ───────────────
        if len(bear_signals) >= required_short and can_short and mom_direction == "short":
            req_tag = f" req={required_short}/4" if required_short > 2 else ""
            reason = f"SHORT {len(bear_signals)}/4: {' + '.join(bear_signals)} [15m={trend_15m}]{req_tag}{widen_tag}"
            use_limit = "MOM" not in bear_signals[0]
            self._last_signal_breakdown = _build_breakdown()
            return ("short", reason, use_limit, len(bear_signals))

        self._last_signal_breakdown = _build_breakdown()
        return None

    # ======================================================================
    # EXIT LOGIC — RIDE WINNERS, CUT LOSERS
    # ======================================================================

    def _update_trail_distance(self, pnl_pct: float | None = None) -> float:
        """Update dynamic trail distance based on PEAK profit level.

        Uses _peak_unrealized_pnl (highest PnL ever seen) for tier selection,
        NOT current pnl_pct. This ensures tiers widen based on the best the
        trade has been, not where it is right now.

        Trail distance ONLY increases (never tightens once widened).
        Returns the current trail distance percentage.
        """
        # Use peak PnL for tier selection — captures spikes between ticks
        peak = self._peak_unrealized_pnl
        # Walk through tiers from highest to lowest
        for min_profit, distance in reversed(self.TRAIL_TIERS):
            if peak >= min_profit and distance > self._trail_distance_pct:
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
        """3-PHASE EXIT SYSTEM — aggressive profit taking.

        ALWAYS: Hard SL, ratcheting profit floors, Hard TP (if not trailing).
        PHASE 1 (0-30s): Only hard exits. Let trade settle.
          Exception: if peak >= +0.5%, skip to Phase 2 immediately.
        PHASE 2 (30s-10 min): Breakeven, trail, decay emergency, reversals.
        PHASE 3 (10-30 min): Trail or cut. Flat/timeout ONLY close losers.
        """
        signals: list[Signal] = []
        hold_seconds = time.monotonic() - self.entry_time
        pnl_pct = self._calc_pnl_pct(current_price)
        side = self.position_side or "long"

        # Track peaks
        self._peak_unrealized_pnl = max(self._peak_unrealized_pnl, pnl_pct)
        if side == "long":
            self.highest_since_entry = max(self.highest_since_entry, current_price)
        else:
            self.lowest_since_entry = min(self.lowest_since_entry, current_price)

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Hard SL — fires in ALL phases
        # ══════════════════════════════════════════════════════════════
        if side == "long":
            sl_price = self.entry_price * (1 - self._sl_pct / 100)
            if current_price <= sl_price:
                self.logger.info(
                    "[%s] SL HIT %.2f%% (SL=%.2f%%) — %ds in",
                    self.pair, pnl_pct, self._sl_pct, int(hold_seconds),
                )
                return self._do_exit(current_price, pnl_pct, side, "SL", hold_seconds)
        else:
            sl_price = self.entry_price * (1 + self._sl_pct / 100)
            if current_price >= sl_price:
                self.logger.info(
                    "[%s] SL HIT %.2f%% (SL=%.2f%%) — %ds in",
                    self.pair, pnl_pct, self._sl_pct, int(hold_seconds),
                )
                return self._do_exit(current_price, pnl_pct, side, "SL", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Hard TP — 10% capital safety net (only if NOT trailing)
        # If trailing is active, ratchets + trail protect the profit.
        # ══════════════════════════════════════════════════════════════
        capital_pnl = pnl_pct * self.leverage
        if capital_pnl >= self.HARD_TP_CAPITAL_PCT and not self._trailing_active:
            self.logger.info(
                "[%s] HARD TP HIT — capital +%.1f%% (price +%.2f%% × %dx) — %ds in",
                self.pair, capital_pnl, pnl_pct, self.leverage, int(hold_seconds),
            )
            return self._do_exit(current_price, pnl_pct, side, "HARD_TP_10PCT", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Ratcheting profit floors — lock in gains, never give back
        # Checked on EVERY tick, ALL phases. Cannot go back down.
        # ══════════════════════════════════════════════════════════════
        for threshold, floor in self.PROFIT_RATCHETS:
            if capital_pnl >= threshold and floor > self._profit_floor_pct:
                old_floor = self._profit_floor_pct
                self._profit_floor_pct = floor
                self.logger.info(
                    "[%s] RATCHET LOCK — capital +%.1f%% >= +%.0f%%, "
                    "floor raised %.1f%% → +%.1f%%",
                    self.pair, capital_pnl, threshold,
                    old_floor if old_floor > -999 else 0, floor,
                )
        if self._profit_floor_pct > -999 and capital_pnl < self._profit_floor_pct:
            self.logger.info(
                "[%s] PROFIT FLOOR BREACH — capital +%.1f%% < floor +%.1f%% — EXIT",
                self.pair, capital_pnl, self._profit_floor_pct,
            )
            return self._do_exit(current_price, pnl_pct, side, "PROFIT_LOCK", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # PHASE 1 (0-30s): HANDS OFF — only SL and Hard TP above
        # EXCEPTION: if peak PnL >= +1.0%, skip to Phase 2 immediately
        #   (real profit appeared — don't let it evaporate)
        # ══════════════════════════════════════════════════════════════
        if hold_seconds < self.PHASE1_SECONDS:
            # Peak-aware skip: if we've seen real profit, graduate early
            if self._peak_unrealized_pnl >= self.PHASE1_SKIP_AT_PEAK_PCT:
                self.logger.info(
                    "[%s] PHASE1 SKIP — peak +%.2f%% >= +%.1f%% threshold, "
                    "entering Phase 2 at %ds (current PnL=%+.2f%%)",
                    self.pair, self._peak_unrealized_pnl,
                    self.PHASE1_SKIP_AT_PEAK_PCT,
                    int(hold_seconds), pnl_pct,
                )
                # Fall through to Phase 2 below
            else:
                if self._tick_count % 30 == 0:
                    self.logger.info(
                        "[%s] PHASE1 %ds/%ds | %s $%.2f | PnL=%+.2f%% | peak=%+.2f%%",
                        self.pair, int(hold_seconds), self.PHASE1_SECONDS,
                        side, current_price, pnl_pct, self._peak_unrealized_pnl,
                    )
                return signals

        # ══════════════════════════════════════════════════════════════
        # PHASE 2 (3-10 min): WATCH — move SL to entry, trail, reversals
        # ══════════════════════════════════════════════════════════════
        if hold_seconds < self.PHASE2_SECONDS:
            # SL moved to entry at +0.3% (only exit here if price returns to entry)
            if self._peak_unrealized_pnl >= self.MOVE_SL_TO_ENTRY_PCT and not self._trailing_active:
                at_entry = (
                    (side == "long" and current_price <= self.entry_price) or
                    (side == "short" and current_price >= self.entry_price)
                )
                if at_entry:
                    self.logger.info(
                        "[%s] BREAKEVEN — peaked +%.2f%%, back to entry | %ds in",
                        self.pair, self._peak_unrealized_pnl, int(hold_seconds),
                    )
                    return self._do_exit(current_price, pnl_pct, side, "BREAKEVEN", hold_seconds)

            # Trail at +0.5% — use PEAK PnL (not current) for activation
            # This catches spikes: if price hit +3% between ticks, peak is +3%
            if self._peak_unrealized_pnl >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                self._update_trail_distance()
                self.logger.info(
                    "[%s] TRAIL ON — peak +%.2f%%, current +%.2f%%, dist %.2f%% | %ds in",
                    self.pair, self._peak_unrealized_pnl, pnl_pct,
                    self._trail_distance_pct, int(hold_seconds),
                )

            if self._trailing_active:
                self._update_trail_distance()
                if side == "long":
                    trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                    if current_price <= trail_stop:
                        return self._do_exit(current_price, pnl_pct, side, "TRAIL", hold_seconds)
                else:
                    trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                    if current_price >= trail_stop:
                        return self._do_exit(current_price, pnl_pct, side, "TRAIL", hold_seconds)

            # Profit decay emergency — lost 60%+ of peak profit
            peak_capital = self._peak_unrealized_pnl * self.leverage
            current_capital = pnl_pct * self.leverage
            if (peak_capital >= self.PROFIT_DECAY_EMERGENCY_PEAK_CAP
                    and current_capital < peak_capital * self.PROFIT_DECAY_EMERGENCY_RATIO):
                return self._do_exit(current_price, pnl_pct, side, "DECAY_EMERGENCY", hold_seconds)

            # ── SPOT PROFIT PROTECTION (Phase 2, spot only) ────────────
            if not self.is_futures and pnl_pct > 0:
                peak = self._peak_unrealized_pnl
                # Spot pullback: lost 50% of peak
                if peak >= self.SPOT_PULLBACK_MIN_PEAK_PCT:
                    if pnl_pct < peak * self.SPOT_PULLBACK_RATIO:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_PULLBACK", hold_seconds)
                # Spot decay: profit fading toward breakeven
                if peak >= self.SPOT_DECAY_MIN_PEAK_PCT:
                    if pnl_pct < self.SPOT_DECAY_EXIT_BELOW_PCT:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_DECAY", hold_seconds)
                # Spot breakeven: don't let green go red
                if peak >= self.SPOT_BREAKEVEN_MIN_PEAK_PCT:
                    if pnl_pct <= self.SPOT_BREAKEVEN_EXIT_BELOW_PCT:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_BREAKEVEN", hold_seconds)

            # Signal reversal (only in profit)
            if pnl_pct >= self.REVERSAL_MIN_PROFIT_PCT:
                if side == "long":
                    rev = (rsi_now > self.RSI_REVERSAL_LONG and self._prev_rsi <= self.RSI_REVERSAL_LONG) or \
                          (momentum_60s < self.MOMENTUM_REVERSAL_PCT)
                else:
                    rev = (rsi_now < self.RSI_REVERSAL_SHORT and self._prev_rsi >= self.RSI_REVERSAL_SHORT) or \
                          (momentum_60s > abs(self.MOMENTUM_REVERSAL_PCT))
                if rev:
                    return self._do_exit(current_price, pnl_pct, side, "REVERSAL", hold_seconds)

            return signals

        # ══════════════════════════════════════════════════════════════
        # PHASE 3 (10-30 min): TRAIL OR CUT
        # ══════════════════════════════════════════════════════════════

        # Trailing — use PEAK PnL for activation (catches inter-tick spikes)
        if self._peak_unrealized_pnl >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
            self._trailing_active = True
            self._update_trail_distance()

        if self._trailing_active:
            self._update_trail_distance()
            if side == "long":
                trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                if current_price <= trail_stop:
                    return self._do_exit(current_price, pnl_pct, side, "TRAIL", hold_seconds)
            else:
                trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                if current_price >= trail_stop:
                    return self._do_exit(current_price, pnl_pct, side, "TRAIL", hold_seconds)

        # Profit decay emergency — lost 60%+ of peak profit
        peak_capital = self._peak_unrealized_pnl * self.leverage
        current_capital = pnl_pct * self.leverage
        if (peak_capital >= self.PROFIT_DECAY_EMERGENCY_PEAK_CAP
                and current_capital < peak_capital * self.PROFIT_DECAY_EMERGENCY_RATIO):
            return self._do_exit(current_price, pnl_pct, side, "DECAY_EMERGENCY", hold_seconds)

        # ── SPOT PROFIT PROTECTION (Phase 3, spot only) ────────────
        if not self.is_futures and pnl_pct > 0:
            peak = self._peak_unrealized_pnl
            # Spot pullback: lost 50% of peak
            if peak >= self.SPOT_PULLBACK_MIN_PEAK_PCT:
                if pnl_pct < peak * self.SPOT_PULLBACK_RATIO:
                    return self._do_exit(current_price, pnl_pct, side, "SPOT_PULLBACK", hold_seconds)
            # Spot decay: profit fading toward breakeven
            if peak >= self.SPOT_DECAY_MIN_PEAK_PCT:
                if pnl_pct < self.SPOT_DECAY_EXIT_BELOW_PCT:
                    return self._do_exit(current_price, pnl_pct, side, "SPOT_DECAY", hold_seconds)
            # Spot breakeven: don't let green go red
            if peak >= self.SPOT_BREAKEVEN_MIN_PEAK_PCT:
                if pnl_pct <= self.SPOT_BREAKEVEN_EXIT_BELOW_PCT:
                    return self._do_exit(current_price, pnl_pct, side, "SPOT_BREAKEVEN", hold_seconds)

        # Flatline — 10 min with no movement — ONLY close losers
        if (hold_seconds >= self.FLATLINE_SECONDS
                and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT and pnl_pct <= 0):
            return self._do_exit(current_price, pnl_pct, side, "FLAT", hold_seconds)

        # Hard timeout — ONLY close losers (winners protected by trail/ratchet)
        if hold_seconds >= self.MAX_HOLD_SECONDS and not self._trailing_active and pnl_pct <= 0:
            return self._do_exit(current_price, pnl_pct, side, "TIMEOUT", hold_seconds)

        # Safety: past timeout AND losing
        if hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
            return self._do_exit(current_price, pnl_pct, side, "SAFETY", hold_seconds)

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

        # ── ALWAYS: Hard SL ──────────────────────────────────────────
        if side == "long":
            sl_price = self.entry_price * (1 - self._sl_pct / 100)
            if current_price <= sl_price:
                exit_type = "SL"
        elif side == "short":
            sl_price = self.entry_price * (1 + self._sl_pct / 100)
            if current_price >= sl_price:
                exit_type = "SL"

        # ── ALWAYS: Ratcheting profit floors ─────────────────────────
        capital_pnl = pnl_pct * self.leverage
        if not exit_type:
            for threshold, floor in self.PROFIT_RATCHETS:
                if capital_pnl >= threshold and floor > self._profit_floor_pct:
                    old_floor = self._profit_floor_pct
                    self._profit_floor_pct = floor
                    self.logger.info(
                        "[%s] WS RATCHET LOCK — capital +%.1f%% >= +%.0f%%, "
                        "floor %.1f%% → +%.1f%%",
                        self.pair, capital_pnl, threshold,
                        old_floor if old_floor > -999 else 0, floor,
                    )
            if self._profit_floor_pct > -999 and capital_pnl < self._profit_floor_pct:
                exit_type = "PROFIT_LOCK"

        # ── ALWAYS: Hard TP (only if NOT trailing) ───────────────────
        if not exit_type and capital_pnl >= self.HARD_TP_CAPITAL_PCT and not self._trailing_active:
            exit_type = "HARD_TP_10PCT"

        # Periodic logging (every 10s) for visibility
        now_mono = time.monotonic()
        if now_mono - self._last_ws_sl_log >= 10:
            self._last_ws_sl_log = now_mono
            trail_info = ""
            if self._trailing_active:
                if side == "long":
                    t_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                else:
                    t_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                trail_info = f" Trail={self._trail_distance_pct:.2f}%@${t_stop:.2f}"
            floor_info = f" Floor=+{self._profit_floor_pct:.0f}%cap" if self._profit_floor_pct > -999 else ""
            self.logger.info(
                "[%s] WS TICK: %s @ $%.2f PnL=%+.2f%% (%+.1f%%cap) peak=%+.2f%% "
                "SL=$%.2f(%.2f%%) hold=%ds%s%s%s",
                self.pair, side, current_price, pnl_pct, capital_pnl,
                self._peak_unrealized_pnl, sl_price, self._sl_pct,
                int(hold_seconds),
                trail_info, floor_info,
                " → " + exit_type + "!" if exit_type else "",
            )

        # ── PHASE 1: only hard exits fire, skip everything else ──────
        _in_phase2_plus = hold_seconds >= self.PHASE1_SECONDS
        if not exit_type and not _in_phase2_plus:
            if self._peak_unrealized_pnl >= self.PHASE1_SKIP_AT_PEAK_PCT:
                _in_phase2_plus = True  # graduate early — real profit to protect
            else:
                return  # hands off — no WS exits except SL/ratchet/hard_tp

        # ── PHASE 2+: breakeven (SL moved to entry after peak > +0.20%) ──
        if not exit_type and _in_phase2_plus:
            if self._peak_unrealized_pnl >= self.MOVE_SL_TO_ENTRY_PCT and not self._trailing_active:
                at_entry = (
                    (side == "long" and current_price <= self.entry_price) or
                    (side == "short" and current_price >= self.entry_price)
                )
                if at_entry:
                    exit_type = "BREAKEVEN"

        # ── PHASE 2+: trailing (activate from PEAK, not current) ─────
        if not exit_type and _in_phase2_plus:
            if self._peak_unrealized_pnl >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
                self._trailing_active = True
                self._update_trail_distance()

            if self._trailing_active:
                self._update_trail_distance()
                if side == "long":
                    trail_stop = self.highest_since_entry * (1 - self._trail_distance_pct / 100)
                    if current_price <= trail_stop:
                        exit_type = "TRAIL"
                else:
                    trail_stop = self.lowest_since_entry * (1 + self._trail_distance_pct / 100)
                    if current_price >= trail_stop:
                        exit_type = "TRAIL"

        # ── PHASE 2+: profit decay emergency (lost 60%+ of peak) ─────
        if not exit_type and _in_phase2_plus:
            peak_capital = self._peak_unrealized_pnl * self.leverage
            if (peak_capital >= self.PROFIT_DECAY_EMERGENCY_PEAK_CAP
                    and capital_pnl < peak_capital * self.PROFIT_DECAY_EMERGENCY_RATIO):
                exit_type = "DECAY_EMERGENCY"

        # ── PHASE 2+: SPOT PROFIT PROTECTION (spot only) ────────────
        if not exit_type and _in_phase2_plus and not self.is_futures and pnl_pct > 0:
            peak = self._peak_unrealized_pnl
            # Spot pullback: lost 50% of peak
            if peak >= self.SPOT_PULLBACK_MIN_PEAK_PCT:
                if pnl_pct < peak * self.SPOT_PULLBACK_RATIO:
                    exit_type = "SPOT_PULLBACK"
            # Spot decay: profit fading toward breakeven
            if not exit_type and peak >= self.SPOT_DECAY_MIN_PEAK_PCT:
                if pnl_pct < self.SPOT_DECAY_EXIT_BELOW_PCT:
                    exit_type = "SPOT_DECAY"
            # Spot breakeven: don't let green go red
            if not exit_type and peak >= self.SPOT_BREAKEVEN_MIN_PEAK_PCT:
                if pnl_pct <= self.SPOT_BREAKEVEN_EXIT_BELOW_PCT:
                    exit_type = "SPOT_BREAKEVEN"

        # ── PHASE 3: flatline + timeout — ONLY close losers ──────────
        if not exit_type and hold_seconds >= self.FLATLINE_SECONDS:
            if abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT and pnl_pct <= 0:
                exit_type = "FLAT"
        if not exit_type and hold_seconds >= self.MAX_HOLD_SECONDS:
            if not self._trailing_active and pnl_pct <= 0:
                exit_type = "TIMEOUT"
        if not exit_type and hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
            exit_type = "SAFETY"

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
        """Execute an exit: build signal, record result, log.

        Fee-aware: skips tiny exits (gross < $0.10) unless it's an SL,
        ratchet floor, hard TP, or safety exit.
        """
        # Fee-aware minimum — skip tiny exits that'd be eaten by fees
        _FORCE_EXIT_TYPES = {"SL", "PROFIT_LOCK", "HARD_TP_10PCT", "SAFETY",
                             "WS-SL", "WS-PROFIT_LOCK", "WS-HARD_TP_10PCT", "WS-SAFETY"}
        clean_type = exit_type.replace("WS-", "")
        if clean_type not in {"SL", "PROFIT_LOCK", "HARD_TP_10PCT", "SAFETY"} and self.entry_price > 0:
            if self.is_futures:
                from alpha.trade_executor import DELTA_CONTRACT_SIZE
                contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0.01)
                coin_amount = self.entry_amount * contract_size
            else:
                coin_amount = self.entry_amount
            gross_usd = abs(price - self.entry_price) * coin_amount
            if gross_usd < self.FEE_MINIMUM_GROSS_USD:
                self.logger.info(
                    "[%s] Skip %s — gross $%.4f < $%.2f minimum",
                    self.pair, exit_type, gross_usd, self.FEE_MINIMUM_GROSS_USD,
                )
                return []  # empty = no exit

        cap_pct = pnl_pct * self.leverage
        reason = (
            f"Scalp {exit_type} {pnl_pct:+.2f}% price "
            f"({cap_pct:+.1f}% capital at {self.leverage}x)"
        )
        # Compute peak P&L BEFORE _record_scalp_result resets entry_price/highest/lowest
        if side == "long" and self.entry_price > 0:
            peak_pnl = ((self.highest_since_entry - self.entry_price) / self.entry_price) * 100
        elif side == "short" and self.entry_price > 0:
            peak_pnl = ((self.entry_price - self.lowest_since_entry) / self.entry_price) * 100
        else:
            peak_pnl = 0.0
        self._record_scalp_result(pnl_pct, exit_type.lower())
        return [self._exit_signal(price, side, reason, peak_pnl)]

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
        # Use dashboard-driven allocation if set, else fall back to class constant
        base_alloc = self._allocation_pct
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
    # SETUP CLASSIFICATION
    # ======================================================================

    @staticmethod
    def _classify_setup(reason: str) -> str:
        """Classify entry into a setup type based on which signals fired.

        Reads the signal tags from the reason string to determine the dominant
        setup pattern. Returns a short uppercase setup name for DB storage.
        """
        r = reason.upper()

        # RSI override is its own setup
        if "RSI-OVERRIDE" in r:
            return "RSI_OVERRIDE"

        # Named setups — in priority order (most specific first)
        if "BBSQZ:" in r:
            return "BB_SQUEEZE"
        if "LIQSWEEP:" in r:
            return "LIQ_SWEEP"
        if "FVG:" in r:
            return "FVG_FILL"
        if "VOLDIV:" in r:
            return "VOL_DIVERGENCE"
        if "VWAP:" in r:
            return "VWAP_RECLAIM"
        if "TCONT:" in r:
            return "TREND_CONT"

        # Combination setups
        has_mom = "MOM:" in r or "MOM5M:" in r
        has_vol = "VOL:" in r
        has_rsi = "RSI:" in r
        has_bb = "BB:" in r

        if has_mom and has_vol:
            return "MOMENTUM_BURST"
        if has_bb and has_rsi:
            return "MEAN_REVERT"

        # Count total signal tags to determine if multi-signal
        signal_count = sum([
            has_mom, has_vol, has_rsi, has_bb,
            "VWAP:" in r, "TCONT:" in r, "BBSQZ:" in r,
            "LIQSWEEP:" in r, "FVG:" in r, "VOLDIV:" in r,
            "MOM5M:" in r.replace("MOM:", ""),
        ])
        if signal_count >= 4:
            return "MULTI_SIGNAL"

        return "MIXED"

    # ======================================================================
    # SIGNAL STATE WRITE (for dashboard Signal Monitor)
    # ======================================================================

    async def _write_signal_state(
        self,
        momentum_60s: float,
        vol_ratio: float,
        rsi_now: float,
        bb_position: float,
        momentum_300s: float,
    ) -> None:
        """Write current signal values to signal_state table for dashboard monitor.

        Called once per scan cycle (every 5s) from check().
        Uses _last_signal_breakdown for firing state of each signal.
        """
        db = self.executor.db
        if not db or not db.is_connected:
            return

        breakdown = self._last_signal_breakdown
        if not breakdown:
            return

        eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds()

        # Determine dominant direction from breakdown
        bull_count = breakdown.get("bull_count", 0)
        bear_count = breakdown.get("bear_count", 0)
        mom_dir = "bull" if momentum_60s > 0 else ("bear" if momentum_60s < 0 else "neutral")

        signals = [
            # Core 4
            {
                "signal_id": "MOM_60S",
                "value": momentum_60s,
                "threshold": eff_mom,
                "firing": abs(momentum_60s) >= eff_mom,
                "direction": mom_dir,
            },
            {
                "signal_id": "VOL",
                "value": vol_ratio,
                "threshold": eff_vol,
                "firing": vol_ratio >= eff_vol,
                "direction": mom_dir if vol_ratio >= eff_vol else "neutral",
            },
            {
                "signal_id": "RSI",
                "value": rsi_now,
                "threshold": eff_rsi_l,  # show the long threshold
                "firing": rsi_now < eff_rsi_l or rsi_now > eff_rsi_s,
                "direction": "bull" if rsi_now < eff_rsi_l else ("bear" if rsi_now > eff_rsi_s else "neutral"),
            },
            {
                "signal_id": "BB",
                "value": bb_position,
                "threshold": self.BB_MEAN_REVERT_LOWER,
                "firing": bb_position <= self.BB_MEAN_REVERT_LOWER or bb_position >= self.BB_MEAN_REVERT_UPPER,
                "direction": "bull" if bb_position <= self.BB_MEAN_REVERT_LOWER else (
                    "bear" if bb_position >= self.BB_MEAN_REVERT_UPPER else "neutral"
                ),
            },
            # Bonus 7
            {
                "signal_id": "MOM_5M",
                "value": momentum_300s,
                "threshold": self.MOMENTUM_5M_MIN_PCT,
                "firing": abs(momentum_300s) >= self.MOMENTUM_5M_MIN_PCT,
                "direction": "bull" if momentum_300s > 0 else ("bear" if momentum_300s < 0 else "neutral"),
            },
            {
                "signal_id": "TCONT",
                "value": None,
                "threshold": None,
                "firing": breakdown.get("bull_mom", False) or breakdown.get("bear_mom", False),
                "direction": "bull" if any("TCONT:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("TCONT:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
            {
                "signal_id": "VWAP",
                "value": None,
                "threshold": None,
                "firing": any("VWAP:" in s for s in breakdown.get("bull_signals", []) + breakdown.get("bear_signals", [])),
                "direction": "bull" if any("VWAP:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("VWAP:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
            {
                "signal_id": "BBSQZ",
                "value": None,
                "threshold": None,
                "firing": any("BBSQZ:" in s for s in breakdown.get("bull_signals", []) + breakdown.get("bear_signals", [])),
                "direction": "bull" if any("BBSQZ:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("BBSQZ:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
            {
                "signal_id": "LIQSWEEP",
                "value": None,
                "threshold": None,
                "firing": any("LIQSWEEP:" in s for s in breakdown.get("bull_signals", []) + breakdown.get("bear_signals", [])),
                "direction": "bull" if any("LIQSWEEP:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("LIQSWEEP:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
            {
                "signal_id": "FVG",
                "value": None,
                "threshold": None,
                "firing": any("FVG:" in s for s in breakdown.get("bull_signals", []) + breakdown.get("bear_signals", [])),
                "direction": "bull" if any("FVG:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("FVG:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
            {
                "signal_id": "VOLDIV",
                "value": None,
                "threshold": None,
                "firing": any("VOLDIV:" in s for s in breakdown.get("bull_signals", []) + breakdown.get("bear_signals", [])),
                "direction": "bull" if any("VOLDIV:" in s for s in breakdown.get("bull_signals", [])) else (
                    "bear" if any("VOLDIV:" in s for s in breakdown.get("bear_signals", [])) else "neutral"
                ),
            },
        ]

        try:
            await db.upsert_signal_state(self._base_asset, signals)
        except Exception:
            pass  # non-critical — don't break trading for signal state

    # ======================================================================
    # SIGNAL BUILDERS
    # ======================================================================

    def _build_entry_signal(
        self, side: str, price: float, amount: float, reason: str,
        order_type: str = "market",
    ) -> Signal | None:
        """Build an entry signal with ATR-dynamic SL. Trail handles the TP."""
        setup_type = self._classify_setup(reason)

        # ── Setup disable gate (from dashboard setup_config) ──────
        if not self._setup_config.get(setup_type, True):
            self.logger.info(
                "[%s] SETUP_DISABLED: %s — skipping entry", self.pair, setup_type,
            )
            return None

        self.logger.info(
            "[%s] %s -> %s entry (%s) SL=%.2f%% TP=%.2f%% ATR=%.3f%% setup=%s",
            self.pair, reason, side.upper(), order_type,
            self._sl_pct, self._tp_pct, self._last_atr_pct, setup_type,
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
                          "atr_pct": self._last_atr_pct,
                          "setup_type": setup_type},
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
                          "atr_pct": self._last_atr_pct,
                          "setup_type": setup_type},
            )

    def _exit_signal(self, price: float, side: str, reason: str, peak_pnl: float = 0.0) -> Signal:
        """Build an exit signal for the current position.

        peak_pnl must be pre-computed by the caller BEFORE _record_scalp_result
        resets entry_price/highest_since_entry/lowest_since_entry.
        """
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
            metadata={"peak_pnl": round(peak_pnl, 4)},
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
            now = time.monotonic()
            if now - self._last_reject_log >= 30:
                self._last_reject_log = now
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
        self._profit_floor_pct = -999.0  # reset ratcheting profit floor
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
        self._profit_floor_pct = -999.0  # reset ratcheting profit floor
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
