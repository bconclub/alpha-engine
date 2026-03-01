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
  - NET breakeven at +0.30% peak (covers fees) — only after trail has chance to catch
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

ENTRY — TWO-TIER SIGNAL SYSTEM:
  MOMENTUM PATH (existing): 3-of-4 signals + momentum gate → enter immediately
  TIER 1 PATH (new): leading signals detected → PENDING (no order) → wait for
    momentum confirmation → CONFIRMED → order placed (pre-confirmed, no in-position risk)
  TIER 1 (leading/anticipatory):
    - Volume Anticipation: vol >= 1.5x with <0.10% momentum (institutions loading)
    - BB Squeeze: BB inside Keltner Channel (volatility compression)
    - RSI Approach: 32-38 or 62-68 (approaching reversal, not yet extreme)
  TIER 2 (confirming — momentum path or T1 confirmation):
    - Momentum, Volume, RSI extreme, BB, Mom5m, Trend Cont, VWAP
  Direction from order flow (not past momentum):
    - Volume + BB position, Squeeze + EMA ribbon, RSI approach zones
  PENDING T1 lifecycle (no order placed until confirmed):
    - T1 signals fire → store pending (side, reason, timestamp)
    - Each 5s tick: check momentum confirms (0.10%+) → execute order
    - Counter-momentum (0.15%+) → T1_REJECTED (zero cost)
    - T1 signals fade → T1_EXPIRED (zero cost)
    - 30s timeout → T1_TIMEOUT (zero cost)
  LEVERAGE: Fixed 20x for all entries (capped — 50x/30x removed for capital safety)
  OVERRIDE: RSI < 30 or > 70 → enter immediately (strong extreme)
  SETUP: Each entry classified by dominant signal pattern

EXIT — 3-PHASE SYSTEM (AGGRESSIVE PROFIT LOCK):
  PHASE 1 (0-30s): HANDS OFF — all entries arrive pre-confirmed
    - ONLY exit on hard SL, ratchet floor, hard TP
    - EXCEPTION: if peak PnL >= +0.5%, skip to Phase 2 immediately
  PHASE 2 (30s-10 min): WATCH + TRAIL
    - If PnL > +0.30% → breakeven at entry+fees (net breakeven protection)
    - If PnL > +0.15% → activate trailing (0.10% tight distance — early lock)
    - Trail tiers tightened: +2%=0.50%, +3%=0.70%
    - Pullback exit at 30% retracement from peak
  PHASE 3 (10-30 min): TRAIL OR CUT
    - Trailing active → let it trail with tight distance
    - Still negative after 10 min → FLAT exit
    - Hard timeout at 30 min
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
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
    STOP_LOSS_PCT = 0.30              # default fallback (was 0.25 — noise sweeps)
    MIN_TP_PCT = 1.50                 # default TP
    PAIR_SL_FLOOR: dict[str, float] = {
        "BTC": 0.30,   # BTC: 0.30% price = 6% capital at 20x (was 0.25 — noise sweeps)
        "ETH": 0.35,   # ETH: 0.35% price = 7% capital at 20x (was 0.25 — noise sweeps)
        "XRP": 0.40,   # XRP: 0.40% price = 8% capital at 20x (was 0.25 — noise sweeps)
        "SOL": 0.40,   # SOL: 0.40% price = 8% capital at 20x (was 0.25 — noise sweeps)
    }
    # Per-pair SL caps (normal conditions): wider for volatile pairs
    PAIR_SL_CAP: dict[str, float] = {
        "BTC": 0.50,   # BTC/ETH: tighter cap
        "ETH": 0.50,
        "XRP": 0.60,   # XRP/SOL: wider cap for volatile pairs
        "SOL": 0.60,
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
    MOVE_SL_TO_ENTRY_PCT = 0.30       # breakeven matches trail activation

    # ── Hard TP safety net ────────────────────────────────────────────
    HARD_TP_CAPITAL_PCT = 10.0        # 10% capital gain → exit

    # ── Fee-aware minimum — don't exit tiny profits ───────────────────
    FEE_MINIMUM_GROSS_USD = 0.10      # skip discretionary exit unless gross > $0.10 (protective exits always execute)

    # ── MOMENTUM RIDING EXIT SYSTEM ──────────────────────────────────
    # Two modes: ride momentum with wide ratchet floor, OR exit on signal reversal.
    # Ratchet floor table: peak PnL % → locked floor % (only moves UP)
    RATCHET_FLOOR_TABLE: list[tuple[float, float]] = [
        (0.20, 0.08),   # peak +0.20% → floor +0.08% (early lock)
        (0.30, 0.15),   # peak +0.30% → floor +0.15% (protect 50%)
        (0.50, 0.30),   # peak +0.50% → floor +0.30% (protect 60%)
        (1.00, 0.60),   # peak +1.00% → floor +0.60% (protect 60%)
        (2.00, 1.20),   # peak +2.00% → floor +1.20% (protect 60%)
        (3.00, 2.00),   # peak +3.00% → floor +2.00% (protect 67%)
        (5.00, 3.50),   # peak +5.00% → floor +3.50% (protect 70%)
    ]

    # Signal reversal exit thresholds (exit IMMEDIATELY at market)
    RSI_REVERSAL_LONG = 70            # long exit when RSI crosses above 70
    RSI_REVERSAL_SHORT = 30           # short exit when RSI crosses below 30
    MOMENTUM_DYING_PCT = 0.02         # exit if abs(momentum) drops below 0.02% (was 0.04 — normal oscillation)
    MOM_FLIP_CONFIRM_SECONDS = 15     # momentum must stay flipped for 15s before reversal exit
    MOM_DYING_CONFIRM_SECONDS = 20    # momentum must stay dead (<0.02%) for 20s before reversal
    MOM_FADE_CONFIRM_SECONDS = 15     # momentum must stay dead for 15s before MOMENTUM_FADE profit exit
    MOM_FADE_MIN_HOLD = 90            # minimum 90s hold before MOMENTUM_FADE can fire
    MOM_FADE_TREND_HOLD = 120         # if trend-aligned, extend min hold to 120s
    MOM_FADE_TREND_CONFIRM = 20       # if trend-aligned, extend confirmation to 20s
    REVERSAL_MIN_PROFIT_PCT = 0.30    # need at least +0.30% peak to consider reversal exit (was 0.10 — exiting dust)
    DEAD_MOM_MIN_HOLD = 180           # minimum 180s (3 min) hold before DEAD_MOMENTUM can fire (was 60s — too aggressive)

    # ── Trailing stop tiers: peak PnL % → trail distance % from peak price ─
    # When peak crosses a tier, trail_stop_price = peak * (1 - trail_dist/100)
    # SL is max(hard_sl, trail_stop) for longs — only ever tightens.
    TRAIL_TIER_TABLE: list[tuple[float, float]] = [
        # Activation raised to 0.25% — sub-0.25% trades exit via ratchet/reversal
        # instead of trailing to breakeven and losing to fees.
        # Old 0.15% tier removed: was exiting at +0.05%, net loss after 0.083% RT fees.
        (0.25, 0.10),   # peak +0.25% → trail 0.10% = exits ~+0.15% = +0.07% net ✅
        (0.35, 0.12),   # peak +0.35% → trail 0.12% = exits ~+0.23% = +0.15% net ✅
        (0.50, 0.15),   # peak +0.50% → trail 0.15% = exits ~+0.35% = +0.27% net ✅
        (1.00, 0.20),   # peak +1.00% → trail 0.20% = exits ~+0.80% = big win
        (2.00, 0.30),   # peak +2.00% → trail 0.30% = exits ~+1.70% = huge win
        (3.00, 0.40),   # peak +3.00% → trail 0.40% = exits ~+2.60% = monster
        (5.00, 0.60),   # peak +5.00% → trail 0.60% = exits ~+4.40% = moon
    ]
    TRAIL_MOMENTUM_WIDEN = 1.5    # 50% wider trail when momentum aligned + peak > 0.20%
    TRAIL_MOM_ALIVE_PCT = 0.05    # minimum |momentum| to consider "alive"

    # ── Legacy trailing defaults (futures=0, spot overrides in __init__) ─
    TRAILING_ACTIVATE_PCT = 0.0       # futures: no trailing (momentum riding instead)
    TRAILING_DISTANCE_PCT = 0.0       # futures: no trailing (ratchet floor instead)

    # ── DISABLED PAIRS — skip entirely ────────────────────────────────
    DISABLED_PAIRS: set[str] = set()  # controlled via dashboard pair_config only

    # ── DISABLED SETUPS — controlled via dashboard setup_config only (no hardcoded blocks)

    # ── Entry thresholds — 3-of-4 with 15m trend soft weight ────────────
    # Layer 3 (momentum safety net) — high gates, only catches big moves
    MOMENTUM_MIN_PCT = 0.30           # 0.30%+ move in 60s (real momentum, not noise — was 0.20)
    MOMENTUM_30S_MIN_PCT = 0.12      # 30s window threshold (raised from 0.06)
    VOL_SPIKE_RATIO = 1.5             # need real institutional volume (was 0.6)
    RSI_EXTREME_LONG = 40             # Level 5/10 — RSI < 40 = oversold → long (was 35)
    RSI_EXTREME_SHORT = 60            # Level 5/10 — RSI > 60 = overbought → short (was 60→65→60)
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

    # ── TIER 1 ANTICIPATORY ENTRY ──────────────────────────────────
    # Leading signals: enter BEFORE momentum confirms, exit if unconfirmed
    TIER1_VOL_RATIO = 1.5             # volume anticipation: 1.5x+ with <0.10% momentum
    TIER1_VOL_MAX_MOM = 0.10          # max momentum for "volume without move" pattern
    TIER1_RSI_APPROACH_LONG = (32, 38)  # approaching oversold (not yet extreme)
    TIER1_RSI_APPROACH_SHORT = (62, 68) # approaching overbought (not yet extreme)
    TIER1_MIN_SIGNALS = 2             # need 2+ T1 signals for anticipatory entry

    # ── TIER 1 CONFIRMATION WINDOW ─────────────────────────────────
    CONFIRM_MOM_PCT = 0.15            # momentum threshold for tier1 confirmation (was 0.06 → 0.15)
    CONFIRM_COUNTER_PCT = 0.10        # counter-momentum → immediate rejection (was 0.15)
    T1_ACCEL_CONFIRM_VEL = 0.03      # velocity_5s for acceleration-based T1 confirm

    # ── TIER-BASED POSITION SIZING ─────────────────────────────────
    TIER1_SIZE_3_MULT = 1.0           # 3+ T1 signals → full size
    TIER1_SIZE_2_MULT = 1.0           # 2 T1 signals → full size (was 0.60)
    TIER1_SIZE_1_MULT = 0.80          # 1 T1 + 2 T2 → 80% (was 0.40)
    SURVIVAL_BALANCE = 20.0           # below this, cap allocation
    SURVIVAL_MAX_ALLOC = 30.0         # max alloc % in survival mode
    MAX_SL_LOSS_PCT = 5.0             # max SL loss = 5% of TOTAL balance (not collateral)

    # ── MOM-PATH DECELERATION FILTER ─────────────────────────────
    DECEL_RECENT_WINDOW_S = 10        # recent 10s window for acceleration check
    DECEL_PRIOR_WINDOW_S = 10         # prior 10s window (10-20s ago)
    DECEL_MOM_FLOOR_PCT = 0.08        # minimum 10s momentum in entry direction (was 0.05 = 1 tick noise)

    # ── SIDEWAYS / LOW-VOL RANGE GATE ────────────────────────────
    SIDEWAYS_MOM_GATE = 0.20           # minimum |momentum| during SIDEWAYS regime
    ATR_CONTRACTION_RATIO = 0.50       # current ATR < 50% of avg = contracted range
    RANGE_MOM_GATE = 0.30              # raised momentum gate during low-vol range (vs 0.20%)

    # ── DYNAMIC RANGE GATE (replaces 15m trend gate) ──────────────
    RANGE_LOOKBACK_CANDLES = 30        # 30 x 1m = 30 min rolling window
    RANGE_EXTREME_PCT = 0.20           # bottom/top 20% of range = extreme zone
    RANGE_DEAD_FLAT_PCT = 0.05         # range < 0.05% of price = dead flat, skip gate

    # ── 5-MINUTE COUNTER-TREND FILTER (secondary, when 15m=neutral) ─
    COUNTER_TREND_SOFT_PCT = 0.15     # require 4/4 signals when 5m opposes direction
    COUNTER_TREND_HARD_PCT = 0.40     # block entry entirely when 5m strongly opposes

    # ── LAYER 1: WS ACCELERATION ENTRY ────────────────────────────
    ACCEL_MIN_VELOCITY = 0.08         # 0.08% in 5s = real move (fast exchanges)
    ACCEL_MIN_POSITIVE = 0.01         # acceleration must be positive by at least this
    ACCEL_MIN_TICKS = 3               # minimum ticks in 5s window (legacy, see _FAST/_SLOW)
    ACCEL_COOLDOWN = 30               # seconds between accel entries on same pair
    ACCEL_MIN_SUPPORT = 2             # need 2/4 cached indicator support

    # ── Exchange-aware ACCEL tuning (tick-rate adaptation) ────────
    ACCEL_MIN_TICKS_FAST = 3          # Kraken/Bybit (~60 ticks/min)
    ACCEL_MIN_TICKS_SLOW = 1          # Delta (~12 ticks/min, ~1 per 5s)
    ACCEL_VELOCITY_WINDOW_FAST = 5.0  # seconds — standard window
    ACCEL_VELOCITY_WINDOW_SLOW = 10.0 # seconds — wider to capture 2+ ticks
    ACCEL_MIN_VELOCITY_SLOW = 0.10    # 0.10% over 10s (was 0.06 — too sensitive to Delta ticks)

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

    # ── Adaptive widening — 60min idle, 10% loosening (was disabled, was 30min/20%)
    IDLE_WIDEN_SECONDS = 60 * 60      # 60 min idle before thresholds loosen (was 30 min)
    IDLE_WIDEN_FACTOR = 0.90          # 10% loosening (was 0.80=20%, then disabled at 1.0)

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
        "XRP": 35.0,   # best performer (was 30)
        "ETH": 30.0,   # mixed but catches big moves
        "BTC": 20.0,   # low win rate but diversification
        "SOL": 15.0,   # enable/disable via dashboard pair_config
    }
    # Safety limits for dynamic position sizing
    MAX_COLLATERAL_PCT = 80.0         # never use more than 80% of balance on 1 position
    MAX_TOTAL_EXPOSURE_PCT = 90.0     # max 90% total collateral across all positions
    SECOND_POS_ALLOC_FACTOR = 0.60    # 2nd position gets 60% of normal allocation
    # Minimum signal strength per pair (4/4 — no more coin-flip 3/4 entries)
    # RSI override (RSI <30 or >70) is the ONLY exception at 3/4.
    PAIR_MIN_STRENGTH: dict[str, int] = {
        "XRP": 4,    # was 3/4 — too many marginal entries dying at 0.10% peak
        "ETH": 4,    # was 3/4 — need full confirmation
        "BTC": 4,    # was 3/4 — consistent with all pairs
        "SOL": 4,    # was 3/4 — no weak entries
    }
    # Adaptive: track last N trades per pair for win-rate-based adjustment
    PERF_WINDOW = 5                     # look at last 5 trades per pair
    PERF_LOW_WR_THRESHOLD = 0.20        # <20% WR in window → reduce to minimum
    PERF_HIGH_WR_THRESHOLD = 0.60       # >60% WR in window → boost allocation
    MAX_POSITIONS = 99                  # no hard cap — allocation % handles sizing naturally
    MAX_SPREAD_PCT = 0.15

    # ── Warmup — accept weaker signals for first 5 min after startup ────
    WARMUP_SECONDS = 5 * 60            # 5 min warmup: still requires 3/4 (no free passes)
    WARMUP_MIN_STRENGTH = 4            # during warmup, same 4/4 gate (was 3, was 2)

    # ── Cooldown / loss protection (PER-PAIR: BTC streak doesn't affect XRP) ─
    SL_COOLDOWN_SECONDS = 60             # 60s pause after SL hit (per pair, was 120s)
    REVERSAL_COOLDOWN_SECONDS = 2 * 60 # 2 min pause after REVERSAL exit (ALL directions on pair)
    CONSECUTIVE_LOSS_LIMIT = 3          # after 3 consecutive losses on same pair...
    STREAK_PAUSE_SECONDS = 5 * 60      # ...pause that pair for 5 min
    POST_STREAK_STRENGTH = 3            # first trade after streak pause needs 3/4
    MIN_STRENGTH_FOR_2ND = 3            # 2nd position needs 3/4+ signal strength
    DIR_LOW_WR_THRESHOLD = 0.30         # <30% WR for direction on pair → require 4/4
    DIR_LOW_WR_MIN_STRENGTH = 4         # signal strength required when direction is losing

    # ── Rate limiting / risk ──────────────────────────────────────────────
    MAX_TRADES_PER_HOUR = 10          # keep trading aggressively

    # ── Constants snapshot (for auto-changelog param change detection) ───
    @classmethod
    def get_constants_snapshot(cls) -> dict[str, object]:
        """Return a JSON-serializable dict of all tunable trading constants."""
        return {
            # SL / TP
            "STOP_LOSS_PCT": cls.STOP_LOSS_PCT,
            "MIN_TP_PCT": cls.MIN_TP_PCT,
            "PAIR_SL_FLOOR": cls.PAIR_SL_FLOOR,
            "PAIR_SL_CAP": cls.PAIR_SL_CAP,
            "PAIR_TP_FLOOR": cls.PAIR_TP_FLOOR,
            "ATR_SL_MULTIPLIER": cls.ATR_SL_MULTIPLIER,
            "ATR_TP_MULTIPLIER": cls.ATR_TP_MULTIPLIER,
            # Phase timing
            "PHASE1_SECONDS": cls.PHASE1_SECONDS,
            "PHASE1_SKIP_AT_PEAK_PCT": cls.PHASE1_SKIP_AT_PEAK_PCT,
            "PHASE2_SECONDS": cls.PHASE2_SECONDS,
            "MAX_HOLD_SECONDS": cls.MAX_HOLD_SECONDS,
            "FLATLINE_SECONDS": cls.FLATLINE_SECONDS,
            "FLATLINE_MIN_MOVE_PCT": cls.FLATLINE_MIN_MOVE_PCT,
            "MOVE_SL_TO_ENTRY_PCT": cls.MOVE_SL_TO_ENTRY_PCT,
            "HARD_TP_CAPITAL_PCT": cls.HARD_TP_CAPITAL_PCT,
            # Momentum / reversal exits
            "MOMENTUM_DYING_PCT": cls.MOMENTUM_DYING_PCT,
            "MOM_FLIP_CONFIRM_SECONDS": cls.MOM_FLIP_CONFIRM_SECONDS,
            "MOM_DYING_CONFIRM_SECONDS": cls.MOM_DYING_CONFIRM_SECONDS,
            "MOM_FADE_CONFIRM_SECONDS": cls.MOM_FADE_CONFIRM_SECONDS,
            "MOM_FADE_MIN_HOLD": cls.MOM_FADE_MIN_HOLD,
            "MOM_FADE_TREND_HOLD": cls.MOM_FADE_TREND_HOLD,
            "MOM_FADE_TREND_CONFIRM": cls.MOM_FADE_TREND_CONFIRM,
            "DEAD_MOM_MIN_HOLD": cls.DEAD_MOM_MIN_HOLD,
            "REVERSAL_MIN_PROFIT_PCT": cls.REVERSAL_MIN_PROFIT_PCT,
            "RSI_REVERSAL_LONG": cls.RSI_REVERSAL_LONG,
            "RSI_REVERSAL_SHORT": cls.RSI_REVERSAL_SHORT,
            # Trail tiers (tuples → lists for JSON)
            "TRAIL_TIER_TABLE": [[t, d] for t, d in cls.TRAIL_TIER_TABLE],
            "RATCHET_FLOOR_TABLE": [[t, f] for t, f in cls.RATCHET_FLOOR_TABLE],
            # Entry thresholds
            "MOMENTUM_MIN_PCT": cls.MOMENTUM_MIN_PCT,
            "MOMENTUM_30S_MIN_PCT": cls.MOMENTUM_30S_MIN_PCT,
            "VOL_SPIKE_RATIO": cls.VOL_SPIKE_RATIO,
            "RSI_EXTREME_LONG": cls.RSI_EXTREME_LONG,
            "RSI_EXTREME_SHORT": cls.RSI_EXTREME_SHORT,
            "BB_MEAN_REVERT_UPPER": cls.BB_MEAN_REVERT_UPPER,
            "BB_MEAN_REVERT_LOWER": cls.BB_MEAN_REVERT_LOWER,
            "RSI_OVERRIDE_LONG": cls.RSI_OVERRIDE_LONG,
            "RSI_OVERRIDE_SHORT": cls.RSI_OVERRIDE_SHORT,
            # Position sizing
            "CAPITAL_PCT_FUTURES": cls.CAPITAL_PCT_FUTURES,
            "PAIR_ALLOC_PCT": cls.PAIR_ALLOC_PCT,
            "MAX_COLLATERAL_PCT": cls.MAX_COLLATERAL_PCT,
            "SURVIVAL_BALANCE": cls.SURVIVAL_BALANCE,
            "SURVIVAL_MAX_ALLOC": cls.SURVIVAL_MAX_ALLOC,
            "MAX_SL_LOSS_PCT": cls.MAX_SL_LOSS_PCT,
            "PAIR_MIN_STRENGTH": cls.PAIR_MIN_STRENGTH,
            # Cooldowns
            "SL_COOLDOWN_SECONDS": cls.SL_COOLDOWN_SECONDS,
            "REVERSAL_COOLDOWN_SECONDS": cls.REVERSAL_COOLDOWN_SECONDS,
            "CONSECUTIVE_LOSS_LIMIT": cls.CONSECUTIVE_LOSS_LIMIT,
            "STREAK_PAUSE_SECONDS": cls.STREAK_PAUSE_SECONDS,
            # Rate limits
            "MAX_TRADES_PER_HOUR": cls.MAX_TRADES_PER_HOUR,
            "MAX_SPREAD_PCT": cls.MAX_SPREAD_PCT,
        }

    # ── Class-level shared state ──────────────────────────────────────────
    _live_pnl: dict[str, float] = {}           # pair → current unrealized P&L % (updated every tick)
    _pair_trade_history: dict[str, list[bool]] = {}  # base_asset → list of win/loss booleans (last N)
    _pair_dir_trade_history: dict[str, list[bool]] = {}  # "base_asset:long" → last N win/loss for that direction
    # ── Per-pair streak/cooldown (BTC losses don't pause XRP) ────────────
    _pair_last_sl_time: dict[str, float] = {}            # base_asset → monotonic time of last SL
    _pair_consecutive_losses: dict[str, int] = {}        # base_asset → streak count
    _pair_streak_pause_until: dict[str, float] = {}      # base_asset → pause end time
    _pair_post_streak: dict[str, bool] = {}              # base_asset → True if first trade after streak
    _pair_last_reversal_time: dict[str, float] = {}      # base_asset → monotonic time of last REVERSAL exit
    _pair_last_reversal_side: dict[str, str] = {}        # base_asset → side of the REVERSAL exit (blocks same-dir re-entry)
    # ── Anti-churn filters (GPFC B + C) ─────────────────────────────────
    _pair_last_entry_momentum: dict[str, float] = {}     # "base_asset:long" → momentum at entry
    _pair_last_exit_time_mono: dict[str, float] = {}     # "base_asset:long" → monotonic time of last exit
    _pair_last_exit_price: dict[str, float] = {}         # base_asset → exit price
    _pair_last_exit_time_any: dict[str, float] = {}      # base_asset → monotonic time (any direction)
    # ── DEAD_MOMENTUM streak cooldown (GPFC #6) ──────────────────────────
    _pair_dead_streak: dict[str, int] = {}               # "base_asset:side" → consecutive DEAD_MOMENTUM count
    _pair_dead_cooldown_until: dict[str, float] = {}     # "base_asset:side" → monotonic time cooldown expires
    DEAD_STREAK_LIMIT = 3                                 # 3 consecutive DEAD exits → cooldown
    DEAD_STREAK_COOLDOWN_S = 600                          # 10 minute cooldown
    # ── LAYER 1: WS acceleration shared state ───────────────────────────
    _tick_buffer: dict[str, deque] = {}                   # base_asset → deque of (mono_time, price)
    _last_accel_entry_time: dict[str, float] = {}         # base_asset → last accel entry time
    _cached_signals: dict[str, dict] = {}                 # base_asset → last candle scan indicators

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
        exchange_id: str | None = None,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.trade_exchange: ccxt.Exchange | None = exchange
        self.is_futures = is_futures
        self._exchange_id: str = exchange_id or ("delta" if is_futures else "binance")
        # Leverage and fees depend on exchange
        if self._exchange_id == "bybit":
            self.leverage: int = min(config.bybit.leverage, 20) if is_futures else 1
        else:
            self.leverage: int = min(config.delta.leverage, 20) if is_futures else 1
        self.capital_pct: float = self.CAPITAL_PCT_FUTURES if is_futures else self.SPOT_CAPITAL_PCT
        self._market_analyzer = market_analyzer  # for 15m trend direction

        base_asset = pair.split("/")[0] if "/" in pair else pair.replace("USD", "").replace(":USD", "")
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
        self._last_momentum_60s: float = 0.0  # cached for trail widening
        self._trail_widened: bool = False      # momentum-aware trail state
        self._atr_history: deque[float] = deque(maxlen=60)  # rolling ~1hr of ATR samples
        self._price_history: deque[tuple[float, float]] = deque(maxlen=120)  # (monotonic_time, price) for 10s/30s momentum
        self._last_entry_momentum: float = 0.0  # strongest momentum at last signal (GPFC B)
        self._high_vol: bool = False  # True when ATR > 1.5x normal

        # ── Market regime detection ──────────────────────────────────────
        self._range_position: float | None = None  # 0.0=range bottom, 1.0=range top
        self._market_regime: str = "SIDEWAYS"     # TRENDING_UP, TRENDING_DOWN, SIDEWAYS, CHOPPY
        self._regime_since: float = time.monotonic()  # when current regime started
        self._chop_score: float = 0.0             # 0.0=smooth, 1.0=choppy
        self._atr_ratio: float = 1.0              # current ATR / rolling avg ATR
        self._net_change_30m: float = 0.0         # net price change over 30 candles (%)
        self._chop_clear_count: int = 0           # consecutive checks with chop_score < 0.45

        # Position state
        self.in_position = False
        self.position_side: str | None = None  # "long" or "short"
        self.entry_price: float = 0.0
        self.entry_amount: float = 0.0
        self.entry_time: float = 0.0
        self.highest_since_entry: float = 0.0
        self.lowest_since_entry: float = float("inf")
        self._trailing_active: bool = False
        self._trail_stop_price: float = 0.0      # actual trailing stop price (0 = not yet active)
        self._trail_distance_pct: float = 0.0     # current trail distance tier in %
        # Spot uses wider trail distance and activation (no leverage, needs room)
        # Futures uses momentum-riding ratchet floor system instead of tight trail
        if not is_futures:
            self.TRAILING_ACTIVATE_PCT = self.SPOT_TRAIL_ACTIVATE_PCT
            self.TRAILING_DISTANCE_PCT = self.SPOT_TRAIL_DISTANCE_PCT
            self._trail_distance_pct = self.SPOT_TRAIL_DISTANCE_PCT
        self._peak_unrealized_pnl: float = 0.0  # track peak P&L for decay exit
        self._profit_floor_pct: float = -999.0  # ratcheting capital floor (not yet locked)
        self._in_position_tick: int = 0  # counts 1s ticks while in position (for OHLCV refresh)

        # ── Real-time balance refresh (for entry sizing) ──────
        self._last_balance_refresh: float = 0.0  # monotonic time of last balance API call

        # ── Dashboard-driven config (loaded from DB by main.py) ──────
        self._pair_enabled: bool = True       # can be disabled via pair_config
        self._allocation_pct: float = self.PAIR_ALLOC_PCT.get(base_asset, 15.0)
        self._setup_config: dict[str, bool] = {}  # {setup_type: enabled}

        # Previous RSI for reversal detection
        self._prev_rsi: float = 50.0
        # Momentum flip confirmation timer (0 = not flipped)
        self._mom_flip_since: float = 0.0
        self._mom_dying_since: float = 0.0
        self._mom_fade_since: float = 0.0   # MOMENTUM_FADE confirmation timer
        # Suppress repeated reversal exit logs (only log first detection)
        self._reversal_exit_logged: bool = False

        # Rate limiting
        self._hourly_trades: list[float] = []

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
        # Skip reason — why the bot isn't entering (dashboard reads this)
        self._skip_reason: str = ""
        # 5-minute price direction (from 1m candles, updated each full indicator tick)
        self._price_5m_pct: float = 0.0

        # BB Squeeze tracking (signal #8)
        self._squeeze_tick_count: int = 0

        # ── Tier 1 anticipatory entry state ──────────────────────────
        self._entry_path: str = "momentum"     # "momentum" or "tier1"
        self._tier1_count: int = 0             # tier1 signals at entry
        self._tier2_count: int = 0             # tier2 signals at entry
        # Pending tier1: T1 signals fire → store here, wait for momentum confirmation
        # before placing any order. Dict with keys: side, reason, strength,
        # tier1_count, tier2_count, timestamp, price.  None = no pending.
        self._pending_tier1: dict[str, Any] | None = None
        self._trade_leverage: int = self.leverage  # per-trade dynamic leverage

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
        if self._exchange_id == "bybit":
            rt_mixed = config.bybit.mixed_round_trip * 100
            rt_taker = config.bybit.taker_round_trip * 100
        elif self._exchange_id == "delta":
            rt_mixed = config.delta.mixed_round_trip * 100
            rt_taker = config.delta.taker_round_trip * 100
        else:
            # Binance spot: 0.1% per side (check for BNB discount)
            rt_mixed = 0.20  # 0.1% × 2 = 0.2% round trip
            rt_taker = 0.20
        ratchet_str = " → ".join(f"+{p}%→{f}%" for p, f in self.RATCHET_FLOOR_TABLE)
        exit_mode = "MOMENTUM_RIDING" if self.is_futures else f"SPOT_TRAIL@+{self.TRAILING_ACTIVATE_PCT}%({self.TRAILING_DISTANCE_PCT}%)"
        disabled_tag = f" DISABLED={','.join(self.DISABLED_PAIRS)}" if self.DISABLED_PAIRS else ""
        max_pos = self.SPOT_MAX_POSITIONS if not self.is_futures else self.MAX_POSITIONS
        self.logger.info(
            "[%s] PHASE-BASED v6.1 ACTIVE (%s) — tick=1s/5s(dynamic), "
            "SL=%.2f%% TP=%.2f%% Phase1=%ds(skip@+%.1f%%) Phase2=%ds MaxHold=%ds "
            "Exit=%s MoveToEntry@+%.1f%% Flat=%ds "
            "MaxPos=%d Alloc=%.0f%% SLcool=%ds LossStreak=%d→%ds "
            "Mom=%.2f%% Vol=%.1fx RSI-Override=%d/%d%s%s",
            self.pair, tag,
            self._sl_pct, self._tp_pct,
            self.PHASE1_SECONDS, self.PHASE1_SKIP_AT_PEAK_PCT,
            self.PHASE2_SECONDS, self.MAX_HOLD_SECONDS,
            exit_mode,
            self.MOVE_SL_TO_ENTRY_PCT, self.FLATLINE_SECONDS,
            max_pos, self._allocation_pct,
            self.SL_COOLDOWN_SECONDS, self.CONSECUTIVE_LOSS_LIMIT,
            self.STREAK_PAUSE_SECONDS,
            self.MOMENTUM_MIN_PCT, self.VOL_SPIKE_RATIO,
            self.RSI_OVERRIDE_LONG, self.RSI_OVERRIDE_SHORT,
            disabled_tag,
            pos_info,
        )
        self.logger.info("[%s] Soul: %s", self.pair, soul_msg)

    def get_tick_interval(self) -> int:
        """Dynamic tick: 1s when holding a position, 3s when scanning."""
        return 1 if self.in_position else 3

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

    def _compute_range_position(self, df, current_price: float) -> float | None:
        """Price position in 30-candle rolling range. 0.0=bottom, 1.0=top.

        Returns None if insufficient data or dead-flat market (skip gate).
        """
        if df is None or len(df) < self.RANGE_LOOKBACK_CANDLES:
            return None
        window = df.iloc[-self.RANGE_LOOKBACK_CANDLES:]
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())
        range_size = range_high - range_low
        if range_low <= 0 or (range_size / range_low * 100) < self.RANGE_DEAD_FLAT_PCT:
            return None  # dead flat — no meaningful range
        return (current_price - range_low) / range_size

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

            # Track rolling ATR for high-vol detection
            self._atr_history.append(atr_pct)
            normal_atr = sum(self._atr_history) / len(self._atr_history) if self._atr_history else atr_pct
            self._high_vol = atr_pct > normal_atr * 1.5 and len(self._atr_history) >= 5

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

            # Safety cap — per-pair caps, widen during high volatility
            if not self.is_futures:
                sl_cap = 3.00
            elif self._high_vol:
                base_cap = self.PAIR_SL_CAP.get(self._base_asset, 0.50)
                sl_cap = min(base_cap + 0.15, atr_pct * 2.0)
                self.logger.info(
                    "[%s] HIGH VOL: ATR=%.3f%% (normal=%.3f%%), SL cap widened to %.2f%%",
                    self.pair, atr_pct, normal_atr, sl_cap,
                )
            else:
                sl_cap = self.PAIR_SL_CAP.get(self._base_asset, 0.50)
            tp_cap = 6.00 if not self.is_futures else 5.00
            self._sl_pct = min(self._sl_pct, sl_cap)
            self._tp_pct = min(self._tp_pct, tp_cap)
        except Exception:
            # Silently keep existing values if ATR calc fails
            pass

    def _detect_market_regime(self, df: pd.DataFrame) -> str:
        """Detect market regime from 30x 1m candles.

        Returns one of: TRENDING_UP, TRENDING_DOWN, SIDEWAYS, CHOPPY.

        Uses 4 metrics:
        1. Net price change over 30 candles (direction)
        2. Direction ratio — candles moving in same direction (trend strength)
        3. Chop score — direction change frequency (0=smooth, 1=choppy)
        4. ATR ratio — current ATR vs rolling average (volatility spike)

        CHOPPY blocks ALL entries. Other regimes adjust signal requirements.
        """
        try:
            closes = df["close"].tolist()
            opens = df["open"].tolist()
            if len(closes) < 10:
                return self._market_regime  # not enough data, keep current

            # 1. Net price change over 30 candles
            net_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0
            self._net_change_30m = net_change

            # 2. Direction ratio — count candles moving in same direction
            up_candles = sum(1 for i in range(len(closes)) if closes[i] > opens[i])
            n = len(closes)
            direction_ratio = max(up_candles, n - up_candles) / n

            # 3. Chop score — count direction changes (close > prev close)
            direction_changes = 0
            for i in range(2, len(closes)):
                curr_up = closes[i] > closes[i - 1]
                prev_up = closes[i - 1] > closes[i - 2]
                if curr_up != prev_up:
                    direction_changes += 1
            chop_score = direction_changes / (len(closes) - 2) if len(closes) > 2 else 0
            self._chop_score = chop_score

            # 4. ATR ratio — current ATR vs rolling average
            normal_atr = (
                sum(self._atr_history) / len(self._atr_history)
                if self._atr_history else self._last_atr_pct
            )
            atr_ratio = self._last_atr_pct / normal_atr if normal_atr > 0 else 1.0
            self._atr_ratio = atr_ratio

            # ── Classify regime ──────────────────────────────────────────
            old_regime = self._market_regime

            if chop_score > 0.60 and atr_ratio > 1.3:
                # High reversals + high vol = death zone
                new_regime = "CHOPPY"
            elif abs(net_change) > 0.15 and direction_ratio > 0.55:
                new_regime = "TRENDING_UP" if net_change > 0 else "TRENDING_DOWN"
            elif abs(net_change) < 0.08 and chop_score < 0.40:
                new_regime = "SIDEWAYS"
            else:
                new_regime = self._market_regime  # keep current — don't snap to SIDEWAYS on ambiguity

            # ── CHOPPY exit hysteresis: need 3 consecutive clean checks ──
            if old_regime == "CHOPPY" and new_regime != "CHOPPY":
                self._chop_clear_count += 1
                if self._chop_clear_count < 3:
                    # Stay choppy until 3 consecutive clean readings
                    new_regime = "CHOPPY"
                    self.logger.info(
                        "[%s] REGIME: still CHOPPY (clear %d/3, chop=%.2f, atr=%.1fx)",
                        self.pair, self._chop_clear_count, chop_score, atr_ratio,
                    )
                else:
                    self._chop_clear_count = 0  # reset on exit
            else:
                if new_regime == "CHOPPY":
                    self._chop_clear_count = 0  # reset when entering/staying choppy

            # ── Log regime changes ───────────────────────────────────────
            if new_regime != old_regime:
                self._regime_since = time.monotonic()
                labels = {
                    "TRENDING_UP": "TRENDING UP — favoring longs",
                    "TRENDING_DOWN": "TRENDING DOWN — favoring shorts",
                    "SIDEWAYS": "SIDEWAYS — normal scanning",
                    "CHOPPY": "CHOPPY — no trades, waiting for clarity",
                }
                self.logger.info(
                    "[%s] REGIME: %s (net=%.2f%%, dir=%.2f, chop=%.2f, atr=%.1fx)",
                    self.pair, labels.get(new_regime, new_regime),
                    net_change, direction_ratio, chop_score, atr_ratio,
                )

            self._market_regime = new_regime
            return new_regime

        except Exception:
            self.logger.debug("[%s] Regime detection error, keeping %s", self.pair, self._market_regime)
            return self._market_regime

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

            # Detect market regime (TRENDING_UP/DOWN, SIDEWAYS, CHOPPY)
            self._detect_market_regime(df)

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
            self._last_momentum_60s = momentum_60s  # store for trail widening

            # 30-second momentum from real-time price history deque (GPFC A)
            momentum_30s = 0.0
            if len(self._price_history) >= 2:
                now_mono = time.monotonic()
                for ts, px in reversed(self._price_history):
                    if now_mono - ts >= 28:  # ~30s ago (within 2s tolerance)
                        momentum_30s = ((current_price - px) / px * 100) if px > 0 else 0
                        break

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

            # ── 5-minute price direction (for counter-trend filter) ────
            if len(close) >= 6:
                _price_5m_ago = float(close.iloc[-6])
                self._price_5m_pct = ((current_price - _price_5m_ago) / _price_5m_ago) * 100
            else:
                self._price_5m_pct = 0.0

            # ── 15m trend (info/logging only — no longer blocks entries) ─
            trend_15m = self._get_15m_trend()

            # ── Dynamic range position (replaces 15m trend gate) ───────
            range_pos = self._compute_range_position(df, current_price)
            self._range_position = range_pos  # stored for gate chain below

            # ── Cache signals for Layer 1 acceleration entry ─────────
            bb_range_cache = bb_upper - bb_lower if bb_upper > bb_lower else 1e-9
            bb_pos_cache = (current_price - bb_lower) / bb_range_cache
            _avg_atr = sum(self._atr_history) / len(self._atr_history) if self._atr_history else self._last_atr_pct
            ScalpStrategy._cached_signals[self._base_asset] = {
                "rsi": rsi_now,
                "bb_position": bb_pos_cache,
                "vwap_above": current_price > vwap,
                "ema9_above_21": ema_9 > ema_21,
                "volume_ratio": vol_ratio,
                "price_5m_pct": self._price_5m_pct,
                "trend_15m": trend_15m,
                "range_position": range_pos,
                "market_regime": self._market_regime,
                "atr_pct": self._last_atr_pct,
                "atr_avg": _avg_atr,
                "timestamp": time.monotonic(),
            }

        # ── Heartbeat every 60 seconds ─────────────────────────────────
        if now - self._last_heartbeat >= 60:
            self._last_heartbeat = now
            tag = f"{self.leverage}x" if self.is_futures else "spot"
            if self.in_position:
                hold_sec = now - self.entry_time
                pnl_now = self._calc_pnl_pct(current_price)
                trail_tag = f" [TRAILING trail=$%.2f dist=%.2f%%]" % (self._trail_stop_price, self._trail_distance_pct) if self._trailing_active else ""
                self.logger.info(
                    "[%s] (%s) %s @ $%.2f | %ds | PnL=%+.2f%% | SL=%.2f%% | RSI=%.1f | ATR=%.3f%% | mom=%+.3f%%%s",
                    self.pair, tag, self.position_side, self.entry_price,
                    int(hold_sec), pnl_now, self._sl_pct, rsi_now, self._last_atr_pct, momentum_60s, trail_tag,
                )
            else:
                idle_sec = int(now - self._last_position_exit)
                self.logger.info(
                    "[%s] (%s) SCANNING %ds | $%.2f | RSI=%.1f | Vol=%.1fx | "
                    "mom60=%+.3f%% | %s | W/L=%d/%d skip=%d",
                    self.pair, tag, idle_sec, current_price, rsi_now, vol_ratio,
                    momentum_60s, self._market_regime,
                    self.hourly_wins, self.hourly_losses, self.hourly_skipped,
                )

        # ── In position: check exit ────────────────────────────────────
        if self.in_position:
            # Keep signal state timestamp fresh while in position so options_scalp
            # doesn't see it as stale. Preserve existing signal data (side/strength).
            if self.last_signal_state is not None:
                self.last_signal_state["timestamp"] = time.monotonic()
                self.last_signal_state["current_price"] = current_price

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
        self._skip_reason = ""  # reset each tick — set if we skip

        # Record price for deceleration / acceleration checks
        self._price_history.append((time.monotonic(), current_price))

        # Check position limits
        if self.risk_manager.has_position(self.pair):
            self._skip_reason = "ALREADY_IN_POSITION"
            # Refresh timestamp so options_scalp doesn't see stale signal
            if self.last_signal_state is not None:
                self.last_signal_state["timestamp"] = time.monotonic()
                self.last_signal_state["current_price"] = current_price
            return signals

        total_scalp = sum(
            1 for p in self.risk_manager.open_positions
            if p.strategy == "scalp"
        )
        max_pos = self.SPOT_MAX_POSITIONS if not self.is_futures else self.MAX_POSITIONS
        if total_scalp >= max_pos:
            self._skip_reason = f"MAX_POSITIONS ({total_scalp}/{max_pos})"
            # Refresh timestamp so options_scalp doesn't see stale signal
            if self.last_signal_state is not None:
                self.last_signal_state["timestamp"] = time.monotonic()
                self.last_signal_state["current_price"] = current_price
            return signals

        # ── COOLDOWN: pause after SL hit (PER PAIR) ────────────────
        # Deferred: 4/4 signals bypass cooldown, but signal_strength isn't known yet
        pair_sl_time = ScalpStrategy._pair_last_sl_time.get(self._base_asset, 0.0)
        _sl_cooldown_remaining = pair_sl_time + self.SL_COOLDOWN_SECONDS - now
        _sl_cooldown_active = _sl_cooldown_remaining > 0

        # ── STREAK PAUSE: after N consecutive losses on THIS PAIR ───
        pair_pause_until = ScalpStrategy._pair_streak_pause_until.get(self._base_asset, 0.0)
        if now < pair_pause_until:
            remaining = pair_pause_until - now
            pair_losses = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
            self._skip_reason = f"STREAK_PAUSE ({pair_losses}L, {int(remaining)}s)"
            if self._tick_count % 12 == 0:
                self.logger.info(
                    "[%s] STREAK PAUSE — %d consecutive losses on %s, %.0fs remaining",
                    self.pair, pair_losses, self._base_asset, remaining,
                )
            return signals

        # ── PHANTOM COOLDOWN: no entries for 60s after phantom clear ──
        if now < self._phantom_cooldown_until:
            remaining = self._phantom_cooldown_until - now
            self._skip_reason = f"PHANTOM_COOLDOWN ({int(remaining)}s)"
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
                    self._skip_reason = f"SPREAD_TOO_WIDE ({spread_pct:.2f}%)"
                    return signals
        except Exception:
            pass

        # Balance check
        available = self.risk_manager.get_available_capital(self._exchange_id)
        min_balance = self.MIN_NOTIONAL_SPOT if self._exchange_id == "binance" else 1.00
        if available < min_balance:
            self._skip_reason = f"LOW_BALANCE (${available:.2f})"
            if self._tick_count % 60 == 0:
                self.logger.info(
                    "[%s] Insufficient %s balance: $%.2f",
                    self.pair, self._exchange_id, available,
                )
            return signals

        # ── REGIME GATE: CHOPPY = no new entries ─────────────────────
        if self._market_regime == "CHOPPY":
            self._skip_reason = "REGIME_CHOPPY"
            if self._tick_count % 12 == 0:
                regime_sec = int(time.monotonic() - self._regime_since)
                self.logger.info(
                    "[%s] REGIME BLOCK: CHOPPY for %ds — no entries (chop=%.2f, atr=%.1fx)",
                    self.pair, regime_sec, self._chop_score, self._atr_ratio,
                )
            return signals



        # ── Adaptive widening: if idle 60+ min, loosen thresholds 10% ─
        idle_seconds = now - self._last_position_exit
        is_widened = idle_seconds >= self.IDLE_WIDEN_SECONDS

        # ══════════════════════════════════════════════════════════════
        # PENDING TIER 1 CHECK — no order placed yet, waiting for confirm
        # ══════════════════════════════════════════════════════════════
        entry = None  # will be set by pending confirm OR _detect_quality_entry
        if self._pending_tier1 is not None:
            pt1 = self._pending_tier1
            pt1_age = now - pt1["timestamp"]
            pt1_side = pt1["side"]

            # ── Compute tick velocity for acceleration-based T1 confirm ──
            _t1_vel_5s = 0.0
            _t1_ticks = ScalpStrategy._tick_buffer.get(self._base_asset)
            if _t1_ticks and len(_t1_ticks) >= 3:
                _t1_recent = [(t, p) for t, p in _t1_ticks if now - t <= 5.0]
                if len(_t1_recent) >= 2:
                    _t1_vel_5s = (_t1_recent[-1][1] - _t1_recent[0][1]) / _t1_recent[0][1] * 100

            if pt1_age >= self.PHASE1_SECONDS:
                # ── T1_TIMEOUT: 30s with no confirmation ──────────────
                self.logger.info(
                    "[%s] T1_TIMEOUT — no confirmation after %.0fs, clearing pending %s",
                    self.pair, pt1_age, pt1_side,
                )
                self._pending_tier1 = None
                # Zero fees, zero loss — just a skip

            elif (pt1_side == "long" and _t1_vel_5s >= self.T1_ACCEL_CONFIRM_VEL) or \
                 (pt1_side == "short" and _t1_vel_5s <= -self.T1_ACCEL_CONFIRM_VEL):
                # ── T1_ACCEL_CONFIRM: tick velocity confirms direction ──
                self._entry_path = "tier1"
                self._tier1_count = pt1["tier1_count"]
                self._tier2_count = pt1["tier2_count"]
                self._trade_leverage = self._calculate_leverage(
                    pt1["tier1_count"] + pt1["tier2_count"], momentum_60s, rsi_now,
                )
                entry = (pt1_side, pt1["reason"] + f" [ACCEL vel={_t1_vel_5s:+.3f}%] | LEV:{self._trade_leverage}x", True, pt1["strength"])
                self._pending_tier1 = None
                self.logger.info(
                    "[%s] T1_ACCEL_CONFIRM → EXECUTING — velocity=%+.3f%%/5s confirms %s after %.0fs, strength=%d/4",
                    self.pair, _t1_vel_5s, pt1_side, pt1_age, pt1["strength"],
                )

            elif (pt1_side == "long" and momentum_60s >= self.CONFIRM_MOM_PCT) or \
                 (pt1_side == "short" and momentum_60s <= -self.CONFIRM_MOM_PCT):
                # ── T1_CONFIRMED: candle momentum in pending direction ──
                self._entry_path = "tier1"
                self._tier1_count = pt1["tier1_count"]
                self._tier2_count = pt1["tier2_count"]
                self._trade_leverage = self._calculate_leverage(
                    pt1["tier1_count"] + pt1["tier2_count"], momentum_60s, rsi_now,
                )
                entry = (pt1_side, pt1["reason"] + f" | LEV:{self._trade_leverage}x", True, pt1["strength"])
                self._pending_tier1 = None
                self.logger.info(
                    "[%s] T1_CONFIRMED → EXECUTING — mom=%+.3f%% confirms %s after %.0fs, strength=%d/4",
                    self.pair, momentum_60s, pt1_side, pt1_age, pt1["strength"],
                )

            elif (pt1_side == "long" and momentum_60s <= -self.CONFIRM_COUNTER_PCT) or \
                 (pt1_side == "short" and momentum_60s >= self.CONFIRM_COUNTER_PCT):
                # ── T1_REJECTED: momentum went AGAINST pending direction
                self.logger.info(
                    "[%s] T1_REJECTED — counter-momentum %+.3f%% against pending %s at %.0fs",
                    self.pair, momentum_60s, pt1_side, pt1_age,
                )
                self._pending_tier1 = None

            else:
                # ── Still waiting — check if T1 signals still present ──
                # Hysteresis: don't re-check signal presence for first 10s (prevent flicker)
                if pt1_age >= 10:
                    tier1_recheck = self._detect_tier1_entry(
                        current_price, rsi_now, vol_ratio, momentum_60s,
                        bb_upper, bb_lower, ema_9, ema_21,
                        kc_upper, kc_lower,
                        [], [],  # empty T2 — only checking T1 presence
                        is_widened,
                    )
                    if tier1_recheck is None:
                        self.logger.info(
                            "[%s] T1_EXPIRED — tier1 signals faded after %.0fs, clearing pending %s",
                            self.pair, pt1_age, pt1_side,
                        )
                        self._pending_tier1 = None
                if self._pending_tier1 is not None:
                    # Still pending — enhanced logging
                    if self._tick_count % 6 == 0:
                        self.logger.info(
                            "[%s] T1_WAITING: %s age=%.0fs mom=%+.3f%% vel=%+.3f%% "
                            "need_confirm=%.2f%% or vel=%.2f%%",
                            self.pair, pt1_side, pt1_age,
                            momentum_60s, _t1_vel_5s,
                            self.CONFIRM_MOM_PCT, self.T1_ACCEL_CONFIRM_VEL,
                        )
                    self._skip_reason = f"T1_PENDING ({pt1_side}, {int(pt1_age)}s/{self.PHASE1_SECONDS}s)"
                    return signals  # don't scan for new entries while pending

        # ── Quality momentum detection (3-of-4 with setup tracking) ──
        if entry is None:
            entry = self._detect_quality_entry(
            current_price, rsi_now, vol_ratio,
            momentum_60s, momentum_30s, momentum_120s, momentum_300s,
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

            # ── RANGE GATE: price position in 30-min rolling range ─────
            range_pos = self._range_position
            if range_pos is not None:
                if range_pos <= self.RANGE_EXTREME_PCT:          # LOW ZONE
                    range_min_strength = 3 if side == "long" else 4
                elif range_pos >= (1.0 - self.RANGE_EXTREME_PCT):  # HIGH ZONE
                    range_min_strength = 3 if side == "short" else 4
                else:                                             # MID ZONE
                    range_min_strength = None  # no adjustment

                if range_min_strength and signal_strength < range_min_strength:
                    zone = "LOW" if range_pos <= self.RANGE_EXTREME_PCT else "HIGH"
                    self._skip_reason = (
                        f"RANGE_GATE ({side} in {zone}_ZONE needs "
                        f"{range_min_strength}/4, got {signal_strength}/4, pos={range_pos:.2f})"
                    )
                    self.logger.info(
                        "[%s] RANGE_GATE — %s in %s_ZONE needs %d/4, got %d/4 (pos=%.2f)",
                        self.pair, side.upper(), zone, range_min_strength,
                        signal_strength, range_pos,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

                zone = "LOW" if range_pos <= self.RANGE_EXTREME_PCT else (
                    "HIGH" if range_pos >= (1.0 - self.RANGE_EXTREME_PCT) else "MID")
                self.logger.info(
                    "[%s] RANGE_GATE: pos=%.2f (%s_ZONE) → %s %s",
                    self.pair, range_pos, zone, side,
                    "favored 3/4" if range_min_strength == 3 else "standard",
                )

            # ── SIDEWAYS REGIME GATE: require 4/4 + minimum momentum ──
            if self._market_regime == "SIDEWAYS" and (signal_strength < 4 or abs(momentum_60s) < self.SIDEWAYS_MOM_GATE):
                self._skip_reason = (
                    f"SIDEWAYS_GATE (need 4/4+{self.SIDEWAYS_MOM_GATE}% mom, "
                    f"got {signal_strength}/4 mom={abs(momentum_60s):.3f}%)"
                )
                self.logger.info(
                    "[%s] SIDEWAYS_GATE — need 4/4 + %.2f%% mom, got %d/4 + %.3f%%, skipping %s",
                    self.pair, self.SIDEWAYS_MOM_GATE, signal_strength, abs(momentum_60s), side,
                )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                    "skip_reason": self._skip_reason,
                    **self._last_signal_breakdown,
                }
                return signals

            # ── LOW-VOL RANGE GATE: ATR contracted = consolidation ────────
            _avg_atr = sum(self._atr_history) / len(self._atr_history) if self._atr_history else self._last_atr_pct
            if (
                _avg_atr > 0
                and self._last_atr_pct < _avg_atr * self.ATR_CONTRACTION_RATIO
                and len(self._atr_history) >= 5
            ):
                _abs_mom = abs(momentum_60s)
                if signal_strength < 4 or _abs_mom < self.RANGE_MOM_GATE:
                    self._skip_reason = (
                        f"LOW_VOL_RANGE (ATR={self._last_atr_pct:.3f}% avg={_avg_atr:.3f}%, "
                        f"need 4/4+{self.RANGE_MOM_GATE}% mom, got {signal_strength}/4 mom={_abs_mom:.3f}%)"
                    )
                    self.logger.info(
                        "[%s] LOW_VOL_RANGE — ATR=%.3f%% < %.0f%% of avg=%.3f%%, "
                        "need 4/4 + %.2f%% mom, got %d/4 + %.3f%% — skipping %s",
                        self.pair, self._last_atr_pct, self.ATR_CONTRACTION_RATIO * 100,
                        _avg_atr, self.RANGE_MOM_GATE, signal_strength, _abs_mom, side,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

            # ── 5-MINUTE COUNTER-TREND FILTER (secondary, when 15m=neutral) ──
            _ct_long_counter = side == "long" and self._price_5m_pct < -self.COUNTER_TREND_SOFT_PCT
            _ct_short_counter = side == "short" and self._price_5m_pct > self.COUNTER_TREND_SOFT_PCT
            if _ct_long_counter or _ct_short_counter:
                # HARD BLOCK: strong counter-trend
                _ct_hard = (
                    (side == "long" and self._price_5m_pct < -self.COUNTER_TREND_HARD_PCT)
                    or (side == "short" and self._price_5m_pct > self.COUNTER_TREND_HARD_PCT)
                )
                if _ct_hard:
                    self._skip_reason = f"5M_COUNTER_BLOCK ({side} vs 5m={self._price_5m_pct:+.2f}%)"
                    self.logger.info(
                        "[%s] 5M_COUNTER_BLOCK — %s entry blocked, 5m=%+.2f%% (threshold %.2f%%)",
                        self.pair, side, self._price_5m_pct, self.COUNTER_TREND_HARD_PCT,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals
                # SOFT FILTER: moderate counter-trend, require 4/4 signals
                if signal_strength < 4:
                    self._skip_reason = (
                        f"5M_COUNTER_SOFT ({side} 5m={self._price_5m_pct:+.2f}%, "
                        f"need 4/4 got {signal_strength}/4)"
                    )
                    self.logger.info(
                        "[%s] 5M_COUNTER_SOFT — %s needs 4/4, got %d/4 (5m=%+.2f%%)",
                        self.pair, side, signal_strength, self._price_5m_pct,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

            # ── Deferred SL cooldown check (4/4 signals bypass) ──
            if _sl_cooldown_active:
                if signal_strength >= 4:
                    self.logger.info(
                        "[%s] SL_COOLDOWN_BYPASS — 4/4 signal overrides cooldown (%.0fs remaining)",
                        self.pair, _sl_cooldown_remaining,
                    )
                else:
                    self._skip_reason = f"SL_COOLDOWN ({int(_sl_cooldown_remaining)}s)"
                    if self._tick_count % 12 == 0:
                        self.logger.info(
                            "[%s] SL COOLDOWN — %.0fs remaining before new entries",
                            self.pair, _sl_cooldown_remaining,
                        )
                    return signals

            # Stash entry momentum for declining-momentum filter (GPFC B)
            self._last_entry_momentum = max(abs(momentum_60s), abs(momentum_30s))

            # ── Write signal state immediately so options_scalp always sees it,
            #    even if scalp skips due to cooldown/sizing/disabled setup. ──
            self.last_signal_state = {
                "side": side,
                "reason": reason,
                "strength": signal_strength,
                "rsi": rsi_now,
                "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                "current_price": current_price,
                "timestamp": time.monotonic(),
                **self._last_signal_breakdown,
            }

            # ── DECELERATION FILTER (MOMENTUM PATH ONLY) ──────────────────
            # T1 and ACCEL entries have their own confirmation — they enter
            # BEFORE the move, so decel/sparse checks are wrong for them.
            if self._entry_path != "tier1":
                # Compare recent 10s momentum vs prior 10s (10-20s ago).
                # If the move is decelerating → the 60s momentum is stale, skip.
                # If accelerating → the move is gaining speed, safe to enter.
                self._last_momentum_10s = 0.0
                self._last_momentum_prior_10s = 0.0
                mom_recent = 0.0
                mom_prior = 0.0
                accel_check_valid = False

                prices = self._price_history
                recent_prices = [(ts, px) for ts, px in prices if now - self.DECEL_RECENT_WINDOW_S <= ts <= now]
                prior_prices = [(ts, px) for ts, px in prices if now - (self.DECEL_RECENT_WINDOW_S + self.DECEL_PRIOR_WINDOW_S) <= ts < now - self.DECEL_RECENT_WINDOW_S]

                if len(recent_prices) >= 2 and len(prior_prices) >= 2:
                    mom_recent = (recent_prices[-1][1] - recent_prices[0][1]) / recent_prices[0][1] * 100
                    mom_prior = (prior_prices[-1][1] - prior_prices[0][1]) / prior_prices[0][1] * 100
                    accel_check_valid = True
                    self._last_momentum_10s = mom_recent
                    self._last_momentum_prior_10s = mom_prior

                # Sparse tick guard: prior_10s == 0.000% means no price change in 10-20s window.
                # On Delta (~12 ticks/min), this is common. Without prior data we can't
                # verify acceleration — treat as unreliable and skip.
                if accel_check_valid and mom_prior == 0.0 and mom_recent != 0.0:
                    self._skip_reason = f"SPARSE_TICK_SKIP (10s={mom_recent:+.3f}% prior_10s=0.000%)"
                    self.logger.info(
                        "[%s] SPARSE_TICK — 10s=%+.3f%% but prior_10s=0.000%% — cannot verify acceleration, skipping %s",
                        self.pair, mom_recent, side,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

                # Floor check: need SOME momentum in entry direction (absolute minimum)
                floor_fail = False
                if side == "long" and mom_recent < self.DECEL_MOM_FLOOR_PCT:
                    floor_fail = True
                elif side == "short" and mom_recent > -self.DECEL_MOM_FLOOR_PCT:
                    floor_fail = True

                # Deceleration check: recent must be stronger than prior in entry direction
                decelerating = False
                if accel_check_valid and not floor_fail:
                    if side == "long":
                        decelerating = mom_recent <= mom_prior  # upward move slowing
                    else:
                        decelerating = mom_recent >= mom_prior  # downward move slowing

                if floor_fail or decelerating:
                    tag = "DECEL_SKIP" if decelerating else "MOM_FLOOR"
                    self._skip_reason = f"{tag} (10s={mom_recent:+.3f}% prior_10s={mom_prior:+.3f}%)"
                    self.logger.info(
                        "[%s] %s — 60s mom=%+.3f%% but 10s=%+.3f%% prior_10s=%+.3f%% — move %s, skipping %s",
                        self.pair, tag, momentum_60s, mom_recent, mom_prior,
                        "decelerating" if decelerating else "below floor", side,
                    )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

            # ── REVERSAL COOLDOWN: block ALL directions on pair after reversal exit ──
            rev_time = ScalpStrategy._pair_last_reversal_time.get(self._base_asset, 0.0)
            rev_remaining = rev_time + self.REVERSAL_COOLDOWN_SECONDS - now
            if rev_remaining > 0:
                rev_side = ScalpStrategy._pair_last_reversal_side.get(self._base_asset, "")
                self._skip_reason = f"REVERSAL_COOLDOWN ({int(rev_remaining)}s, was {rev_side})"
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "[%s] REVERSAL COOLDOWN — exited %s %ds ago, blocking ALL re-entry for %ds",
                        self.pair, rev_side,
                        int(self.REVERSAL_COOLDOWN_SECONDS - rev_remaining),
                        int(rev_remaining),
                    )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                    "skip_reason": self._skip_reason,
                    **self._last_signal_breakdown,
                }
                return signals

            # ── DECLINING MOMENTUM: skip if re-entering same pair+dir with weaker momentum (GPFC B) ──
            dir_key_chk = f"{self._base_asset}:{side}"
            last_mom = ScalpStrategy._pair_last_entry_momentum.get(dir_key_chk)
            last_exit_t = ScalpStrategy._pair_last_exit_time_mono.get(dir_key_chk, 0.0)
            if last_mom is not None and (now - last_exit_t) < 300:  # within 5 min
                current_mom = max(abs(momentum_60s), abs(momentum_30s))
                if current_mom < last_mom:
                    self._skip_reason = (
                        f"DECLINING_MOM ({side} {self._base_asset} — "
                        f"last entry MOM:{last_mom:.3f}% now MOM:{current_mom:.3f}%)"
                    )
                    if self._tick_count % 12 == 0:
                        self.logger.info(
                            "[%s] SKIP: declining momentum %s — last entry MOM:%.3f%% now MOM:%.3f%%",
                            self.pair, side, last_mom, current_mom,
                        )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

            # ── DEAD_MOMENTUM STREAK COOLDOWN: 3 consecutive DEAD exits → 10min pause (GPFC #6) ──
            dead_cd = ScalpStrategy._pair_dead_cooldown_until.get(dir_key_chk, 0.0)
            if dead_cd > now:
                remaining = int(dead_cd - now)
                self._skip_reason = f"DEAD_STREAK_CD ({dir_key_chk} — {remaining}s left)"
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "[%s] DEAD_STREAK COOLDOWN — %s blocked for %ds (3+ DEAD_MOMENTUM exits)",
                        self.pair, dir_key_chk, remaining,
                    )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                    "skip_reason": self._skip_reason,
                    **self._last_signal_breakdown,
                }
                return signals

            # ── PRICE LEVEL MEMORY: skip if price too close to last exit (GPFC C) ──
            last_exit_px = ScalpStrategy._pair_last_exit_price.get(self._base_asset)
            last_exit_any_t = ScalpStrategy._pair_last_exit_time_any.get(self._base_asset, 0.0)
            if last_exit_px is not None and last_exit_px > 0 and (now - last_exit_any_t) < 180:  # within 3 min
                price_diff_pct = abs(current_price - last_exit_px) / last_exit_px * 100
                if price_diff_pct < 0.10:
                    self._skip_reason = (
                        f"PRICE_LEVEL_MEMORY ({self._base_asset} — "
                        f"exit:${last_exit_px:.2f} now:${current_price:.2f} diff:{price_diff_pct:.3f}%)"
                    )
                    if self._tick_count % 12 == 0:
                        self.logger.info(
                            "[%s] SKIP: price too close to last exit — exit:$%.2f now:$%.2f diff:%.3f%%",
                            self.pair, last_exit_px, current_price, price_diff_pct,
                        )
                    self.last_signal_state = {
                        "side": side, "reason": reason,
                        "strength": signal_strength, "trend_15m": trend_15m,
                        "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                        "current_price": current_price, "timestamp": time.monotonic(),
                        "skip_reason": self._skip_reason,
                        **self._last_signal_breakdown,
                    }
                    return signals

            # ── PER-DIRECTION WIN RATE GATE: losing direction needs 4/4 ──
            dir_key = f"{self._base_asset}:{side}"
            dir_history = ScalpStrategy._pair_dir_trade_history.get(dir_key, [])
            dir_losing = False
            if len(dir_history) >= self.PERF_WINDOW:
                dir_wr = sum(dir_history) / len(dir_history)
                if dir_wr < self.DIR_LOW_WR_THRESHOLD:
                    dir_losing = True
                    if signal_strength < self.DIR_LOW_WR_MIN_STRENGTH:
                        self._skip_reason = (
                            f"DIR_LOW_WR ({side} WR={dir_wr:.0%} < {self.DIR_LOW_WR_THRESHOLD:.0%} "
                            f"over last {len(dir_history)}, needs {self.DIR_LOW_WR_MIN_STRENGTH}/4 got {signal_strength}/4)"
                        )
                        if self._tick_count % 12 == 0:
                            self.logger.info(
                                "[%s] DIR_LOW_WR — %s win rate %.0f%% < %.0f%% over last %d trades, "
                                "needs %d/4 but got %d/4, skipping",
                                self.pair, side, dir_wr * 100, self.DIR_LOW_WR_THRESHOLD * 100,
                                len(dir_history), self.DIR_LOW_WR_MIN_STRENGTH, signal_strength,
                            )
                        self.last_signal_state = {
                            "side": side, "reason": reason,
                            "strength": signal_strength, "trend_15m": trend_15m,
                            "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                            "current_price": current_price, "timestamp": time.monotonic(),
                            "skip_reason": self._skip_reason,
                            **self._last_signal_breakdown,
                        }
                        return signals

            # ── PER-PAIR STRENGTH GATE: 4/4 required, RSI override = 3/4 ──
            in_warmup = (time.monotonic() - self._strategy_start_time) < self.WARMUP_SECONDS
            is_rsi_override = "RSI-OVERRIDE" in reason
            if is_rsi_override:
                min_strength = 3  # RSI extreme is the ONLY exception to 4/4 gate
            elif in_warmup:
                min_strength = self.WARMUP_MIN_STRENGTH
            else:
                min_strength = self.PAIR_MIN_STRENGTH.get(self._base_asset, 4)
            if signal_strength < min_strength:
                self._skip_reason = f"STRENGTH_GATE ({signal_strength}/4 < {min_strength}/4)"
                if self._tick_count % 12 == 0:
                    warmup_tag = " (WARMUP)" if in_warmup else ""
                    self.logger.info(
                        "[%s] STRENGTH GATE%s — %s needs %d/4+ but got %d/4, skipping",
                        self.pair, warmup_tag, self._base_asset, min_strength, signal_strength,
                    )
                self.last_signal_state = {
                    "side": side, "reason": reason,
                    "strength": signal_strength, "trend_15m": trend_15m,
                    "rsi": rsi_now, "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                    "current_price": current_price, "timestamp": time.monotonic(),
                    "skip_reason": self._skip_reason,
                    **self._last_signal_breakdown,
                }
                return signals

            # ── Dynamic leverage based on conviction ──────────────────
            self._trade_leverage = self._calculate_leverage(
                signal_strength, momentum_60s, rsi_now,
            )
            reason += f" | LEV:{self._trade_leverage}x"

            # ── Refresh balance from exchange API (picks up new deposits) ─
            await self._refresh_balance_if_stale()
            available = self.risk_manager.get_available_capital(self._exchange_id)

            # ── Dynamic capital allocation based on signal strength ────
            amount = self._calculate_position_size_dynamic(
                current_price, available, signal_strength, total_scalp,
                momentum_60s=momentum_60s,
            )
            if amount is None:
                # _skip_reason already set inside sizing method (INSUFFICIENT_CAPITAL)
                if not self._skip_reason:
                    self._skip_reason = "POSITION_SIZE_ZERO"
                return signals

            # Share signal state with options strategy
            self._skip_reason = ""  # Clear — we're entering
            self.last_signal_state = {
                "side": side,
                "reason": reason,
                "strength": signal_strength,
                "trend_15m": trend_15m,
                "rsi": rsi_now,
                "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                "current_price": current_price,
                "timestamp": time.monotonic(),
                "skip_reason": "",
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
                "[%s] SIGNAL ENTRY — %s | strength=%d/4 | path=%s t1=%d t2=%d | Soul: %s",
                self.pair, reason, signal_strength,
                self._entry_path, self._tier1_count, self._tier2_count, soul_msg,
            )
            order_type = "limit" if use_limit else "market"
            entry_signal = self._build_entry_signal(side, current_price, amount, reason, order_type)
            if entry_signal is not None:
                signals.append(entry_signal)
        else:
            # If SL cooldown is active and no entry was found, show cooldown as skip reason
            if _sl_cooldown_active and not self._skip_reason:
                self._skip_reason = f"SL_COOLDOWN ({int(_sl_cooldown_remaining)}s)"
            # Update signal state even when no entry (options can see what's happening)
            self.last_signal_state = {
                "side": None,
                "reason": None,
                "strength": 0,
                "trend_15m": trend_15m,
                "rsi": rsi_now,
                "momentum_60s": momentum_60s, "momentum_30s": momentum_30s,
                "current_price": current_price,
                "timestamp": time.monotonic(),
                "skip_reason": self._skip_reason,
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

        When widened=True (idle 60+ min), thresholds loosen by 10%:
        - Momentum: 0.30% → 0.27%
        - Volume: 1.5x → 1.35x
        - RSI long: 40 → 41 (wider range triggers)
        - RSI short: 60 → 59 (wider range triggers)
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
        momentum_30s: float,
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
        self._entry_path = "momentum"  # default; overridden to "tier1" when pending confirms
        self._tier1_count = 0
        self._tier2_count = 0

        # ── Get effective thresholds (may be widened, but SAME for both dirs) ─
        eff_mom, eff_vol, eff_rsi_l, eff_rsi_s = self._effective_thresholds(widened)
        widen_tag = " WIDE" if widened else ""

        # Direction determined by stronger momentum window (GPFC A: dual 30s/60s)
        mom_for_dir = momentum_60s if abs(momentum_60s) >= abs(momentum_30s) else momentum_30s
        mom_direction = "long" if mom_for_dir > 0 else "short"

        # ── MOMENTUM STRENGTH TIERS (use stronger of 30s/60s) ──────────
        mom_abs = max(abs(momentum_60s), abs(momentum_30s))
        mom_30s_fire = abs(momentum_30s) >= self.MOMENTUM_30S_MIN_PCT
        mom_60s_fire = abs(momentum_60s) >= eff_mom
        below_gate = not mom_30s_fire and not mom_60s_fire

        if mom_abs >= 0.20:
            mom_strength = "STRONG"
        elif mom_abs >= 0.12:
            mom_strength = "MODERATE"
        else:
            mom_strength = "WEAK"

        # ── SIGNAL GATE: require 4/4 signals (quality over quantity) ──────
        # Base 4/4 for ALL entries. Trend-aligned can reduce to 3/4.
        # RSI override (RSI <30/>70) is the ONLY exception at 3/4.
        required_long = 4
        required_short = 4

        # Weak momentum (0.08-0.12%) = need more confirmation → 4/4
        if mom_strength == "WEAK":
            required_long = max(required_long, 4)
            required_short = max(required_short, 4)

        # High volatility = need full confirmation → 4/4 (no weak entries in chaos)
        if self._high_vol:
            required_long = max(required_long, 4)
            required_short = max(required_short, 4)

        # ── REGIME-BASED SIGNAL ADJUSTMENT ─────────────────────────────
        # Counter-trend trades need extra confirmation (4/4)
        if self._market_regime == "TRENDING_UP":
            required_short = max(required_short, 4)  # shorting against trend = 4/4
        elif self._market_regime == "TRENDING_DOWN":
            required_long = max(required_long, 4)  # longing against trend = 4/4

        # (15m trend gate removed — replaced by Range Gate in main entry path)

        # (15m trend-aligned reduction removed — Range Gate handles zone-based requirements)

        # WEAK momentum in SIDEWAYS already requires 4/4 signals (line above).
        # No hard skip — 4/4 is sufficient protection. Let strong setups through.

        # ── Post-streak gate: first trade after streak pause needs 3/4 ─────
        if ScalpStrategy._pair_post_streak.get(self._base_asset, False):
            required_long = max(required_long, self.POST_STREAK_STRENGTH)
            required_short = max(required_short, self.POST_STREAK_STRENGTH)

        self.logger.debug(
            "[%s] MOM %s: 30s=%+.3f%%/60s=%+.3f%% dir=%s req=L%d/S%d regime=%s gate=%s",
            self.pair, mom_strength, momentum_30s, momentum_60s, mom_direction,
            required_long, required_short, self._market_regime,
            "PASS" if not below_gate else "BLOCKED",
        )

        # ── Count bullish and bearish signals ──────────────────────────────
        bull_signals: list[str] = []
        bear_signals: list[str] = []

        # 1. Momentum — dual window: 30s OR 60s (GPFC A)
        if mom_30s_fire or mom_60s_fire:
            mom_tag = f"MOM:30s{momentum_30s:+.2f}%/60s{momentum_60s:+.2f}%"
            if momentum_60s >= eff_mom or momentum_30s >= self.MOMENTUM_30S_MIN_PCT:
                bull_signals.append(mom_tag)
            if momentum_60s <= -eff_mom or momentum_30s <= -self.MOMENTUM_30S_MIN_PCT:
                bear_signals.append(mom_tag)

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

        # 9. Liquidity Sweep — DISABLED (poor performance: 134-contract SWEEP lost $0.82)
        #    Was: Swing Failure Pattern + RSI divergence
        #    if df is not None and len(df) >= 12 ...
        if False:  # LIQSWEEP DISABLED — poor performance
            pass

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

        # ══════════════════════════════════════════════════════════════
        # GATE 0: NO MOMENTUM → check TIER 1 leading signals
        # Instead of entering immediately, store as pending and wait
        # for momentum confirmation on subsequent ticks (no order placed).
        # ══════════════════════════════════════════════════════════════
        if below_gate:
            self._last_signal_breakdown = _build_breakdown()
            # Try tier 1 anticipatory detection (leading signals, no momentum needed)
            tier1_result = self._detect_tier1_entry(
                price, rsi_now, vol_ratio, momentum_60s,
                bb_upper, bb_lower, ema_9, ema_21,
                kc_upper, kc_lower,
                bull_signals, bear_signals, widened,
            )
            if tier1_result is not None:
                t1_side, t1_reason, _, t1_strength = tier1_result
                # Store as PENDING — do NOT place any order yet
                self._pending_tier1 = {
                    "side": t1_side,
                    "reason": t1_reason,
                    "strength": t1_strength,
                    "tier1_count": self._tier1_count,
                    "tier2_count": self._tier2_count,
                    "timestamp": time.monotonic(),
                    "price": price,
                    "pair": self.pair,
                }
                self.logger.info(
                    "[%s] T1_PENDING — %s | strength=%d | t1=%d t2=%d | waiting for momentum confirm",
                    self.pair, t1_reason, t1_strength,
                    self._tier1_count, self._tier2_count,
                )
                self._skip_reason = f"T1_PENDING ({t1_side}, waiting for momentum)"
            else:
                self._skip_reason = f"NO_MOMENTUM ({abs(momentum_60s):.3f}% < {eff_mom:.3f}%)"
            return None

        # ── Layer 3 velocity check: skip if 5s velocity opposes momentum ──
        _mom_ticks = ScalpStrategy._tick_buffer.get(self._base_asset)
        _mom_vel_5s = 0.0
        if _mom_ticks and len(_mom_ticks) >= 2:
            _mom_now = time.monotonic()
            _mom_recent = [(t, p) for t, p in _mom_ticks if _mom_now - t <= 5.0]
            if len(_mom_recent) >= 2:
                _mom_vel_5s = (_mom_recent[-1][1] - _mom_recent[0][1]) / _mom_recent[0][1] * 100
        if _mom_vel_5s != 0:
            # If momentum says long but velocity is negative (or vice versa), move is reversing
            if (mom_direction == "long" and _mom_vel_5s < -0.005) or \
               (mom_direction == "short" and _mom_vel_5s > 0.005):
                self.logger.info(
                    "[%s] MOM_VELOCITY_CHECK: 60s=%+.3f%% but 5s_vel=%+.3f%% — move reversing, skip",
                    self.pair, momentum_60s, _mom_vel_5s,
                )
                self._last_signal_breakdown = _build_breakdown()
                self._skip_reason = f"MOM_REVERSING (60s={momentum_60s:+.3f}% vel={_mom_vel_5s:+.3f}%)"
                return None

        # (WEAK_COUNTER_TREND removed — Range Gate handles zone-based filtering)

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

        # (TREND_CONFLICT removed — Range Gate handles zone-based filtering)

        # ── Check required signals (LONG) ────────────────────────────────
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

        # ── Log direction-blocked entries ────────────────────────────────
        if len(bull_signals) >= required_long and mom_direction != "long":
            self.logger.info(
                "[%s] DIRECTION BLOCK: %d bull signals but mom is %+.3f%% (%s)",
                self.pair, len(bull_signals), momentum_60s, mom_direction,
            )
        if len(bear_signals) >= required_short and mom_direction != "short":
            self.logger.info(
                "[%s] DIRECTION BLOCK: %d bear signals but mom is %+.3f%% (%s)",
                self.pair, len(bear_signals), momentum_60s, mom_direction,
            )

        self._last_signal_breakdown = _build_breakdown()
        return None

    # ======================================================================
    # TIER 1 ANTICIPATORY ENTRY — leading signals, direction from order flow
    # ======================================================================

    def _detect_tier1_entry(
        self,
        price: float,
        rsi_now: float,
        vol_ratio: float,
        momentum_60s: float,
        bb_upper: float,
        bb_lower: float,
        ema_9: float,
        ema_21: float,
        kc_upper: float,
        kc_lower: float,
        bull_signals: list[str],
        bear_signals: list[str],
        widened: bool = False,
    ) -> tuple[str, str, bool, int] | None:
        """Detect anticipatory entry from TIER 1 leading signals.

        Called when GATE 0 blocks (no momentum) — looks for institutional
        loading patterns that precede the move.

        TIER 1 signals:
        1. Volume Anticipation: vol >= 1.5x with |mom| < 0.10% (loading before move)
        2. BB Squeeze: BB inside Keltner Channel (volatility compression)
        3. RSI Approach: 32-38 (approaching oversold) or 62-68 (approaching overbought)

        Direction from order flow (not past momentum):
        - Volume + lower BB → LONG, Volume + upper BB → SHORT
        - Squeeze + EMA9 > EMA21 → LONG, Squeeze + EMA9 < EMA21 → SHORT
        - RSI 32-38 → LONG, RSI 62-68 → SHORT

        Returns (side, reason, use_limit, signal_count) or None.
        """
        can_short = self.is_futures and config.delta.enable_shorting
        mom_abs = abs(momentum_60s)

        # BB position for direction inference
        bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1.0
        bb_position = (price - bb_lower) / bb_range  # 0.0 = lower, 1.0 = upper

        # Squeeze state (already tracked on self)
        bb_inside_kc = (
            kc_upper > 0 and kc_lower > 0
            and bb_upper < kc_upper and bb_lower > kc_lower
        )
        in_squeeze = bb_inside_kc or self._squeeze_tick_count > 0

        # ── Compute TIER 1 signals with direction ──────────────────────
        t1_long: list[str] = []
        t1_short: list[str] = []

        # T1-1: Volume Anticipation — high volume, no momentum yet
        if vol_ratio >= self.TIER1_VOL_RATIO and mom_abs < self.TIER1_VOL_MAX_MOM:
            if bb_position <= 0.30:
                t1_long.append(f"T1:VOL_ANTIC:{vol_ratio:.1f}x+BBlow")
            elif bb_position >= 0.70 and can_short:
                t1_short.append(f"T1:VOL_ANTIC:{vol_ratio:.1f}x+BBhigh")
            else:
                # Volume loading but BB mid — use EMA for direction
                if ema_9 > 0 and ema_21 > 0:
                    if ema_9 > ema_21:
                        t1_long.append(f"T1:VOL_ANTIC:{vol_ratio:.1f}x+EMA↑")
                    elif ema_9 < ema_21 and can_short:
                        t1_short.append(f"T1:VOL_ANTIC:{vol_ratio:.1f}x+EMA↓")

        # T1-2: BB Squeeze — volatility compression before breakout
        if in_squeeze:
            if ema_9 > 0 and ema_21 > 0:
                if ema_9 > ema_21:
                    t1_long.append(f"T1:BBSQZ+EMA↑")
                elif ema_9 < ema_21 and can_short:
                    t1_short.append(f"T1:BBSQZ+EMA↓")

        # T1-3: RSI Approach Zones — not yet extreme, approaching reversal
        rsi_l_low, rsi_l_high = self.TIER1_RSI_APPROACH_LONG
        rsi_s_low, rsi_s_high = self.TIER1_RSI_APPROACH_SHORT
        if rsi_l_low <= rsi_now <= rsi_l_high:
            t1_long.append(f"T1:RSI_APPROACH:{rsi_now:.0f}")
        if rsi_s_low <= rsi_now <= rsi_s_high and can_short:
            t1_short.append(f"T1:RSI_APPROACH:{rsi_now:.0f}")

        # ── Count existing tier 2 signals (from _detect_quality_entry) ──
        t2_long_count = len(bull_signals)
        t2_short_count = len(bear_signals)

        # ── Decide entry: 2+ T1, or 1 T1 + 2 T2 ──────────────────────
        long_ok = (
            len(t1_long) >= self.TIER1_MIN_SIGNALS
            or (len(t1_long) >= 1 and t2_long_count >= 2)
        )
        short_ok = (
            len(t1_short) >= self.TIER1_MIN_SIGNALS
            or (len(t1_short) >= 1 and t2_short_count >= 2)
        ) and can_short

        # If signals disagree on direction → skip (conflicting order flow)
        if long_ok and short_ok:
            self.logger.debug(
                "[%s] TIER1 CONFLICT: both long (%s) and short (%s) — skipping",
                self.pair, t1_long, t1_short,
            )
            return None

        if long_ok:
            t1_tags = " + ".join(t1_long)
            t2_tags = " + ".join(bull_signals) if bull_signals else ""
            reason = f"LONG TIER1 {len(t1_long)}T1+{t2_long_count}T2: {t1_tags}"
            if t2_tags:
                reason += f" [{t2_tags}]"
            total = len(t1_long) + t2_long_count
            self._tier1_count = len(t1_long)
            self._tier2_count = t2_long_count
            return ("long", reason, True, total)  # limit order for anticipatory

        if short_ok:
            t1_tags = " + ".join(t1_short)
            t2_tags = " + ".join(bear_signals) if bear_signals else ""
            reason = f"SHORT TIER1 {len(t1_short)}T1+{t2_short_count}T2: {t1_tags}"
            if t2_tags:
                reason += f" [{t2_tags}]"
            total = len(t1_short) + t2_short_count
            self._tier1_count = len(t1_short)
            self._tier2_count = t2_short_count
            return ("short", reason, True, total)

        return None

    def _calculate_leverage(self, signal_count: int, momentum_60s: float, rsi_now: float) -> int:
        """Dynamic leverage — capped at 20x for all entries.

        50x SL at -0.40% = -20% capital (coin flip to ruin).
        20x SL at -0.40% = -8% capital (survivable).
        """
        if not self.is_futures:
            return 1
        return min(self.leverage, 20)

    # ======================================================================
    # EXIT LOGIC — RIDE WINNERS, CUT LOSERS
    # ======================================================================

    def _update_ratchet_floor(self) -> float:
        """Update ratchet floor based on PEAK PnL — floor only moves UP, never down.

        Ratchet floor table maps peak PnL % → locked profit floor %.
        Once peak reaches a threshold, floor locks at corresponding level.
        If current PnL drops below floor → FLOOR_EXIT.
        """
        peak = self._peak_unrealized_pnl
        for min_peak, floor in reversed(self.RATCHET_FLOOR_TABLE):
            if peak >= min_peak and floor > self._profit_floor_pct:
                old_floor = self._profit_floor_pct
                self._profit_floor_pct = floor
                self.logger.info(
                    "[%s] RATCHET FLOOR — peak +%.2f%% >= +%.2f%%, "
                    "floor raised +%.2f%% → +%.2f%% (locks +%.0f%% capital at %dx)",
                    self.pair, peak, min_peak,
                    old_floor if old_floor > -999 else 0, floor,
                    floor * self._trade_leverage, self._trade_leverage,
                )
                break
        return self._profit_floor_pct

    def _update_trail_stop(self) -> None:
        """Update trailing stop price based on peak PnL tier table.

        Computes trail_stop from the PEAK price (not current), using the
        widest matching tier.  Trail stop only ever moves UP for longs
        (tightens) and DOWN for shorts.  Sets self._trailing_active when
        first tier is crossed.
        """
        if not self.is_futures:
            return  # spot uses pullback system, not trail tiers

        peak_pnl = self._peak_unrealized_pnl
        side = self.position_side or "long"

        # Find best matching tier (iterate reversed = highest first)
        new_dist: float | None = None
        for min_peak, trail_dist in reversed(self.TRAIL_TIER_TABLE):
            if peak_pnl >= min_peak:
                new_dist = trail_dist
                break

        if new_dist is None:
            # Log when peak crosses old 0.15% threshold but not new 0.25%
            if peak_pnl >= 0.15 and not self._trailing_active:
                if not getattr(self, '_trail_skip_logged', False):
                    self.logger.info(
                        "[%s] TRAIL_SKIP_LOW_PEAK — peak +%.2f%% (old threshold 0.15%% would activate, "
                        "new threshold 0.25%% not reached)",
                        self.pair, peak_pnl,
                    )
                    self._trail_skip_logged = True
            return  # peak hasn't reached first tier yet

        # ── Momentum-aware trail widening ──
        # When momentum is alive and aligned with position direction,
        # widen the trail to let winners run instead of getting stopped
        # out by normal volatility during a strong move.
        base_dist = new_dist
        mom = self._last_momentum_60s
        momentum_aligned = (
            (side == "long" and mom > self.TRAIL_MOM_ALIVE_PCT)
            or (side == "short" and mom < -self.TRAIL_MOM_ALIVE_PCT)
        )

        if momentum_aligned and peak_pnl > 0.20:
            new_dist = round(base_dist * self.TRAIL_MOMENTUM_WIDEN, 4)
            if not self._trail_widened:
                self._trail_widened = True
                self.logger.info(
                    "[%s] TRAIL WIDEN — momentum %.3f%% aligned with %s, "
                    "peak +%.2f%% > 0.20%%, trail %.2f%% → %.2f%% (×%.1f)",
                    self.pair, mom, side, peak_pnl,
                    base_dist, new_dist, self.TRAIL_MOMENTUM_WIDEN,
                )
        elif self._trail_widened:
            # Momentum faded or flipped — revert to base trail distance
            self._trail_widened = False
            self.logger.info(
                "[%s] TRAIL TIGHTEN — momentum %.3f%% faded/flipped, "
                "trail %.2f%% → %.2f%% (base)",
                self.pair, mom, base_dist * self.TRAIL_MOMENTUM_WIDEN, base_dist,
            )

        # Compute trail stop from peak PRICE (not entry)
        if side == "long":
            peak_price = self.highest_since_entry
            candidate = peak_price * (1 - new_dist / 100)
            # Trail stop only moves UP for longs
            if candidate > self._trail_stop_price:
                old_stop = self._trail_stop_price
                self._trail_stop_price = candidate
                self._trail_distance_pct = new_dist
                if not self._trailing_active:
                    self._trailing_active = True
                    self.logger.info(
                        "[%s] TRAIL ACTIVATED — peak +%.2f%% dist=%.2f%% "
                        "trail_stop=$%.2f (entry=$%.2f)",
                        self.pair, peak_pnl, new_dist,
                        candidate, self.entry_price,
                    )
                elif candidate - old_stop > 0.001:  # only log meaningful moves
                    self.logger.info(
                        "[%s] TRAIL SL MOVED: $%.2f → $%.2f "
                        "(peak=+%.2f%% trail_dist=%.2f%%)",
                        self.pair, old_stop, candidate,
                        peak_pnl, new_dist,
                    )
        else:  # short
            peak_price = self.lowest_since_entry
            candidate = peak_price * (1 + new_dist / 100)
            # Trail stop only moves DOWN for shorts
            if self._trail_stop_price == 0 or candidate < self._trail_stop_price:
                old_stop = self._trail_stop_price
                self._trail_stop_price = candidate
                self._trail_distance_pct = new_dist
                if not self._trailing_active:
                    self._trailing_active = True
                    self.logger.info(
                        "[%s] TRAIL ACTIVATED — peak +%.2f%% dist=%.2f%% "
                        "trail_stop=$%.2f (entry=$%.2f)",
                        self.pair, peak_pnl, new_dist,
                        candidate, self.entry_price,
                    )
                elif old_stop - candidate > 0.001:
                    self.logger.info(
                        "[%s] TRAIL SL MOVED: $%.2f → $%.2f "
                        "(peak=+%.2f%% trail_dist=%.2f%%)",
                        self.pair, old_stop, candidate,
                        peak_pnl, new_dist,
                    )

    def _check_exits(self, current_price: float, rsi_now: float, momentum_60s: float) -> list[Signal]:
        """MOMENTUM RIDING EXIT SYSTEM — ride momentum, exit on reversal.

        ALWAYS: Hard SL, Hard TP at 10%, ratchet floor.
        PHASE 1 (0-30s): Only hard exits. Let trade settle.
          Exception: if peak >= +0.5%, skip to Phase 2 immediately.
        PHASE 2+ (30s+): Two-mode system:
          MODE 1 — MOMENTUM RIDING: while momentum aligned, stay in. Wide ratchet floor.
          MODE 2 — SIGNAL REVERSAL: momentum flips, RSI extreme, or momentum dying → exit immediately.
        PHASE 3 (10-30 min): flatline/timeout only close losers.
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

        # Update trailing stop tiers (moves trail_stop_price up for longs)
        self._update_trail_stop()

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Hard SL + Trail Stop — fires in ALL phases
        # Trail stop overrides hard SL when tighter (only moves toward price)
        # ══════════════════════════════════════════════════════════════
        if side == "long":
            hard_sl = self.entry_price * (1 - self._sl_pct / 100)
            sl_price = max(hard_sl, self._trail_stop_price) if self._trail_stop_price > 0 else hard_sl
            if current_price <= sl_price:
                exit_label = "TRAIL" if self._trailing_active and sl_price > hard_sl else "SL"
                self.logger.info(
                    "[%s] %s HIT pnl=%+.2f%% (sl=$%.2f trail=$%.2f) — %ds in",
                    self.pair, exit_label, pnl_pct, sl_price,
                    self._trail_stop_price, int(hold_seconds),
                )
                return self._do_exit(current_price, pnl_pct, side, exit_label, hold_seconds)
        else:
            hard_sl = self.entry_price * (1 + self._sl_pct / 100)
            sl_price = min(hard_sl, self._trail_stop_price) if self._trail_stop_price > 0 else hard_sl
            if current_price >= sl_price:
                exit_label = "TRAIL" if self._trailing_active and sl_price < hard_sl else "SL"
                self.logger.info(
                    "[%s] %s HIT pnl=%+.2f%% (sl=$%.2f trail=$%.2f) — %ds in",
                    self.pair, exit_label, pnl_pct, sl_price,
                    self._trail_stop_price, int(hold_seconds),
                )
                return self._do_exit(current_price, pnl_pct, side, exit_label, hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Hard TP — 10% capital safety net
        # ══════════════════════════════════════════════════════════════
        capital_pnl = pnl_pct * self._trade_leverage
        if capital_pnl >= self.HARD_TP_CAPITAL_PCT:
            self.logger.info(
                "[%s] HARD TP HIT — capital +%.1f%% (price +%.2f%% × %dx) — %ds in",
                self.pair, capital_pnl, pnl_pct, self._trade_leverage, int(hold_seconds),
            )
            return self._do_exit(current_price, pnl_pct, side, "HARD_TP_10PCT", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # ALWAYS: Ratchet floor — based on PnL % (not capital %)
        # Floor only moves UP. If current PnL drops below floor → EXIT.
        # ══════════════════════════════════════════════════════════════
        self._update_ratchet_floor()
        if self._profit_floor_pct > -999 and pnl_pct < self._profit_floor_pct:
            self.logger.info(
                "FLOOR_EXIT: %s pnl=+%.2f%% hit floor +%.2f%% — locking profit",
                self.pair, pnl_pct, self._profit_floor_pct,
            )
            return self._do_exit(current_price, pnl_pct, side, "RATCHET", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # PHASE 1 (0-30s): HANDS OFF — only SL/TP/ratchet above
        # All entries (momentum AND tier1) arrive pre-confirmed.
        # Tier1 confirmation happens BEFORE order placement (pending check).
        # EXCEPTION: if peak PnL >= +0.5%, skip to Phase 2 immediately
        # ══════════════════════════════════════════════════════════════
        if hold_seconds < self.PHASE1_SECONDS:
            if self._peak_unrealized_pnl >= self.PHASE1_SKIP_AT_PEAK_PCT:
                self.logger.info(
                    "[%s] PHASE1 SKIP — peak +%.2f%% >= +%.1f%%, entering Phase 2 at %ds",
                    self.pair, self._peak_unrealized_pnl,
                    self.PHASE1_SKIP_AT_PEAK_PCT, int(hold_seconds),
                )
            else:
                if self._tick_count % 30 == 0:
                    self.logger.info(
                        "[%s] PHASE1 %ds/%ds | %s $%.2f | PnL=%+.2f%% | peak=%+.2f%% | path=%s",
                        self.pair, int(hold_seconds), self.PHASE1_SECONDS,
                        side, current_price, pnl_pct, self._peak_unrealized_pnl,
                        self._entry_path,
                    )
                return signals

        # ══════════════════════════════════════════════════════════════
        # PHASE 2+: MOMENTUM RIDING EXIT SYSTEM (futures)
        # ══════════════════════════════════════════════════════════════
        if self.is_futures:
            # ── Check momentum alignment ──────────────────────────────
            if side == "long":
                momentum_aligned = momentum_60s > 0
            else:
                momentum_aligned = momentum_60s < 0

            # ── MODE 1: RIDING — momentum still aligned, stay in ──────
            if momentum_aligned and pnl_pct > 0:
                # Log riding status periodically
                if self._tick_count % 12 == 0:
                    self.logger.info(
                        "RIDING: %s peak=+%.2f%% floor=+%.2f%% mom=%.3f%% — momentum aligned",
                        self.pair, self._peak_unrealized_pnl,
                        self._profit_floor_pct if self._profit_floor_pct > -999 else 0,
                        momentum_60s,
                    )
                # Breakeven safety: if peak was high but we're back near entry
                if self._peak_unrealized_pnl >= self.MOVE_SL_TO_ENTRY_PCT:
                    fee_adj = config.bybit.mixed_round_trip if self._exchange_id == "bybit" else config.delta.mixed_round_trip
                    if side == "long":
                        be_price = self.entry_price * (1 + fee_adj)
                        at_be = current_price <= be_price
                    else:
                        be_price = self.entry_price * (1 - fee_adj)
                        at_be = current_price >= be_price
                    if at_be:
                        return self._do_exit(current_price, pnl_pct, side, "BREAKEVEN", hold_seconds)
                return signals  # STAY IN — momentum still aligned

            # ── MODE 2: SIGNAL REVERSAL EXIT (last resort, not default) ─
            # Ratchet floor is the primary profit protector.
            # Reversal only fires on CONFIRMED momentum death.
            reversal_reason = ""

            # Check 1: momentum flipped sign — needs 15s confirmation
            mom_flipped = (side == "long" and momentum_60s < 0) or \
                          (side == "short" and momentum_60s > 0)
            if mom_flipped:
                if self._mom_flip_since == 0:
                    # First detection — start timer
                    self._mom_flip_since = time.monotonic()
                    self.logger.info(
                        "MOM_FLIP_START: %s mom=%.3f%% — confirming for %ds",
                        self.pair, momentum_60s, self.MOM_FLIP_CONFIRM_SECONDS,
                    )
                elif time.monotonic() - self._mom_flip_since >= self.MOM_FLIP_CONFIRM_SECONDS:
                    # Confirmed — momentum stayed flipped for 15s+
                    flip_dur = int(time.monotonic() - self._mom_flip_since)
                    reversal_reason = f"mom_flip_confirmed ({momentum_60s:+.3f}%, {flip_dur}s)"
            else:
                # Momentum re-aligned — reset timer and clear reversal log flag
                if self._mom_flip_since > 0:
                    self.logger.info(
                        "MOM_FLIP_RESET: %s mom=%.3f%% re-aligned after %.0fs — false alarm",
                        self.pair, momentum_60s, time.monotonic() - self._mom_flip_since,
                    )
                self._mom_flip_since = 0.0
                self._reversal_exit_logged = False  # allow fresh log if it flips again

            # Check 2: momentum dying (below 0.02% absolute — truly dead)
            if not reversal_reason and abs(momentum_60s) < self.MOMENTUM_DYING_PCT:
                # MOMENTUM_FADE: dying + in profit → confirm via timer, then exit.
                # Trail handles winners — MOMENTUM_FADE only fires on un-trailed positions.
                # Trend-aligned trades get extra patience (pauses are legs, not death).
                if (self._trailing_active or self._trail_stop_price > 0) and pnl_pct > 0.05:
                    pass  # trail is managing this trade — don't interfere
                elif pnl_pct > 0.05:
                    trend_15m = self._get_15m_trend()
                    trend_aligned = (
                        (side == "long" and trend_15m == "bullish")
                        or (side == "short" and trend_15m == "bearish")
                    )
                    min_hold = self.MOM_FADE_TREND_HOLD if trend_aligned else self.MOM_FADE_MIN_HOLD
                    confirm_req = self.MOM_FADE_TREND_CONFIRM if trend_aligned else self.MOM_FADE_CONFIRM_SECONDS

                    if hold_seconds >= min_hold:
                        now_m = time.monotonic()
                        if self._mom_fade_since == 0:
                            self._mom_fade_since = now_m
                            self.logger.info(
                                "MOM_FADE_START: %s pnl=%+.2f%% mom=%.3f%% hold=%ds "
                                "trend=%s — confirming for %ds",
                                self.pair, pnl_pct, abs(momentum_60s),
                                int(hold_seconds), trend_15m, confirm_req,
                            )
                        elif now_m - self._mom_fade_since >= confirm_req:
                            confirm_dur = int(now_m - self._mom_fade_since)
                            self.logger.info(
                                "MOMENTUM_FADE: %s pnl=%+.2f%% hold=%ds "
                                "confirmed=%ds trend=%s",
                                self.pair, pnl_pct, int(hold_seconds),
                                confirm_dur, trend_15m,
                            )
                            return self._do_exit(current_price, pnl_pct, side, "MOMENTUM_FADE", hold_seconds)

                # DEAD_MOMENTUM path: requires MOM_DYING_CONFIRM_SECONDS of sustained dead momentum
                if self._mom_dying_since == 0:
                    self._mom_dying_since = time.monotonic()
                    self.logger.info(
                        "MOM_DYING_START: %s abs_mom=%.3f%% — confirming for %ds",
                        self.pair, abs(momentum_60s), self.MOM_DYING_CONFIRM_SECONDS,
                    )
                elif time.monotonic() - self._mom_dying_since >= self.MOM_DYING_CONFIRM_SECONDS:
                    dying_dur = int(time.monotonic() - self._mom_dying_since)
                    reversal_reason = f"mom_dying_confirmed ({abs(momentum_60s):.3f}%, {dying_dur}s)"
            else:
                # Momentum recovered above threshold — reset ALL dying/fade timers
                if self._mom_dying_since > 0:
                    self.logger.info(
                        "MOM_DYING_RESET: %s abs_mom=%.3f%% recovered after %.0fs — false alarm",
                        self.pair, abs(momentum_60s), time.monotonic() - self._mom_dying_since,
                    )
                if self._mom_fade_since > 0:
                    self.logger.info(
                        "MOM_FADE_RESET: %s abs_mom=%.3f%% recovered after %.0fs — holding",
                        self.pair, abs(momentum_60s), time.monotonic() - self._mom_fade_since,
                    )
                self._mom_dying_since = 0.0
                self._mom_fade_since = 0.0

            # RSI cross REMOVED as reversal trigger for futures.
            # RSI crossing 70 in a long is trend strength, not reversal.
            # Ratchet floor handles profit protection.

            # DEAD_MOMENTUM: confirmed dead momentum + losing + held >3min → cut losses
            # Don't wait for SL when the setup has clearly failed.
            # Skip if: profitable, peak was significant (>0.15%), or trail is active.
            _dm_trail_active = self._trailing_active or self._trail_stop_price > 0
            _dm_peak_significant = self._peak_unrealized_pnl >= 0.15
            if (
                reversal_reason
                and pnl_pct <= 0
                and not _dm_peak_significant
                and not _dm_trail_active
                and hold_seconds > self.DEAD_MOM_MIN_HOLD
            ):
                if not self._reversal_exit_logged:
                    self._reversal_exit_logged = True
                    self.logger.info(
                        "DEAD_MOMENTUM: %s pnl=%+.2f%% peak=%.2f%% hold=%ds mom=%.3f%% — "
                        "cutting loss, setup failed (%s)",
                        self.pair, pnl_pct, self._peak_unrealized_pnl,
                        int(hold_seconds), momentum_60s, reversal_reason,
                    )
                return self._do_exit(current_price, pnl_pct, side, "DEAD_MOMENTUM", hold_seconds)
            elif reversal_reason and (_dm_peak_significant or _dm_trail_active) and pnl_pct <= 0:
                if not self._reversal_exit_logged:
                    self.logger.debug(
                        "DEAD_MOM_SKIP: %s pnl=%+.2f%% peak=%.2f%% trail=%s — "
                        "letting trail/ratchet handle exit",
                        self.pair, pnl_pct, self._peak_unrealized_pnl,
                        _dm_trail_active,
                    )

            # If confirmed reversal AND in profit → exit
            if reversal_reason and pnl_pct >= self.REVERSAL_MIN_PROFIT_PCT:
                if not self._reversal_exit_logged:
                    self._reversal_exit_logged = True
                    self.logger.info(
                        "REVERSAL_DETECTED: %s peak=+%.2f%% pnl=%+.2f%% mom=%.3f%% — "
                        "attempting exit (%s)",
                        self.pair, self._peak_unrealized_pnl, pnl_pct,
                        momentum_60s, reversal_reason,
                    )
                return self._do_exit(current_price, pnl_pct, side, "REVERSAL", hold_seconds)

            # If reversal signal but NOT in profit — check breakeven
            if reversal_reason and self._peak_unrealized_pnl >= self.MOVE_SL_TO_ENTRY_PCT:
                fee_adj = config.delta.mixed_round_trip
                if side == "long":
                    be_price = self.entry_price * (1 + fee_adj)
                    at_be = current_price <= be_price
                else:
                    be_price = self.entry_price * (1 - fee_adj)
                    at_be = current_price >= be_price
                if at_be:
                    return self._do_exit(current_price, pnl_pct, side, "BREAKEVEN", hold_seconds)

            # Log reversal pending once (suppresses spam while waiting)
            if reversal_reason and not self._reversal_exit_logged:
                self._reversal_exit_logged = True
                self.logger.info(
                    "REVERSAL_PENDING: %s pnl=%+.2f%% (need +%.2f%% for exit) — %s",
                    self.pair, pnl_pct, self.REVERSAL_MIN_PROFIT_PCT, reversal_reason,
                )

        else:
            # ── SPOT PROFIT PROTECTION (unchanged) ────────────────────
            if pnl_pct > 0:
                peak = self._peak_unrealized_pnl
                if peak >= self.SPOT_PULLBACK_MIN_PEAK_PCT:
                    if pnl_pct < peak * self.SPOT_PULLBACK_RATIO:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_PULLBACK", hold_seconds)
                if peak >= self.SPOT_DECAY_MIN_PEAK_PCT:
                    if pnl_pct < self.SPOT_DECAY_EXIT_BELOW_PCT:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_DECAY", hold_seconds)
                if peak >= self.SPOT_BREAKEVEN_MIN_PEAK_PCT:
                    if pnl_pct <= self.SPOT_BREAKEVEN_EXIT_BELOW_PCT:
                        return self._do_exit(current_price, pnl_pct, side, "SPOT_BREAKEVEN", hold_seconds)

        # ══════════════════════════════════════════════════════════════
        # PHASE 3 (10-30 min): FLATLINE / TIMEOUT — only close losers
        # ══════════════════════════════════════════════════════════════
        if hold_seconds >= self.PHASE2_SECONDS:
            # Flatline — 10 min with no movement — ONLY close losers
            if (hold_seconds >= self.FLATLINE_SECONDS
                    and abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT and pnl_pct <= 0):
                return self._do_exit(current_price, pnl_pct, side, "FLAT", hold_seconds)

            # Hard timeout — ONLY close losers
            if hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct <= 0:
                return self._do_exit(current_price, pnl_pct, side, "TIMEOUT", hold_seconds)

            # Safety: past timeout AND losing
            if hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
                return self._do_exit(current_price, pnl_pct, side, "SAFETY", hold_seconds)

        return signals

    def check_exits_immediate(self, current_price: float) -> None:
        """WS tick handler — acceleration entry (Layer 1) + exit checks.

        Called by WebSocket PriceFeed on every tick. If not in position,
        collects ticks and checks for price acceleration entry. If in position,
        runs SL, Hard TP, ratchet floor, breakeven, flatline/timeout.
        """
        # ── Always collect ticks into shared buffer ──────────────────
        key = self._base_asset
        now = time.monotonic()
        if key not in ScalpStrategy._tick_buffer:
            ScalpStrategy._tick_buffer[key] = deque(maxlen=120)
        ScalpStrategy._tick_buffer[key].append((now, current_price))

        if not self.in_position or not self.position_side:
            # ── LAYER 1: Acceleration entry check ────────────────────
            self._check_acceleration_entry(key, now, current_price)
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

        # Update trailing stop tiers on every WS tick
        self._update_trail_stop()

        # Update live P&L for class-level tracker
        ScalpStrategy._live_pnl[self.pair] = pnl_pct

        exit_type: str | None = None

        # ── ALWAYS: Hard SL + Trail Stop ──────────────────────────────
        if side == "long":
            hard_sl = self.entry_price * (1 - self._sl_pct / 100)
            sl_price = max(hard_sl, self._trail_stop_price) if self._trail_stop_price > 0 else hard_sl
            if current_price <= sl_price:
                exit_type = "TRAIL" if self._trailing_active and sl_price > hard_sl else "SL"
        elif side == "short":
            hard_sl = self.entry_price * (1 + self._sl_pct / 100)
            sl_price = min(hard_sl, self._trail_stop_price) if self._trail_stop_price > 0 else hard_sl
            if current_price >= sl_price:
                exit_type = "TRAIL" if self._trailing_active and sl_price < hard_sl else "SL"

        # ── ALWAYS: Ratchet floor (PnL-based, not capital-based) ─────
        if not exit_type:
            self._update_ratchet_floor()
            if self._profit_floor_pct > -999 and pnl_pct < self._profit_floor_pct:
                exit_type = "RATCHET"

        # ── ALWAYS: Hard TP ──────────────────────────────────────────
        capital_pnl = pnl_pct * self._trade_leverage
        if not exit_type and capital_pnl >= self.HARD_TP_CAPITAL_PCT:
            exit_type = "HARD_TP_10PCT"

        # Periodic logging (every 10s) for visibility
        now_mono = time.monotonic()
        if now_mono - self._last_ws_sl_log >= 10:
            self._last_ws_sl_log = now_mono
            floor_info = f" Floor=+{self._profit_floor_pct:.2f}%" if self._profit_floor_pct > -999 else ""
            trail_info = f" Trail=$%.2f(%.2f%%)" % (self._trail_stop_price, self._trail_distance_pct) if self._trailing_active else ""
            self.logger.info(
                "[%s] WS TICK: %s @ $%.2f PnL=%+.2f%% (%+.1f%%cap) peak=%+.2f%% "
                "SL=$%.2f(%.2f%%) hold=%ds%s%s%s",
                self.pair, side, current_price, pnl_pct, capital_pnl,
                self._peak_unrealized_pnl, sl_price, self._sl_pct,
                int(hold_seconds),
                floor_info, trail_info,
                " -> " + exit_type + "!" if exit_type else "",
            )

        # ── PHASE 1: only hard exits fire ────────────────────────────
        _in_phase2_plus = hold_seconds >= self.PHASE1_SECONDS
        if not exit_type and not _in_phase2_plus:
            if self._peak_unrealized_pnl >= self.PHASE1_SKIP_AT_PEAK_PCT:
                _in_phase2_plus = True
            else:
                return  # hands off

        # ── PHASE 2+: breakeven (peaked high but returned to entry) ──
        if not exit_type and _in_phase2_plus:
            if self._peak_unrealized_pnl >= self.MOVE_SL_TO_ENTRY_PCT:
                fee_adj = config.delta.mixed_round_trip
                if side == "long":
                    be_price = self.entry_price * (1 + fee_adj)
                    at_be = current_price <= be_price
                else:
                    be_price = self.entry_price * (1 - fee_adj)
                    at_be = current_price >= be_price
                if at_be:
                    exit_type = "BREAKEVEN"

        # ── PHASE 2+: SPOT PROFIT PROTECTION (spot only) ────────────
        if not exit_type and _in_phase2_plus and not self.is_futures and pnl_pct > 0:
            peak = self._peak_unrealized_pnl
            if peak >= self.SPOT_PULLBACK_MIN_PEAK_PCT:
                if pnl_pct < peak * self.SPOT_PULLBACK_RATIO:
                    exit_type = "SPOT_PULLBACK"
            if not exit_type and peak >= self.SPOT_DECAY_MIN_PEAK_PCT:
                if pnl_pct < self.SPOT_DECAY_EXIT_BELOW_PCT:
                    exit_type = "SPOT_DECAY"
            if not exit_type and peak >= self.SPOT_BREAKEVEN_MIN_PEAK_PCT:
                if pnl_pct <= self.SPOT_BREAKEVEN_EXIT_BELOW_PCT:
                    exit_type = "SPOT_BREAKEVEN"

        # ── PHASE 3: flatline + timeout — ONLY close losers ──────────
        if not exit_type and hold_seconds >= self.FLATLINE_SECONDS:
            if abs(pnl_pct) < self.FLATLINE_MIN_MOVE_PCT and pnl_pct <= 0:
                exit_type = "FLAT"
        if not exit_type and hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct <= 0:
            exit_type = "TIMEOUT"
        if not exit_type and hold_seconds >= self.MAX_HOLD_SECONDS and pnl_pct < 0:
            exit_type = "SAFETY"

        # ── EXECUTE EXIT ─────────────────────────────────────────────
        if exit_type:
            self.logger.info(
                "[%s] WS EXIT %s — %s @ $%.2f PnL=%+.2f%% (%+.1f%% capital at %dx) hold=%ds",
                self.pair, exit_type, side, current_price,
                pnl_pct, pnl_pct * self._trade_leverage, self._trade_leverage,
                int(hold_seconds),
            )
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

    # ── LAYER 1: WS Acceleration entry ──────────────────────────────────

    def _check_acceleration_entry(self, key: str, now: float, price: float) -> None:
        """Detect price acceleration from WS ticks and fire entry if confirmed."""
        # ── Exchange-aware tick-rate tuning ─────────────────────────
        if self._exchange_id == "delta":
            min_ticks = self.ACCEL_MIN_TICKS_SLOW
            vel_window = self.ACCEL_VELOCITY_WINDOW_SLOW
            min_velocity = self.ACCEL_MIN_VELOCITY_SLOW
        else:
            min_ticks = self.ACCEL_MIN_TICKS_FAST
            vel_window = self.ACCEL_VELOCITY_WINDOW_FAST
            min_velocity = self.ACCEL_MIN_VELOCITY

        ticks = ScalpStrategy._tick_buffer.get(key)
        min_buffer = min_ticks + min(min_ticks, 2)   # fast=5, slow=2
        if not ticks or len(ticks) < min_buffer:
            return

        # ── Cooldown check ───────────────────────────────────────────
        if now - ScalpStrategy._last_accel_entry_time.get(key, 0) <= self.ACCEL_COOLDOWN:
            return

        # ── Basic guards: already in position, paused, regime ────────
        if self.in_position:
            return
        if self.risk_manager.is_paused:
            return
        if self._market_regime == "CHOPPY":
            return

        # ── Tick density check: pair must have real activity ──────────
        # Fast exchanges (Kraken/Bybit): 8+ ticks in 2*window proves liquidity.
        # Slow exchanges (Delta ~1 tick/5s): 2+ ticks in 2*window is sufficient.
        broad_window = vel_window * 2
        ticks_broad = [(t, p) for t, p in ticks if now - t <= broad_window]
        density_min = 8 if min_ticks >= 3 else 2
        if len(ticks_broad) < density_min:
            return

        # ── Compute velocity windows ─────────────────────────────────
        recent = [(t, p) for t, p in ticks_broad if now - t <= vel_window]
        prior = [(t, p) for t, p in ticks_broad if now - t > vel_window]

        prior_min = min(min_ticks, 2)                 # fast=2, slow=1
        if len(recent) < min_ticks or len(prior) < prior_min:
            return

        velocity_current = (recent[-1][1] - recent[0][1]) / recent[0][1] * 100
        velocity_prev = (prior[-1][1] - prior[0][1]) / prior[0][1] * 100
        acceleration = abs(velocity_current) - abs(velocity_prev)

        if abs(velocity_current) < min_velocity:
            return
        if acceleration < self.ACCEL_MIN_POSITIVE:
            return
        if len(recent) < min_ticks:
            return

        direction = "long" if velocity_current > 0 else "short"

        # ── Shorting guard ───────────────────────────────────────────
        if direction == "short" and not self.is_futures:
            return

        # ── Check cached candle signals (from last 5s scan cycle) ────
        cached = ScalpStrategy._cached_signals.get(key, {})
        cache_age = now - cached.get("timestamp", 0) if cached else 999
        support_count = 0
        if cached and cache_age < 10:
            if direction == "long":
                if cached.get("rsi", 50) < 45:
                    support_count += 1
                if cached.get("bb_position", 0.5) < 0.30:
                    support_count += 1
                if cached.get("vwap_above", False):
                    support_count += 1
                if cached.get("ema9_above_21", False):
                    support_count += 1
            else:
                if cached.get("rsi", 50) > 55:
                    support_count += 1
                if cached.get("bb_position", 0.5) > 0.70:
                    support_count += 1
                if not cached.get("vwap_above", True):
                    support_count += 1
                if not cached.get("ema9_above_21", True):
                    support_count += 1

        if support_count < self.ACCEL_MIN_SUPPORT:
            return

        # ── Require at least one of RSI or BB confirming (not just VWAP/EMA) ──
        has_rsi_or_bb = False
        if cached and cache_age < 10:
            if direction == "long":
                has_rsi_or_bb = cached.get("rsi", 50) < 45 or cached.get("bb_position", 0.5) < 0.30
            else:
                has_rsi_or_bb = cached.get("rsi", 50) > 55 or cached.get("bb_position", 0.5) > 0.70
        if not has_rsi_or_bb:
            return

        # ── RANGE GATE (from cached candle data) ──
        _accel_range_pos = cached.get("range_position") if cached and cache_age < 10 else None
        if _accel_range_pos is not None:
            if _accel_range_pos <= self.RANGE_EXTREME_PCT:          # LOW ZONE
                _range_min = 3 if direction == "long" else 4
            elif _accel_range_pos >= (1.0 - self.RANGE_EXTREME_PCT):  # HIGH ZONE
                _range_min = 3 if direction == "short" else 4
            else:
                _range_min = None
            if _range_min and support_count < _range_min:
                _zone = "LOW" if _accel_range_pos <= self.RANGE_EXTREME_PCT else "HIGH"
                self.logger.info(
                    "[%s] ACCEL RANGE_GATE — %s in %s_ZONE needs %d/4, got %d/4 (pos=%.2f)",
                    self.pair, direction.upper(), _zone, _range_min,
                    support_count, _accel_range_pos,
                )
                return

        # ── 5-minute counter-trend filter (from cached candle data) ──
        _accel_5m = cached.get("price_5m_pct", 0.0) if cached and cache_age < 10 else 0.0
        if _accel_5m != 0.0:
            _accel_hard = (
                (direction == "long" and _accel_5m < -self.COUNTER_TREND_HARD_PCT)
                or (direction == "short" and _accel_5m > self.COUNTER_TREND_HARD_PCT)
            )
            if _accel_hard:
                self.logger.info(
                    "[%s] ACCEL 5M_COUNTER_BLOCK — %s blocked, 5m=%+.2f%%",
                    self.pair, direction, _accel_5m,
                )
                return
            _accel_soft = (
                (direction == "long" and _accel_5m < -self.COUNTER_TREND_SOFT_PCT)
                or (direction == "short" and _accel_5m > self.COUNTER_TREND_SOFT_PCT)
            )
            if _accel_soft and support_count < 4:
                self.logger.info(
                    "[%s] ACCEL 5M_COUNTER_SOFT — %s needs 4/4 support, got %d/4 (5m=%+.2f%%)",
                    self.pair, direction, support_count, _accel_5m,
                )
                return

        # ── SIDEWAYS regime gate (from cached candle data) ──
        _accel_regime = cached.get("market_regime", "SIDEWAYS") if cached and cache_age < 10 else "SIDEWAYS"
        if _accel_regime == "SIDEWAYS" and (support_count < 4 or abs(velocity_current) < 0.06):
            self.logger.info(
                "[%s] ACCEL SIDEWAYS_GATE — need 4/4 + vel>=0.06%%, got %d/4 + vel=%.3f%%",
                self.pair, support_count, abs(velocity_current),
            )
            return

        # ── LOW-VOL range gate (from cached ATR data) ──
        _accel_atr = cached.get("atr_pct", 0.0) if cached and cache_age < 10 else 0.0
        _accel_atr_avg = cached.get("atr_avg", 0.0) if cached and cache_age < 10 else 0.0
        if _accel_atr_avg > 0 and _accel_atr < _accel_atr_avg * self.ATR_CONTRACTION_RATIO:
            _accel_abs_vel = abs(velocity_current)
            if support_count < 4 or _accel_abs_vel < self.RANGE_MOM_GATE:
                self.logger.info(
                    "[%s] ACCEL LOW_VOL_RANGE — ATR=%.3f%% < %.0f%% of avg=%.3f%%, "
                    "need 4/4 + vel>=%.2f%%, got %d/4 + vel=%.3f%% — skipping",
                    self.pair, _accel_atr, self.ATR_CONTRACTION_RATIO * 100,
                    _accel_atr_avg, self.RANGE_MOM_GATE, support_count, _accel_abs_vel,
                )
                return

        # ── FIRE: acceleration confirmed with signal support ─────────
        self.logger.info(
            "[%s] ACCEL ENTRY: %s vel=%+.3f%%/%.0fs accel=%+.3f ticks=%d support=%d/4",
            self.pair, direction, velocity_current, vel_window, acceleration,
            len(recent), support_count,
        )
        try:
            asyncio.get_running_loop().create_task(
                self._execute_accel_entry(
                    direction, velocity_current, acceleration, support_count, price,
                    vel_window,
                )
            )
        except RuntimeError:
            pass  # no running event loop (shouldn't happen in normal operation)

    async def _execute_accel_entry(
        self,
        direction: str,
        velocity: float,
        acceleration: float,
        support_count: int,
        price: float,
        vel_window: float = 5.0,
    ) -> None:
        """Execute a Layer 1 acceleration entry (async, called via create_task)."""
        try:
            # Double-check not already in position (race guard)
            if self.in_position:
                return

            self._entry_path = "acceleration"
            self._tier1_count = 0
            self._tier2_count = 0

            # Build reason string
            reason = (
                f"{direction.upper()} ACCEL: vel={velocity:+.3f}%/{vel_window:.0f}s "
                f"accel={acceleration:+.3f} support={support_count}/4"
            )

            # Dynamic leverage (use support_count as signal strength proxy)
            signal_strength = max(support_count, 3)  # treat as 3/4 minimum
            self._trade_leverage = self._calculate_leverage(signal_strength, velocity, 50.0)
            reason += f" | LEV:{self._trade_leverage}x"

            # Refresh balance + sizing
            await self._refresh_balance_if_stale()
            available = self.risk_manager.get_available_capital(self._exchange_id)
            total_scalp = len(self.risk_manager.open_positions)

            # ── Pre-flight checks ────────────────────────────────────────
            exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
            self.logger.info(
                "[%s] ACCEL_SIZE: avail=$%.2f exch_cap=$%.2f exch=%s strength=%d "
                "price=$%.2f open=%d atr=%.4f%% sl=%.2f%%",
                self.pair, available, exchange_capital, self._exchange_id,
                signal_strength, price, total_scalp,
                self._last_atr_pct, self._sl_pct,
            )

            if not available or available <= 0:
                self.logger.warning(
                    "[%s] ACCEL skip — no capital on %s ($%.2f)",
                    self.pair, self._exchange_id, available or 0,
                )
                return

            # BTC/ETH need significant capital for 1 contract at leverage
            if self._base_asset in ("BTC", "ETH") and exchange_capital < 50:
                self.logger.info(
                    "[%s] ACCEL skip — %s needs $50+ for 1 contract (have $%.2f)",
                    self.pair, self._base_asset, exchange_capital,
                )
                return

            # Ensure SL is set (ATR may not be computed yet before first candle scan)
            if self._sl_pct <= 0 or self._last_atr_pct <= 0:
                fallback_sl = self.PAIR_SL_FLOOR.get(self._base_asset, 0.35)
                fallback_tp = self.PAIR_TP_FLOOR.get(self._base_asset, 1.50)
                self.logger.info(
                    "[%s] ACCEL using fallback SL/TP (no ATR yet): SL=%.2f%% TP=%.2f%%",
                    self.pair, fallback_sl, fallback_tp,
                )
                self._sl_pct = fallback_sl
                self._tp_pct = fallback_tp

            amount = self._calculate_position_size_dynamic(
                price, available, signal_strength, total_scalp,
                momentum_60s=velocity,
            )
            if amount is None:
                self.logger.warning(
                    "[%s] ACCEL skip — sizing=None (exch_cap=$%.2f avail=$%.2f "
                    "price=$%.2f lev=%dx sl=%.2f%%)",
                    self.pair, exchange_capital, available,
                    price, self._trade_leverage, self._sl_pct,
                )
                return

            # Build and execute signal (market order for speed)
            entry_signal = self._build_entry_signal(direction, price, amount, reason, "market")
            if entry_signal is None:
                return

            if self.risk_manager.approve_signal(entry_signal):
                order = await self.executor.execute(entry_signal)
                if order is not None:
                    self.risk_manager.record_open(entry_signal)
                    self.on_fill(entry_signal, order)
                    ScalpStrategy._last_accel_entry_time[self._base_asset] = time.monotonic()
                    self.logger.info(
                        "[%s] ACCEL FILLED — %s @ $%.2f, %dx | vel=%+.3f%%/5s",
                        self.pair, direction.upper(), price,
                        self._trade_leverage, velocity,
                    )
                else:
                    self.logger.warning("[%s] ACCEL entry order failed/skipped", self.pair)
            else:
                self.logger.info("[%s] ACCEL entry rejected by risk manager", self.pair)
        except Exception:
            self.logger.exception("[%s] ACCEL entry execution error", self.pair)

    def _do_exit(
        self, price: float, pnl_pct: float, side: str,
        exit_type: str, hold_seconds: float,
    ) -> list[Signal]:
        """Execute an exit: build signal, record result, log.

        Fee-aware: skips tiny exits (gross < $0.10) unless it's an SL,
        ratchet floor, hard TP, or safety exit.
        """
        # Fee-aware minimum — skip tiny exits that'd be eaten by fees
        # Fee minimum only applies to discretionary exits, NOT protective exits.
        # TRAIL/BREAKEVEN/RATCHET protect profit — blocking them turns winners into losers.
        _PROTECTED_EXIT_TYPES = {"SL", "TRAIL", "BREAKEVEN", "PROFIT_LOCK",
                                 "HARD_TP", "HARD_TP_10PCT", "RATCHET", "SAFETY",
                                 "DEAD_MOMENTUM", "MOMENTUM_FADE"}
        clean_type = exit_type.replace("WS-", "")
        if clean_type not in _PROTECTED_EXIT_TYPES and self.entry_price > 0:
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

        cap_pct = pnl_pct * self._trade_leverage
        reason = (
            f"Scalp {exit_type} {pnl_pct:+.2f}% price "
            f"({cap_pct:+.1f}% capital at {self._trade_leverage}x)"
        )
        # Compute peak P&L BEFORE _record_scalp_result resets entry_price/highest/lowest
        if side == "long" and self.entry_price > 0:
            peak_pnl = ((self.highest_since_entry - self.entry_price) / self.entry_price) * 100
        elif side == "short" and self.entry_price > 0:
            peak_pnl = ((self.entry_price - self.lowest_since_entry) / self.entry_price) * 100
        else:
            peak_pnl = 0.0
        # Track exit price for price-level memory guard (GPFC C)
        ScalpStrategy._pair_last_exit_price[self._base_asset] = price
        ScalpStrategy._pair_last_exit_time_any[self._base_asset] = time.monotonic()
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

            # Use actual trail stop price (computed by _update_trail_stop)
            trail_stop: float | None = self._trail_stop_price if self._trail_stop_price > 0 else None

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
            # Momentum fade / dead momentum timer state
            fade_timer_active = self._mom_fade_since > 0
            fade_elapsed = int(time.monotonic() - self._mom_fade_since) if fade_timer_active else 0
            dead_timer_active = self._mom_dying_since > 0
            dead_elapsed = int(time.monotonic() - self._mom_dying_since) if dead_timer_active else 0

            # Determine confirm time needed based on trend alignment
            fade_required = 0
            dead_required = self.MOM_DYING_CONFIRM_SECONDS
            if fade_timer_active:
                trend_15m = self._get_15m_trend()
                trend_aligned = (
                    (self.position_side == "long" and trend_15m == "bullish")
                    or (self.position_side == "short" and trend_15m == "bearish")
                )
                fade_required = self.MOM_FADE_TREND_CONFIRM if trend_aligned else self.MOM_FADE_CONFIRM_SECONDS

            if open_trade:
                # Live dollar P&L (gross, pre-fee — keeps pnl column fresh for dashboard)
                coin_amount = self.entry_amount
                if self.is_futures and self.entry_amount > 0:
                    from alpha.trade_executor import DELTA_CONTRACT_SIZE
                    contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0.01)
                    coin_amount = self.entry_amount * contract_size
                if self.position_side == "long":
                    live_pnl = (current_price - self.entry_price) * coin_amount
                else:
                    live_pnl = (self.entry_price - current_price) * coin_amount

                await self.executor.db.update_trade(open_trade["id"], {
                    "position_state": state,
                    "trail_stop_price": round(trail_stop, 8) if trail_stop else None,
                    "current_pnl": round(pnl_pct, 4),
                    "current_price": round(current_price, 8),
                    "peak_pnl": round(peak_pnl, 4),
                    "pnl": round(live_pnl, 8),
                    "pnl_pct": round(pnl_pct, 4),
                    "fade_timer_active": fade_timer_active,
                    "fade_elapsed": fade_elapsed if fade_timer_active else None,
                    "fade_required": fade_required if fade_timer_active else None,
                    "dead_timer_active": dead_timer_active,
                    "dead_elapsed": dead_elapsed if dead_timer_active else None,
                    "dead_required": dead_required if dead_timer_active else None,
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

    async def _refresh_balance_if_stale(self) -> None:
        """Refresh exchange balance from API if stale (>60s old).

        Called right before position sizing to ensure we use real-time balance,
        not a cached value from minutes ago. New deposits are picked up immediately.
        """
        REFRESH_INTERVAL = 60  # seconds
        now = time.monotonic()
        if now - self._last_balance_refresh < REFRESH_INTERVAL:
            return  # recent enough

        if not self.trade_exchange:
            return

        try:
            balance = await self.trade_exchange.fetch_balance()
            total_map = balance.get("total", {})
            usd_total = 0.0
            for key in ("USDT", "USD", "USDC"):
                val = total_map.get(key)
                if val is not None and float(val) > 0:
                    usd_total += float(val)

            if usd_total > 0:
                if self._exchange_id == "delta":
                    self.risk_manager.delta_capital = usd_total
                else:
                    self.risk_manager.binance_capital = usd_total
                self.risk_manager.capital = self.risk_manager.binance_capital + self.risk_manager.delta_capital
                self._last_balance_refresh = now
                self.logger.debug(
                    "[%s] Balance refreshed: %s=$%.2f",
                    self.pair, self._exchange_id, usd_total,
                )
        except Exception:
            pass  # non-critical — fall back to cached balance

    # (Legacy _calculate_position_size removed — replaced by _calculate_position_size_dynamic)

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
        2nd simultaneous position gets 60% of normal allocation.
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

        # Reduce for 2nd simultaneous position
        if total_open >= 1:
            base_alloc *= self.SECOND_POS_ALLOC_FACTOR

        return max(5.0, min(70.0, base_alloc))

    # Large position momentum thresholds (bigger positions = stricter entry)
    LARGE_POS_MOM_PCT = 0.12         # require 0.12% momentum for large positions
    LARGE_POS_CONTRACTS: dict[str, int] = {
        "XRP": 50,
        "ETH": 3,
        "BTC": 2,
        "SOL": 3,
    }

    def _calculate_position_size_dynamic(
        self, current_price: float, available: float,
        signal_strength: int, total_open: int,
        momentum_60s: float = 0.0,
    ) -> float | None:
        """Dynamic position sizing based on balance and allocation — NO hardcoded contract caps.

        Sizing flow:
        1. Get allocation % for this pair (performance-based, adaptive)
        2. Reduce for 2nd simultaneous position (60% factor)
        3. Calculate collateral from allocation
        4. Safety cap: never use more than 80% of balance on one position
        5. Calculate notional at leverage
        6. Convert to contracts (round down to whole contracts)
        7. Minimum 1 contract — if can't afford, skip (INSUFFICIENT_CAPITAL)
        8. Large position gate: >50 XRP or >3 ETH requires 0.12% momentum
        """
        exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
        if exchange_capital <= 0:
            return None

        alloc_pct = self._get_adaptive_alloc_pct(signal_strength, total_open)

        # ── Tier-based sizing multiplier ────────────────────────────────
        tier1_mult = 1.0
        if self._entry_path == "tier1":
            if self._tier1_count >= 3:
                tier1_mult = self.TIER1_SIZE_3_MULT
            elif self._tier1_count >= 2:
                tier1_mult = self.TIER1_SIZE_2_MULT
            else:
                tier1_mult = self.TIER1_SIZE_1_MULT
            alloc_pct *= tier1_mult

        # ── Survival mode: low balance → cap allocation ─────────────────
        if exchange_capital < self.SURVIVAL_BALANCE:
            old_alloc = alloc_pct
            alloc_pct = min(alloc_pct, self.SURVIVAL_MAX_ALLOC)
            if old_alloc != alloc_pct:
                self.logger.info(
                    "[%s] SURVIVAL_MODE: balance=$%.2f < $%.0f — capping alloc %.0f%% → %.0f%%",
                    self.pair, exchange_capital, self.SURVIVAL_BALANCE, old_alloc, alloc_pct,
                )

        win_rate, n_trades = self._get_pair_win_rate()

        if self.is_futures:
            from alpha.trade_executor import DELTA_CONTRACT_SIZE
            contract_size = DELTA_CONTRACT_SIZE.get(self.pair, 0)
            if contract_size <= 0:
                return None

            # 1. Collateral from allocation
            collateral = exchange_capital * (alloc_pct / 100)

            # 2. Cap: never exceed available balance
            collateral = min(collateral, available)

            # 3. Safety cap: never use more than 80% of balance on one position
            max_collateral = exchange_capital * (self.MAX_COLLATERAL_PCT / 100)
            collateral = min(collateral, max_collateral)

            # 4. Also cap at risk manager's max_position_pct (configurable)
            max_position_value = exchange_capital * (self.risk_manager.max_position_pct / 100)
            collateral = min(collateral, max_position_value)

            # 5. Calculate notional at leverage and convert to contracts
            notional = collateral * self._trade_leverage
            contract_value = contract_size * current_price
            contracts = int(notional / contract_value)

            # 6. Minimum 1 contract — if can't afford, skip
            if contracts < 1:
                self._skip_reason = "INSUFFICIENT_CAPITAL"
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] INSUFFICIENT_CAPITAL: $%.2f collateral → %d contracts "
                        "(need $%.2f for 1 contract at $%.0f)",
                        self.pair, collateral, contracts,
                        contract_value / self._trade_leverage, current_price,
                    )
                return None

            # 7. Large position gate: bigger positions need stronger momentum
            large_threshold = self.LARGE_POS_CONTRACTS.get(self._base_asset, 999)
            if contracts >= large_threshold and abs(momentum_60s) < self.LARGE_POS_MOM_PCT:
                self._skip_reason = f"LARGE_POS_GATE ({contracts} contracts, mom={momentum_60s:.3f}%)"
                self.logger.info(
                    "LARGE_POS_GATE: %s requiring %.2f%% mom for %d contracts (got %.3f%%) — skipping",
                    self.pair, self.LARGE_POS_MOM_PCT, contracts, abs(momentum_60s),
                )
                return None

            # 8. Big size signal gate: >50 contracts requires 4/4 signals
            BIG_SIZE_CONTRACTS = 50
            if contracts > BIG_SIZE_CONTRACTS and signal_strength < 4:
                self._skip_reason = f"BIG_SIZE_GATE ({contracts} contracts needs 4/4, got {signal_strength}/4)"
                self.logger.info(
                    "BIG_SIZE_GATE: %s %d contracts > %d — needs 4/4 signals but got %d/4, skipping",
                    self.pair, contracts, BIG_SIZE_CONTRACTS, signal_strength,
                )
                return None

            # 9. Risk cap: max SL loss must not exceed MAX_SL_LOSS_PCT of total balance
            # Work backwards: max_notional = max_loss_usd / (sl_pct / 100)
            max_loss_usd = exchange_capital * (self.MAX_SL_LOSS_PCT / 100)
            if self._sl_pct > 0:
                max_notional = max_loss_usd / (self._sl_pct / 100)
                max_contracts = int(max_notional / contract_value)
                if contracts > max_contracts >= 1:
                    self.logger.info(
                        "RISK_CAP: %s %d→%d contracts (max SL loss $%.2f = %.0f%% of $%.2f at %.2f%% SL, %dx)",
                        self.pair, contracts, max_contracts, max_loss_usd,
                        self.MAX_SL_LOSS_PCT, exchange_capital, self._sl_pct, self._trade_leverage,
                    )
                    contracts = max_contracts

            total_collateral = contracts * contract_value / self._trade_leverage
            amount = contracts * contract_size

            self.logger.info(
                "SIZING: %s balance=$%.2f alloc=%.0f%% collateral=$%.2f "
                "notional=$%.2f contracts=%d (%.4f %s) WR=%.0f%%/%d str=%d/4 path=%s t1_mult=%.2f",
                self.pair, exchange_capital, alloc_pct, total_collateral,
                contracts * contract_value, contracts,
                amount, self._base_asset,
                win_rate * 100, n_trades, signal_strength,
                self._entry_path, tier1_mult,
            )
        else:
            # Spot: same performance-based allocation
            capital = exchange_capital * (alloc_pct / 100)
            capital = min(capital, available)

            # Safety cap: 80% of balance
            max_capital = exchange_capital * (self.MAX_COLLATERAL_PCT / 100)
            capital = min(capital, max_capital)

            if capital < self.MIN_NOTIONAL_SPOT:
                self._skip_reason = "INSUFFICIENT_CAPITAL"
                if self._tick_count % 60 == 0:
                    self.logger.info(
                        "[%s] Spot $%.2f < $%.2f min (%.0f%% alloc) — skipping",
                        self.pair, capital, self.MIN_NOTIONAL_SPOT, alloc_pct,
                    )
                return None

            amount = capital / current_price
            self.logger.info(
                "SIZING: %s balance=$%.2f alloc=%.0f%% collateral=$%.2f "
                "notional=$%.2f contracts=spot (%.8f %s) WR=%.0f%%/%d str=%d/4 path=%s t1_mult=%.2f",
                self.pair, exchange_capital, alloc_pct, capital,
                capital, amount, self._base_asset,
                win_rate * 100, n_trades, signal_strength,
                self._entry_path, tier1_mult,
            )

        return amount

    # ======================================================================
    # SETUP CLASSIFICATION
    # ======================================================================

    @staticmethod
    def _classify_setups(reason: str) -> list[str]:
        """Classify entry into its PRIMARY setup type.

        Returns a list with the primary setup type first.  The caller checks
        ONLY the first entry against the dashboard toggle — no fallthrough.
        Priority order (pick the FIRST match):
          1. RSI_OVERRIDE        (RSI < 30 or > 70)
          2. ANTIC               (T1: tag — anticipatory leading signals)
          3. ACCEL_ENTRY         (ACCEL: tag — WS acceleration entry)
          4. BB_SQUEEZE          (BBSQZ tag)
          5. MOMENTUM_BURST      (MOM + VOL)
          6. MEAN_REVERT         (BB + RSI)
          7. TREND_CONT          (TCONT tag)
          8. VWAP_RECLAIM        (VWAP tag)
          9. LIQ_SWEEP           (LIQSWEEP tag)
         10. FVG_FILL            (FVG tag)
         11. VOL_DIVERGENCE      (VOLDIV tag)
         12. MULTI_SIGNAL        (none of above matched — true catch-all)
         13. MIXED               (final fallback)
        """
        r = reason.upper()

        has_mom = "MOM:" in r or "MOM5M:" in r
        has_vol = "VOL:" in r
        has_rsi = "RSI:" in r
        has_bb = "BB:" in r

        # Priority 1: RSI_OVERRIDE — RSI extreme (< 30 or > 70)
        if has_rsi:
            rsi_match = re.search(r"RSI:(\d+)", r)
            if rsi_match:
                rsi_val = int(rsi_match.group(1))
                if rsi_val < 30 or rsi_val > 70:
                    return ["RSI_OVERRIDE"]

        # Priority 2: ANTIC — leading signals (T1: prefix)
        if "T1:" in r:
            return ["ANTIC"]

        # Priority 3: ACCEL_ENTRY — WS acceleration-based entry
        if "ACCEL:" in r:
            return ["ACCEL_ENTRY"]

        # Priority 4: BB_SQUEEZE
        if "BBSQZ:" in r:
            return ["BB_SQUEEZE"]

        # Priority 5: MOMENTUM_BURST
        if has_mom and has_vol:
            return ["MOMENTUM_BURST"]

        # Priority 6: MEAN_REVERT
        if has_bb and has_rsi:
            return ["MEAN_REVERT"]

        # Priority 7: TREND_CONT
        if "TCONT:" in r:
            return ["TREND_CONT"]

        # Priority 8: VWAP_RECLAIM
        if "VWAP:" in r:
            return ["VWAP_RECLAIM"]

        # Priority 9: LIQ_SWEEP
        if "LIQSWEEP:" in r:
            return ["LIQ_SWEEP"]

        # Priority 10: FVG_FILL
        if "FVG:" in r:
            return ["FVG_FILL"]

        # Priority 11: VOL_DIVERGENCE
        if "VOLDIV:" in r:
            return ["VOL_DIVERGENCE"]

        # Priority 12: MULTI_SIGNAL — none of the above matched
        # Only when 4+ distinct signals fired but no recognized pattern
        signal_count = sum([
            has_mom, has_vol, has_rsi, has_bb,
            "VWAP:" in r, "TCONT:" in r, "BBSQZ:" in r,
            "LIQSWEEP:" in r, "FVG:" in r, "VOLDIV:" in r,
        ])
        if signal_count >= 4:
            return ["MULTI_SIGNAL"]

        # Priority 13: MIXED — final fallback
        return ["MIXED"]

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
        candidates = self._classify_setups(reason)
        setup_type = candidates[0]  # primary classification — no fallthrough

        # ── Check if primary setup is enabled via dashboard toggle ──────
        if not self._setup_config.get(setup_type, True):
            self.logger.info(
                "[%s] SETUP_DISABLED: %s — skipping entry",
                self.pair, setup_type,
            )
            return None

        self.logger.info(
            "[%s] %s -> %s entry (%s) SL=%.2f%% TP=%.2f%% ATR=%.3f%% setup=%s",
            self.pair, reason, side.upper(), order_type,
            self._sl_pct, self._tp_pct, self._last_atr_pct, setup_type,
        )

        # signals_fired = raw reason string (all signal tags for analysis)
        mom_10s = getattr(self, '_last_momentum_10s', 0.0)
        mom_prior = getattr(self, '_last_momentum_prior_10s', 0.0)
        _rp = self._range_position
        _rp_tag = f" rng={_rp:.2f}" if _rp is not None else ""
        signals_fired = f"{reason} | 10s={mom_10s:+.3f}% prior={mom_prior:+.3f}% | 5m={self._price_5m_pct:+.2f}%{_rp_tag}"

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
                leverage=self._trade_leverage if self.is_futures else 1,
                position_type="long" if self.is_futures else "spot",
                exchange_id="delta" if self.is_futures else "binance",
                metadata={"pending_side": "long", "pending_amount": amount,
                          "tp_price": tp, "sl_price": sl,
                          "sl_pct": self._sl_pct, "tp_pct": self._tp_pct,
                          "atr_pct": self._last_atr_pct,
                          "setup_type": setup_type,
                          "signals_fired": signals_fired,
                          "entry_path": self._entry_path,
                          "leverage_tier": self._trade_leverage},
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
                leverage=self._trade_leverage,
                position_type="short",
                exchange_id="delta",
                metadata={"pending_side": "short", "pending_amount": amount,
                          "tp_price": tp, "sl_price": sl,
                          "sl_pct": self._sl_pct, "tp_pct": self._tp_pct,
                          "atr_pct": self._last_atr_pct,
                          "setup_type": setup_type,
                          "signals_fired": signals_fired,
                          "entry_path": self._entry_path,
                          "leverage_tier": self._trade_leverage},
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
                amount *= self._trade_leverage

        exit_side = "sell" if side == "long" else "buy"
        return Signal(
            side=exit_side,
            price=price,
            amount=amount,
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.pair,
            leverage=self._trade_leverage if self.is_futures else 1,
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
                self._trade_leverage, soul_msg,
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
        self._trail_stop_price = 0.0              # reset trailing stop price
        self._trail_distance_pct = 0.0             # reset trail distance tier
        self._trail_skip_logged = False            # reset TRAIL_SKIP_LOW_PEAK log flag
        self._trail_widened = False                # reset momentum-aware trail widen flag
        self._peak_unrealized_pnl = 0.0  # reset peak P&L tracker for decay exit
        self._profit_floor_pct = -999.0  # reset ratcheting profit floor
        self._in_position_tick = 0  # reset tick counter for OHLCV refresh cadence
        self._mom_flip_since = 0.0  # reset momentum flip confirmation timer
        self._mom_dying_since = 0.0  # reset momentum dying confirmation timer
        self._mom_fade_since = 0.0   # reset momentum fade confirmation timer
        self._reversal_exit_logged = False  # reset reversal log suppression
        self._pending_tier1 = None  # clear any pending T1 on entry
        self._hourly_trades.append(time.time())
        # Track entry momentum for declining-momentum filter (GPFC B)
        dir_key = f"{self._base_asset}:{side}"
        ScalpStrategy._pair_last_entry_momentum[dir_key] = getattr(self, '_last_entry_momentum', 0.0)

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
        if self._exchange_id == "bybit":
            entry_fee_rate = config.bybit.maker_fee    # 0.02% (limit entry, no GST)
            exit_fee_rate = config.bybit.taker_fee     # 0.055% (market exit, no GST)
        elif self._exchange_id == "delta":
            entry_fee_rate = config.delta.maker_fee_with_gst   # 0.024% (limit entry)
            exit_fee_rate = config.delta.taker_fee_with_gst    # 0.059% (market exit)
        else:
            entry_fee_rate = getattr(self.executor, "_binance_taker_fee", 0.001)
            exit_fee_rate = entry_fee_rate
        est_fees = notional * (entry_fee_rate + exit_fee_rate)
        net_pnl = gross_pnl - est_fees

        capital_pnl_pct = pnl_pct * self._trade_leverage

        self.hourly_pnl += net_pnl

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

        # Track per-pair PER-DIRECTION win/loss history
        dir_key = f"{self._base_asset}:{self.position_side or 'long'}"
        if dir_key not in ScalpStrategy._pair_dir_trade_history:
            ScalpStrategy._pair_dir_trade_history[dir_key] = []
        ScalpStrategy._pair_dir_trade_history[dir_key].append(is_win)
        if len(ScalpStrategy._pair_dir_trade_history[dir_key]) > self.PERF_WINDOW:
            ScalpStrategy._pair_dir_trade_history[dir_key] = \
                ScalpStrategy._pair_dir_trade_history[dir_key][-self.PERF_WINDOW:]

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

        # Reversal cooldown: block ALL re-entry on pair for 2 min (move is dead)
        if exit_type.upper() == "REVERSAL":
            exited_side = self.position_side or "long"
            ScalpStrategy._pair_last_reversal_time[self._base_asset] = now
            ScalpStrategy._pair_last_reversal_side[self._base_asset] = exited_side
            self.logger.info(
                "[%s] REVERSAL COOLDOWN SET — no re-entry on %s for %ds (was %s)",
                self.pair, self._base_asset, self.REVERSAL_COOLDOWN_SECONDS, exited_side,
            )

        # Track exit time per direction for declining-momentum filter (GPFC B)
        dir_key_exit = f"{self._base_asset}:{self.position_side or 'long'}"
        ScalpStrategy._pair_last_exit_time_mono[dir_key_exit] = now

        # Track DEAD_MOMENTUM streak per direction (GPFC #6)
        if exit_type.upper() == "DEAD_MOMENTUM":
            prev_streak = ScalpStrategy._pair_dead_streak.get(dir_key_exit, 0)
            ScalpStrategy._pair_dead_streak[dir_key_exit] = prev_streak + 1
            if prev_streak + 1 >= self.DEAD_STREAK_LIMIT:
                ScalpStrategy._pair_dead_cooldown_until[dir_key_exit] = now + self.DEAD_STREAK_COOLDOWN_S
                self.logger.warning(
                    "[%s] DEAD_STREAK COOLDOWN — %d consecutive DEAD_MOMENTUM on %s, pausing %ds",
                    self.pair, prev_streak + 1, dir_key_exit, self.DEAD_STREAK_COOLDOWN_S,
                )
        else:
            # Any non-DEAD exit resets the streak
            ScalpStrategy._pair_dead_streak[dir_key_exit] = 0

        hold_sec = int(now - self.entry_time)
        duration = f"{hold_sec // 60}m{hold_sec % 60:02d}s" if hold_sec >= 60 else f"{hold_sec}s"

        # Log with fee breakdown for visibility
        fee_ratio = abs(gross_pnl / est_fees) if est_fees > 0 else 0
        pair_losses = ScalpStrategy._pair_consecutive_losses.get(self._base_asset, 0)
        streak_tag = f" streak={pair_losses}" if pair_losses > 0 else ""
        self.logger.info(
            "[%s] CLOSED %s %+.2f%% price (%+.1f%% capital at %dx) | "
            "Gross=$%.4f Net=$%.4f fees=$%.4f (%.1fx) | %s | W/L=%d/%d%s",
            self.pair, exit_type.upper(), pnl_pct, capital_pnl_pct, self._trade_leverage,
            gross_pnl, net_pnl, est_fees, fee_ratio, duration,
            self.hourly_wins, self.hourly_losses, streak_tag,
        )

        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self.entry_amount = 0.0
        self._trailing_active = False
        self._trail_stop_price = 0.0
        self._trail_distance_pct = 0.0
        self._peak_unrealized_pnl = 0.0  # reset for next trade
        self._profit_floor_pct = -999.0  # reset ratcheting profit floor
        self._in_position_tick = 0  # reset tick counter
        self._reversal_exit_logged = False
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
        pass  # No daily loss tracking — trade every opportunity
