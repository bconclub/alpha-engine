"""Alpha Options Scalp — Buy CALLs/PUTs on strong momentum signals.

PHILOSOPHY: Options are the safest momentum play. Max loss = premium paid.
No leverage, no liquidation. When scalp sees 4/4 momentum, buy the option.

Entry: 3-of-4+ momentum signals from scalp strategy (trending regime only)
       Bullish → buy CALL, Bearish → buy PUT
       15m trend conflict gate: no CALLs in bearish, no PUTs in bullish (RSI override)
       Pullback entry: wait up to 30s for 5% premium dip before buying
       1 contract per trade, nearest affordable strike (ATM or up to 3 OTM)
       Minimum 0.25% momentum required

Exit:
  - Ratchet floor: lock profit at (5→2, 10→5, 20→12, 50→35, 100→70)%
  - SL: 30% premium loss (always active, even in Phase 1)
  - Momentum Fade: profitable + momentum < 0.02% for 15s → exit (min 60s hold)
  - Dead Momentum: losing + momentum dead 45s + held 3min → exit
  - TP: 30% premium gain
  - Trailing: activates at +15%, trails 5% behind peak
  - Pullback: exit if lost 40% of peak gain (when peak was 8%+)
  - Decay: exit if was +10%+ and faded to +3%
  - Timeout: close after 5 minutes (theta kills options)
  - Time: close 2 hours before expiry
  - Signal reversal: close if opposite momentum fires
  - Phase 1 (first 30s): only SL fires — no TP/trail/pullback/decay
  - Check every 10 seconds
  - Position verified against exchange every 30s

Position Sizing:
  - Max 20% of capital on options ($2 max)
  - Max 1 option position at a time
  - Premium must be $0.01 to $2.00

Risk: Max loss = premium paid. No liquidation. Safest momentum play.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.db import Database
from alpha.strategies.base import BaseStrategy, MarketCondition, Signal, StrategyName
from alpha.utils import setup_logger

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.strategies.scalp import ScalpStrategy
    from alpha.trade_executor import TradeExecutor

logger = setup_logger("options_scalp")

IST = timezone(timedelta(hours=5, minutes=30))


class OptionsScalpStrategy(BaseStrategy):
    """Buy CALLs/PUTs on strong momentum signals from the scalp strategy.

    Reads the scalp strategy's `last_signal_state` dict every 10 seconds.
    Only enters on 3-of-4+ signals with 15m trend alignment. Max loss = premium paid.
    """

    name = StrategyName.OPTIONS_SCALP
    check_interval_sec = 10  # 10-second ticks

    # ── Option chain refresh ──────────────────────────────────────
    CHAIN_REFRESH_INTERVAL = 30 * 60     # Refresh every 30 min
    MIN_EXPIRY_HOURS = 4                 # Must be 4+ hours to expiry
    CLOSE_BEFORE_EXPIRY_HOURS = 2        # Close 2 hours before expiry

    # ── Strike selection ──────────────────────────────────────────
    BTC_STRIKE_ROUND = 200               # BTC: nearest $200
    ETH_STRIKE_ROUND = 20                # ETH: nearest $20
    MAX_OTM_STRIKES = 3                  # Max strikes to walk OTM for affordability

    # ── Premium limits ────────────────────────────────────────────
    OPTIONS_LEVERAGE = 50                # Delta options are 50x leveraged
    MAX_PREMIUM_CAPITAL_PCT = 20.0       # Max 20% of capital (compared against collateral)
    MAX_PREMIUM_USD = 2.00               # Hard cap $2 collateral
    MIN_PREMIUM_USD = 0.01               # Skip illiquid < $0.01

    # ── Entry ─────────────────────────────────────────────────────
    MIN_SIGNAL_STRENGTH = 3              # 3-of-4 required (was 4 — 3/4+ signals enough)
    SIGNAL_STALENESS_SEC = 15            # Signal must be < 15s old (was 30 — stale entries)
    CONTRACTS_PER_TRADE = 1              # 1 contract per trade
    MIN_MOMENTUM_PCT = 0.25             # Skip if |momentum_60s| < 0.25%

    # ── Pullback entry ───────────────────────────────────────────
    PULLBACK_WAIT_SEC = 30              # Wait up to 30s for premium dip
    PULLBACK_DIP_PCT = 5.0             # Min dip to trigger entry
    PULLBACK_SKIP_RISE_PCT = 5.0       # Skip if premium rose this much
    PULLBACK_POLL_SEC = 2              # Check every 2s during pullback wait

    # ── Exit thresholds (tuned for momentum scalps) ────────────────
    TP_PREMIUM_GAIN_PCT = 30.0           # Take profit at +30% premium gain
    SL_PREMIUM_LOSS_PCT = 30.0           # Stop loss at -30% premium drop (was 20 — noise clips at 50x)
    TRAILING_ACTIVATE_PCT = 15.0         # Trail activates at +15% gain (was 10 — too early)
    TRAILING_DISTANCE_PCT = 5.0          # Trail 5% below peak premium
    PULLBACK_EXIT_PCT = 40.0             # Exit if lost 40% of peak gain (was 50 — too aggressive)
    PULLBACK_ACTIVATE_PCT = 8.0          # Pullback only fires after +8% peak (was 5 — let winners breathe)
    DECAY_THRESHOLD_PCT = 3.0            # Exit if was +10%+ and faded to +3%
    TIMEOUT_MINUTES = 5                  # Options timeout (was 10 — theta kills, 5min enough)
    PHASE1_HANDS_OFF_SEC = 30            # Only SL fires in first 30s after fill

    # ── Momentum fade — premium profitable but momentum dying ────────
    OPT_MOM_FADE_THRESHOLD = 0.02        # momentum < 0.02% = dying
    OPT_MOM_FADE_CONFIRM_SEC = 15        # hold 15s below threshold to confirm
    OPT_MOM_FADE_MIN_HOLD = 60           # min 60s in position before fade can fire
    OPT_MOM_FADE_TREND_HOLD = 90         # trend-aligned: need 90s hold
    OPT_MOM_FADE_TREND_CONFIRM = 20      # trend-aligned: need 20s confirm

    # ── Dead momentum — momentum dead + losing + held too long ───────
    OPT_DEAD_MOM_CONFIRM_SEC = 45        # 45s of dead momentum
    OPT_DEAD_MOM_MIN_HOLD = 180          # min 3min hold before dead fires

    # ── Ratchet floor table: (peak_pct, locked_floor_pct) ────────────
    OPT_RATCHET_FLOOR_TABLE = [
        (5.0, 2.0),
        (10.0, 5.0),
        (20.0, 12.0),
        (50.0, 35.0),
        (100.0, 70.0),
    ]

    # ── Position limits ───────────────────────────────────────────
    MAX_OPTION_POSITIONS = 1             # 1 option at a time

    def __init__(
        self,
        pair: str,
        executor: TradeExecutor,
        risk_manager: RiskManager,
        options_exchange: Any = None,
        futures_exchange: Any = None,
        scalp_strategy: ScalpStrategy | None = None,
        market_analyzer: Any = None,
        db: Database | None = None,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.options_exchange: ccxt.Exchange | None = options_exchange
        self.futures_exchange: ccxt.Exchange | None = futures_exchange
        self._scalp = scalp_strategy
        self._market_analyzer = market_analyzer
        self._db = db
        self._exchange_id = "delta"

        # Asset info
        self._base_asset = "BTC" if "BTC" in pair else "ETH"

        # Option chain cache
        self._option_chain: list[dict[str, Any]] = []
        self._chain_last_refresh: float = 0.0
        self._selected_expiry: datetime | None = None
        self._available_strikes: list[float] = []

        # Position state
        self.in_position = False
        self.option_side: str | None = None       # "call" or "put"
        self.option_symbol: str | None = None      # ccxt unified symbol
        self.entry_premium: float = 0.0
        self.entry_time: float = 0.0
        self.highest_premium: float = 0.0
        self._trailing_active: bool = False
        self.strike_price: float = 0.0
        self.expiry_dt: datetime | None = None

        # Stats
        self._tick_count: int = 0
        self.hourly_wins: int = 0
        self.hourly_losses: int = 0
        self.hourly_pnl: float = 0.0

        # Skip-logging throttle: only log each skip reason once per 5 min
        self._last_skip_reason: str = ""
        self._last_skip_time: float = 0.0
        self._SKIP_LOG_INTERVAL = 5 * 60  # 5 minutes

        # Dashboard state write interval
        self._STATE_WRITE_INTERVAL = 30  # Write to DB every 30 seconds
        self._last_state_write: float = 0.0

        # Ticker failure tracking — detect POSITION_GONE (expired/delisted)
        self._consecutive_ticker_failures: int = 0
        self._MAX_TICKER_FAILURES = 6   # 6 failures × 10s = 60s of no data → position gone
        self._EXPIRY_CLOSE_MINUTES = 5  # Within 5 min of expiry, treat ticker fail as expired

        # Last known premium — used as exit price when position disappears
        self._last_known_premium: float = 0.0

        # Cooldown after POSITION_GONE — no new options entry for 60s
        self._position_gone_cooldown_until: float = 0.0
        self._POSITION_GONE_COOLDOWN_SEC = 60

        # Regime skip logging throttle (log once per 60s to avoid spam)
        self._last_regime_log: float = 0.0

        # Position verification ticker (every 3rd tick = ~30s)
        self._position_verify_tick: int = 0

        # Momentum fade / dead momentum timers
        self._opt_mom_fade_since: float | None = None
        self._opt_mom_dying_since: float | None = None
        # Ratchet profit floor
        self._opt_ratchet_floor: float = 0.0

    # ==================================================================
    # ACTIVITY LOGGING
    # ==================================================================

    async def _log_activity(
        self,
        event_type: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an event to activity_log (visible on dashboard Live Activity)."""
        if self._db:
            try:
                await self._db.log_activity(
                    event_type=event_type,
                    pair=self.pair,
                    description=description,
                    exchange="delta",
                    metadata=metadata,
                )
            except Exception as e:
                self.logger.debug("[%s] activity_log write failed: %s", self.pair, e)

    async def _log_skip(self, reason: str, metadata: dict[str, Any] | None = None) -> None:
        """Log an options skip event (throttled to avoid spam)."""
        now = time.monotonic()
        if reason == self._last_skip_reason and (now - self._last_skip_time) < self._SKIP_LOG_INTERVAL:
            return
        self._last_skip_reason = reason
        self._last_skip_time = now
        await self._log_activity("options_skip", reason, metadata)

    # ==================================================================
    # LIFECYCLE
    # ==================================================================

    async def on_start(self) -> None:
        """Load option markets on startup + restore position state from DB."""
        if self.options_exchange:
            try:
                await self.options_exchange.load_markets()
                opt_count = sum(
                    1 for m in self.options_exchange.markets.values()
                    if m.get("type") == "option"
                )
                self.logger.info(
                    "[%s] Options exchange loaded — %d option markets",
                    self.pair, opt_count,
                )
            except Exception as e:
                self.logger.error("[%s] Failed to load options markets: %s", self.pair, e)

        await self._refresh_option_chain()

        # Restore position state from DB if engine restarted with open option trade
        await self._restore_position_from_db()

        self.logger.info(
            "[%s] OPTIONS SCALP ACTIVE — min_strength=%d, "
            "TP=%d%% SL=%d%% Trail=%d%%/%d%% Pullback=%d%% Decay=%d%% "
            "Timeout=%dm Phase1=%ds MaxPremium=$%.2f%s",
            self.pair, self.MIN_SIGNAL_STRENGTH,
            int(self.TP_PREMIUM_GAIN_PCT), int(self.SL_PREMIUM_LOSS_PCT),
            int(self.TRAILING_ACTIVATE_PCT), int(self.TRAILING_DISTANCE_PCT),
            int(self.PULLBACK_EXIT_PCT), int(self.DECAY_THRESHOLD_PCT),
            self.TIMEOUT_MINUTES, self.PHASE1_HANDS_OFF_SEC,
            self.MAX_PREMIUM_USD,
            f" | RESTORED: {self.option_side} {self.option_symbol}" if self.in_position else "",
        )

    async def _restore_position_from_db(self) -> None:
        """Restore in-memory position state from DB after engine restart.

        Checks the trades table for an open options_scalp trade on this pair's
        underlying asset. If found, restores all position tracking fields so
        exit management continues seamlessly.
        """
        if not self._db or not self._db.is_connected:
            return

        try:
            # Options trades are stored with the option symbol as pair
            # (e.g. ETH/USD:USD-260222-1980-C), but we need to find by strategy
            open_trades = await self._db.get_open_trades(pair=None)
            for trade in open_trades:
                if trade.get("strategy") != "options_scalp":
                    continue
                # Match by base asset (BTC or ETH)
                trade_pair = trade.get("pair", "")
                trade_asset = trade_pair.split("/")[0] if "/" in trade_pair else ""
                if trade_asset != self._base_asset:
                    continue

                # Found our open option trade — restore state
                self.in_position = True
                self.option_symbol = trade_pair
                self.entry_premium = trade.get("entry_price", 0)
                self.entry_time = time.monotonic()  # can't restore exact time, use now
                self.highest_premium = max(
                    self.entry_premium,
                    trade.get("current_price") or self.entry_premium,
                )

                # Determine option side from position_type or pair suffix
                if trade_pair.endswith("-C"):
                    self.option_side = "call"
                elif trade_pair.endswith("-P"):
                    self.option_side = "put"
                else:
                    self.option_side = "call"  # fallback

                # Restore trailing state
                self._trailing_active = trade.get("position_state") == "trailing"
                self.strike_price = trade.get("stop_loss", 0) or 0  # strike stored elsewhere

                # Try to parse strike from symbol: ETH/USD:USD-260222-1980-C
                parts = trade_pair.split("-")
                if len(parts) >= 3:
                    try:
                        self.strike_price = float(parts[-2])
                    except ValueError:
                        pass

                # Try to restore expiry from symbol: -YYMMDD-
                if len(parts) >= 2:
                    try:
                        expiry_str = parts[-3] if len(parts) >= 4 else parts[1]
                        self.expiry_dt = datetime.strptime(expiry_str, "%y%m%d").replace(
                            hour=12, tzinfo=timezone.utc,
                        )
                    except (ValueError, IndexError):
                        pass

                self.logger.info(
                    "[%s] RESTORED from DB: %s %s strike=$%.0f entry=$%.4f peak=$%.4f trail=%s",
                    self.pair, self.option_side, self.option_symbol,
                    self.strike_price, self.entry_premium, self.highest_premium,
                    self._trailing_active,
                )
                break  # Only one position per asset

        except Exception as e:
            self.logger.error("[%s] Failed to restore position from DB: %s", self.pair, e)

    async def _update_position_state_in_db(self, current_premium: float) -> None:
        """Write live position state to the trades table so dashboard shows real P&L.

        Similar to scalp.py's _update_position_state_in_db, writes:
        current_price (premium), position_state, current_pnl, peak_pnl
        every ~10s.
        """
        if not self._db or not self._db.is_connected:
            return
        if not self.in_position or not self.option_symbol:
            return

        try:
            # P&L %
            pnl_pct = 0.0
            if self.entry_premium > 0:
                pnl_pct = (current_premium - self.entry_premium) / self.entry_premium * 100

            # Peak P&L %
            peak_pnl = 0.0
            if self.entry_premium > 0:
                peak_pnl = (self.highest_premium - self.entry_premium) / self.entry_premium * 100

            state = "trailing" if self._trailing_active else "holding"

            # Find our open trade (options trade pair = option symbol)
            open_trade = await self._db.get_open_trade(
                pair=self.option_symbol, exchange="delta", strategy="options_scalp",
            )
            if open_trade:
                await self._db.update_trade(open_trade["id"], {
                    "position_state": state,
                    "current_price": round(current_premium, 8),
                    "current_pnl": round(pnl_pct, 4),
                    "peak_pnl": round(peak_pnl, 4),
                })
        except Exception as e:
            self.logger.debug("[%s] position state DB update failed: %s", self.pair, e)

    # ==================================================================
    # OPTION CHAIN MANAGEMENT
    # ==================================================================

    async def _refresh_option_chain(self) -> None:
        """Fetch available option contracts, filter for valid expiries.

        Refreshed every 30 minutes. Filters for:
        - Correct underlying asset (BTC or ETH)
        - Expiry at least MIN_EXPIRY_HOURS away
        - Both calls and puts
        """
        now = time.monotonic()
        if now - self._chain_last_refresh < self.CHAIN_REFRESH_INTERVAL and self._option_chain:
            return

        if not self.options_exchange:
            return

        try:
            # Reload markets to get fresh option listings
            if self._chain_last_refresh > 0:
                await self.options_exchange.load_markets(True)  # force reload

            markets = self.options_exchange.markets
            now_utc = datetime.now(timezone.utc)
            min_expiry = now_utc + timedelta(hours=self.MIN_EXPIRY_HOURS)

            chain: list[dict[str, Any]] = []
            for symbol, market in markets.items():
                if market.get("type") != "option":
                    continue
                if market.get("base") != self._base_asset:
                    continue
                if not market.get("active", True):
                    continue

                expiry_ts = market.get("expiry")
                if expiry_ts is None:
                    continue
                expiry_dt = datetime.fromtimestamp(expiry_ts / 1000, tz=timezone.utc)
                if expiry_dt < min_expiry:
                    continue

                chain.append({
                    "symbol": symbol,
                    "strike": float(market.get("strike", 0)),
                    "option_type": (market.get("optionType") or "").lower(),
                    "expiry": expiry_dt,
                })

            chain.sort(key=lambda x: (x["expiry"], x["strike"]))
            self._option_chain = chain
            self._chain_last_refresh = now

            if chain:
                self._selected_expiry = chain[0]["expiry"]
                self._available_strikes = sorted(set(
                    c["strike"] for c in chain
                    if c["expiry"] == self._selected_expiry
                ))
                hours_away = (self._selected_expiry - now_utc).total_seconds() / 3600
                self.logger.info(
                    "[%s] Option chain refreshed: %d contracts, "
                    "selected expiry=%s (%.1fh away), %d strikes",
                    self.pair, len(chain),
                    self._selected_expiry.strftime("%b %d %H:%M UTC"),
                    hours_away, len(self._available_strikes),
                )
            else:
                self._selected_expiry = None
                self._available_strikes = []
                self.logger.warning("[%s] No valid option contracts found", self.pair)

        except Exception as e:
            self.logger.error("[%s] Failed to refresh option chain: %s", self.pair, e)

    # ==================================================================
    # STRIKE SELECTION
    # ==================================================================

    def _get_atm_strike(self, current_price: float) -> float | None:
        """Find the ATM strike nearest to spot price."""
        if not self._available_strikes:
            return None
        return min(self._available_strikes, key=lambda s: abs(s - current_price))

    def _get_otm_candidates(
        self, atm_strike: float, option_type: str,
    ) -> list[float]:
        """Get sorted OTM strikes away from ATM (up for calls, down for puts).

        Returns up to MAX_OTM_STRIKES candidates.
        """
        if option_type == "call":
            candidates = sorted(s for s in self._available_strikes if s > atm_strike)
        else:
            candidates = sorted(
                (s for s in self._available_strikes if s < atm_strike), reverse=True,
            )
        return candidates[:self.MAX_OTM_STRIKES]

    def _build_option_symbol(
        self, strike: float, option_type: str, expiry: datetime,
    ) -> str | None:
        """Find the ccxt unified symbol for the given option parameters.

        Searches the cached chain first, falls back to manual construction:
        BTC/USD:USD-YYMMDD-STRIKE-C/P
        """
        target_type = option_type.lower()
        for opt in self._option_chain:
            if (opt["strike"] == strike
                    and opt["option_type"] == target_type
                    and opt["expiry"] == expiry):
                return opt["symbol"]

        # Fallback: construct manually
        expiry_str = expiry.strftime("%y%m%d")
        strike_str = str(int(strike))
        cp = "C" if target_type == "call" else "P"
        symbol = f"{self._base_asset}/USD:USD-{expiry_str}-{strike_str}-{cp}"
        self.logger.warning(
            "[%s] Option not in chain, constructed: %s", self.pair, symbol,
        )
        return symbol

    # ==================================================================
    # DASHBOARD STATE
    # ==================================================================

    async def _write_dashboard_state(self) -> None:
        """Write current options state to DB for dashboard every 30 seconds."""
        now = time.monotonic()
        if now - self._last_state_write < self._STATE_WRITE_INTERVAL:
            return
        self._last_state_write = now

        if not self._db:
            return

        # ── Signal state from scalp ──
        signal_strength = 0
        signal_side: str | None = None
        signal_reason = ""
        spot_price = 0.0

        if self._scalp and hasattr(self._scalp, "last_signal_state"):
            ss = self._scalp.last_signal_state
            if ss:
                signal_strength = ss.get("strength", 0)
                signal_side = ss.get("side")
                signal_reason = ss.get("reason", "")
                spot_price = ss.get("current_price", 0)

        # Fallback: fetch spot price from futures exchange if scalp didn't provide one
        if spot_price <= 0 and self.futures_exchange:
            try:
                ticker = await self.futures_exchange.fetch_ticker(self.pair)
                spot_price = ticker.get("last") or ticker.get("bid") or 0
            except Exception:
                pass

        # ── Expiry info ──
        expiry_label: str | None = None
        expiry_ts: str | None = None
        atm_strike: float | None = None
        call_premium: float | None = None
        put_premium: float | None = None

        if self._selected_expiry:
            now_utc = datetime.now(timezone.utc)
            hours_away = (self._selected_expiry - now_utc).total_seconds() / 3600
            expiry_label = (
                f"{self._selected_expiry.strftime('%b %d %H:%M UTC')} — "
                f"{int(hours_away)}h away"
            )
            expiry_ts = self._selected_expiry.isoformat()

            # ATM strike
            if self._available_strikes and spot_price > 0:
                atm_strike = min(self._available_strikes, key=lambda s: abs(s - spot_price))

                # Fetch ATM call + put premiums (best-effort, skip on error)
                try:
                    call_sym = self._build_option_symbol(
                        atm_strike, "call", self._selected_expiry,
                    )
                    if call_sym and self.options_exchange:
                        t = await self.options_exchange.fetch_ticker(call_sym)
                        call_premium = t.get("last") or t.get("ask") or None
                except Exception:
                    pass

                try:
                    put_sym = self._build_option_symbol(
                        atm_strike, "put", self._selected_expiry,
                    )
                    if put_sym and self.options_exchange:
                        t = await self.options_exchange.fetch_ticker(put_sym)
                        put_premium = t.get("last") or t.get("ask") or None
                except Exception:
                    pass

        # ── Position info ──
        position_side: str | None = None
        position_strike: float | None = None
        position_symbol: str | None = None
        entry_prem: float | None = None
        current_prem: float | None = None
        pnl_pct: float | None = None
        pnl_usd: float | None = None
        trailing_active = False
        highest_prem: float | None = None

        if self.in_position and self.option_symbol:
            position_side = self.option_side
            position_strike = self.strike_price
            position_symbol = self.option_symbol
            entry_prem = self.entry_premium
            highest_prem = self.highest_premium
            trailing_active = self._trailing_active

            # Fetch current premium for position
            try:
                if self.options_exchange:
                    ticker = await self.options_exchange.fetch_ticker(self.option_symbol)
                    current_prem = ticker.get("last") or ticker.get("bid") or None
                    if current_prem and entry_prem and entry_prem > 0:
                        pnl_pct = (current_prem - entry_prem) / entry_prem * 100
                        pnl_usd = (current_prem - entry_prem) * self.CONTRACTS_PER_TRADE
            except Exception:
                pass

        state = {
            "spot_price": spot_price or None,
            "expiry": expiry_ts,
            "expiry_label": expiry_label,
            "atm_strike": atm_strike,
            "call_premium": call_premium,
            "put_premium": put_premium,
            "signal_strength": signal_strength,
            "signal_side": signal_side,
            "signal_reason": signal_reason,
            "position_side": position_side,
            "position_strike": position_strike,
            "position_symbol": position_symbol,
            "entry_premium": entry_prem,
            "current_premium": current_prem,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "pnl_usd": round(pnl_usd, 4) if pnl_usd is not None else None,
            "trailing_active": trailing_active,
            "highest_premium": highest_prem,
        }

        await self._db.upsert_options_state(self.pair, state)

    # ==================================================================
    # MAIN CHECK LOOP
    # ==================================================================

    async def check(self) -> list[Signal]:
        """Main tick: refresh chain, check for entry/exit."""
        self._tick_count += 1

        # Periodic chain refresh
        await self._refresh_option_chain()

        # Write dashboard state every 30 seconds
        await self._write_dashboard_state()

        # In position: manage exit
        if self.in_position:
            return await self._check_option_exit()

        # Not in position: look for entry from scalp signals
        return await self._check_option_entry()

    # ==================================================================
    # ENTRY LOGIC
    # ==================================================================

    async def _check_option_entry(self) -> list[Signal]:
        """Check scalp's signal state for 3/4+ momentum, buy option."""
        # 0. POSITION_GONE cooldown — no new entries for 60s after position disappeared
        if time.monotonic() < self._position_gone_cooldown_until:
            remaining = self._position_gone_cooldown_until - time.monotonic()
            if self._tick_count % 6 == 0:
                self.logger.info(
                    "[%s] OPTIONS COOLDOWN after POSITION_GONE — %.0fs remaining",
                    self.pair, remaining,
                )
            return []

        # 1. Market regime gate — block CHOPPY, allow TRENDING + SIDEWAYS
        if self._market_analyzer:
            analysis = self._market_analyzer.last_analysis_for(self.pair)
            if analysis and analysis.condition == MarketCondition.CHOPPY:
                now = time.monotonic()
                if now - self._last_regime_log >= 60:
                    self._last_regime_log = now
                    self.logger.info(
                        "[%s] OPTIONS REGIME SKIP: %s (need non-CHOPPY)",
                        self.pair, analysis.condition.value,
                    )
                return []

        # 2. Read scalp strategy's latest signal
        if not self._scalp or not hasattr(self._scalp, "last_signal_state"):
            if self._tick_count % 30 == 0:
                self.logger.info("[%s] OPTIONS: no scalp ref (scalp=%s)", self.pair, bool(self._scalp))
            return []

        signal_state = self._scalp.last_signal_state

        if signal_state is None:
            if self._tick_count % 6 == 0:
                self.logger.info("[%s] OPTIONS: signal_state is None", self.pair)
            return []

        # 2. Check signal freshness
        signal_age = time.monotonic() - signal_state.get("timestamp", 0)
        strength = signal_state.get("strength", 0)

        # Only log OPTIONS CHECK when strength >= 1 (reduce spam)
        if strength >= 1:
            self.logger.info(
                "[%s] OPTIONS CHECK: strength=%d side=%s age=%.1fs reason=%s",
                self.pair, strength,
                signal_state.get("side"),
                signal_age,
                (signal_state.get("reason", "") or "")[:60],
            )

        if signal_age > self.SIGNAL_STALENESS_SEC:
            if strength >= 1:
                self.logger.info(
                    "[%s] OPTIONS STALE: age=%.1fs > %ds — skipping",
                    self.pair, signal_age, self.SIGNAL_STALENESS_SEC,
                )
            return []

        # 3. Check signal strength (3-of-4 required)
        if strength < self.MIN_SIGNAL_STRENGTH:
            # Log WEAK every ~60s (6 ticks × 10s = 60s), not every 10s
            if strength >= 1 and self._tick_count % 6 == 0:
                self.logger.info(
                    "[%s] OPTIONS WEAK: strength=%d < %d — waiting",
                    self.pair, strength, self.MIN_SIGNAL_STRENGTH,
                )
            return []

        # 3b. Minimum momentum gate — skip weak moves
        momentum_60s = abs(signal_state.get("momentum_60s", 0) or 0)
        if momentum_60s < self.MIN_MOMENTUM_PCT:
            self.logger.info(
                "[%s] OPTIONS WEAK MOM: |momentum|=%.3f%% < %.2f%% — skipping",
                self.pair, momentum_60s, self.MIN_MOMENTUM_PCT,
            )
            return []

        side = signal_state.get("side")
        if side is None:
            self.logger.info("[%s] OPTIONS: 3/4+ but side=None — skipping", self.pair)
            return []

        # 4. Determine option type
        option_type = "call" if side == "long" else "put"

        # 4b. 15m trend conflict gate — don't buy CALLs in bearish, PUTs in bullish
        if self._scalp:
            trend_15m = self._scalp._get_15m_trend()
            rsi_val = signal_state.get("rsi")
            if option_type == "call" and trend_15m == "bearish":
                # RSI override: extreme oversold (< 25) allows CALL in bearish
                if rsi_val is not None and rsi_val < 25:
                    self.logger.info(
                        "[%s] OPTIONS 15m bearish but RSI=%.1f < 25 — CALL override",
                        self.pair, rsi_val,
                    )
                else:
                    self.logger.info(
                        "[%s] OPTIONS 15m BEARISH — skipping CALL (RSI=%.1f)",
                        self.pair, rsi_val or 0,
                    )
                    await self._log_skip(
                        f"{self.pair} — OPTIONS SKIP: 15m bearish, no CALL",
                        {"trend": trend_15m, "rsi": rsi_val, "option_type": option_type},
                    )
                    return []
            elif option_type == "put" and trend_15m == "bullish":
                # RSI override: extreme overbought (> 75) allows PUT in bullish
                if rsi_val is not None and rsi_val > 75:
                    self.logger.info(
                        "[%s] OPTIONS 15m bullish but RSI=%.1f > 75 — PUT override",
                        self.pair, rsi_val,
                    )
                else:
                    self.logger.info(
                        "[%s] OPTIONS 15m BULLISH — skipping PUT (RSI=%.1f)",
                        self.pair, rsi_val or 0,
                    )
                    await self._log_skip(
                        f"{self.pair} — OPTIONS SKIP: 15m bullish, no PUT",
                        {"trend": trend_15m, "rsi": rsi_val, "option_type": option_type},
                    )
                    return []

        self.logger.info(
            "[%s] OPTIONS SIGNAL READY: %s %d/4 — checking chain/premium",
            self.pair, option_type.upper(), strength,
        )

        # 5. Get current underlying price
        current_price = signal_state.get("current_price", 0)
        if current_price <= 0:
            self.logger.info("[%s] OPTIONS: no current_price in signal", self.pair)
            return []

        # 6. Check expiry validity
        if self._selected_expiry is None:
            if self._tick_count % 30 == 0:
                self.logger.info("[%s] No valid expiry available", self.pair)
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: no valid expiry available",
                {"option_type": option_type, "strength": strength},
            )
            return []

        hours_to_expiry = (
            self._selected_expiry - datetime.now(timezone.utc)
        ).total_seconds() / 3600
        if hours_to_expiry < self.MIN_EXPIRY_HOURS:
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Nearest expiry only %.1fh away (need %dh+)",
                    self.pair, hours_to_expiry, self.MIN_EXPIRY_HOURS,
                )
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: expiry only {hours_to_expiry:.1f}h away (need {self.MIN_EXPIRY_HOURS}h+)",
                {"option_type": option_type, "strength": strength, "hours_to_expiry": round(hours_to_expiry, 1)},
            )
            return []

        # 7. Find ATM strike
        atm_strike = self._get_atm_strike(current_price)
        if atm_strike is None:
            if self._tick_count % 30 == 0:
                self.logger.info("[%s] No valid strikes found", self.pair)
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: no valid strikes for {option_type.upper()}",
                {"option_type": option_type, "strength": strength, "price": current_price},
            )
            return []

        # 8. Collateral budget
        exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
        max_collateral = min(
            exchange_capital * (self.MAX_PREMIUM_CAPITAL_PCT / 100),
            self.MAX_PREMIUM_USD,
        )

        # 9. Try ATM first, then walk OTM strikes if too expensive
        strikes_to_try = [atm_strike] + self._get_otm_candidates(atm_strike, option_type)
        selected_strike: float | None = None
        selected_symbol: str | None = None
        premium: float = 0.0
        atm_collateral: float | None = None  # track ATM cost for logging

        for i, strike in enumerate(strikes_to_try):
            symbol = self._build_option_symbol(strike, option_type, self._selected_expiry)
            if symbol is None:
                continue

            try:
                ticker = await self.options_exchange.fetch_ticker(symbol)
                prem = ticker.get("last") or ticker.get("ask") or 0
            except Exception as e:
                self.logger.debug("[%s] Ticker fetch failed for %s: %s", self.pair, symbol, e)
                continue

            if prem <= 0:
                continue

            collateral = prem / self.OPTIONS_LEVERAGE

            # Track ATM collateral for logging
            if i == 0:
                atm_collateral = collateral

            # Check premium not too small (illiquid)
            if prem < self.MIN_PREMIUM_USD:
                self.logger.debug(
                    "[%s] Strike $%.0f premium $%.4f < min $%.2f — illiquid",
                    self.pair, strike, prem, self.MIN_PREMIUM_USD,
                )
                continue

            # Check collateral fits budget
            if collateral <= max_collateral:
                selected_strike = strike
                selected_symbol = symbol
                premium = prem
                if i > 0:
                    self.logger.info(
                        "[%s] %s %s: ATM=$%.0f too expensive ($%.2f), "
                        "selected $%.0f OTM ($%.2f collateral)",
                        self.pair, self._base_asset, option_type.upper(),
                        atm_strike, atm_collateral or 0,
                        strike, collateral,
                    )
                break

            self.logger.debug(
                "[%s] Strike $%.0f collateral $%.4f > max $%.4f — trying OTM",
                self.pair, strike, collateral, max_collateral,
            )

        if selected_strike is None or selected_symbol is None:
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] No affordable strike within %d OTM — skipping "
                    "(ATM=$%.0f collateral=$%.4f max=$%.4f)",
                    self.pair, self.MAX_OTM_STRIKES, atm_strike,
                    atm_collateral or 0, max_collateral,
                )
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: no affordable strike within "
                f"{self.MAX_OTM_STRIKES} OTM (ATM=${atm_strike:.0f} "
                f"collateral=${atm_collateral or 0:.4f} > ${max_collateral:.4f})",
                {"option_type": option_type, "atm_strike": atm_strike,
                 "atm_collateral": atm_collateral, "max_collateral": max_collateral,
                 "strength": strength},
            )
            return []

        # 10. Classify setup_type from scalp signal reason
        signals_str = signal_state.get("reason", "")
        setup_type = "unknown"
        if self._scalp and signals_str:
            try:
                candidates = self._scalp._classify_setups(signals_str)
                if candidates:
                    setup_type = candidates[0]  # highest priority setup
            except Exception:
                pass

        # 11. Pullback entry — wait up to 30s for premium dip before buying
        expiry_str = self._selected_expiry.strftime('%b %d %H:%M')
        strike_label = "ATM" if selected_strike == atm_strike else "OTM"

        return await self._attempt_pullback_entry(
            option_type=option_type,
            selected_symbol=selected_symbol,
            selected_strike=selected_strike,
            atm_strike=atm_strike,
            signal_premium=premium,
            strength=strength,
            signals_str=signals_str,
            current_price=current_price,
            setup_type=setup_type,
            expiry_str=expiry_str,
            strike_label=strike_label,
        )

    # ==================================================================
    # PULLBACK ENTRY
    # ==================================================================

    async def _attempt_pullback_entry(
        self,
        option_type: str,
        selected_symbol: str,
        selected_strike: float,
        atm_strike: float,
        signal_premium: float,
        strength: int,
        signals_str: str,
        current_price: float,
        setup_type: str,
        expiry_str: str,
        strike_label: str,
    ) -> list[Signal]:
        """Wait up to PULLBACK_WAIT_SEC for premium to dip before entering.

        1. Poll option ticker every PULLBACK_POLL_SEC (2s)
        2. If premium dips 5-10% below signal → enter at market (dipped price)
        3. If premium rises 5%+ above signal → skip (move already priced in)
        4. After 30s no dip → enter at market only if within +5% of signal price
        """
        import asyncio

        self.logger.info(
            "[%s] PULLBACK WAIT: %s $%.0f premium=$%.4f — waiting up to %ds for dip",
            self.pair, option_type.upper(), selected_strike,
            signal_premium, self.PULLBACK_WAIT_SEC,
        )

        elapsed = 0.0
        entry_premium = signal_premium  # default: use signal-time price

        while elapsed < self.PULLBACK_WAIT_SEC:
            await asyncio.sleep(self.PULLBACK_POLL_SEC)
            elapsed += self.PULLBACK_POLL_SEC

            try:
                ticker = await self.options_exchange.fetch_ticker(selected_symbol)
                now_premium = ticker.get("last") or ticker.get("ask") or 0
            except Exception as e:
                self.logger.debug("[%s] Pullback ticker fail: %s", self.pair, e)
                continue

            if now_premium <= 0:
                continue

            change_pct = (now_premium - signal_premium) / signal_premium * 100

            # Premium rose too much — move already priced in, skip entry
            if change_pct >= self.PULLBACK_SKIP_RISE_PCT:
                self.logger.info(
                    "[%s] PULLBACK SKIP: premium rose +%.1f%% ($%.4f → $%.4f) — move priced in",
                    self.pair, change_pct, signal_premium, now_premium,
                )
                await self._log_skip(
                    f"{self.pair} — OPTIONS PULLBACK SKIP: premium rose +{change_pct:.1f}%",
                    {"signal_premium": signal_premium, "now_premium": now_premium},
                )
                return []

            # Premium dipped enough — enter now
            if change_pct <= -self.PULLBACK_DIP_PCT:
                entry_premium = now_premium
                self.logger.info(
                    "[%s] PULLBACK DIP: premium dipped %.1f%% ($%.4f → $%.4f) — entering",
                    self.pair, change_pct, signal_premium, now_premium,
                )
                break

            self.logger.debug(
                "[%s] PULLBACK polling: %.0fs premium=$%.4f (%+.1f%%)",
                self.pair, elapsed, now_premium, change_pct,
            )

        else:
            # 30s elapsed, no dip — check if still within +5% of signal price
            try:
                ticker = await self.options_exchange.fetch_ticker(selected_symbol)
                final_premium = ticker.get("last") or ticker.get("ask") or 0
            except Exception:
                final_premium = 0

            if final_premium <= 0:
                self.logger.info("[%s] PULLBACK TIMEOUT: no valid premium — skipping", self.pair)
                return []

            final_change = (final_premium - signal_premium) / signal_premium * 100
            if final_change > self.PULLBACK_SKIP_RISE_PCT:
                self.logger.info(
                    "[%s] PULLBACK TIMEOUT: premium +%.1f%% above signal — skipping",
                    self.pair, final_change,
                )
                return []

            entry_premium = final_premium
            self.logger.info(
                "[%s] PULLBACK TIMEOUT: no dip but within range (%+.1f%%) — entering at $%.4f",
                self.pair, final_change, entry_premium,
            )

        # Log to activity_log for dashboard
        await self._log_activity(
            "options_entry",
            f"{self.pair} — OPTIONS: {option_type.upper()} {strike_label} ${selected_strike:.0f} | "
            f"premium=${entry_premium:.4f} | expiry={expiry_str} | signals={strength}/4 {signals_str}",
            {"option_type": option_type, "strike": selected_strike, "premium": entry_premium,
             "strike_label": strike_label,
             "expiry": self._selected_expiry.isoformat() if self._selected_expiry else "",
             "strength": strength, "underlying_price": current_price,
             "symbol": selected_symbol, "setup_type": setup_type},
        )

        # Build and return entry signal
        return self._build_entry_signal(
            option_type=option_type,
            selected_symbol=selected_symbol,
            selected_strike=selected_strike,
            premium=entry_premium,
            strength=strength,
            signals_str=signals_str,
            current_price=current_price,
            setup_type=setup_type,
            expiry_str=expiry_str,
            strike_label=strike_label,
        )

    def _build_entry_signal(
        self,
        option_type: str,
        selected_symbol: str,
        selected_strike: float,
        premium: float,
        strength: int,
        signals_str: str,
        current_price: float,
        setup_type: str,
        expiry_str: str,
        strike_label: str,
    ) -> list[Signal]:
        """Build the entry Signal for an option trade."""
        reason = (
            f"OPTIONS {option_type.upper()} | {strength}/4 signals "
            f"({signals_str}) | "
            f"{strike_label} Strike=${selected_strike:.0f} "
            f"Exp={expiry_str} "
            f"Premium=${premium:.4f}"
        )
        self.logger.info("[%s] OPTIONS ENTRY — %s (setup=%s)", self.pair, reason, setup_type)

        return [Signal(
            side="buy",
            price=premium,
            amount=float(self.CONTRACTS_PER_TRADE),
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=selected_symbol,
            leverage=self.OPTIONS_LEVERAGE,
            position_type="long",
            exchange_id="delta",
            metadata={
                "pending_side": option_type,
                "pending_amount": float(self.CONTRACTS_PER_TRADE),
                "option_type": option_type,
                "strike": selected_strike,
                "strike_label": strike_label,
                "expiry": self._selected_expiry.isoformat() if self._selected_expiry else "",
                "underlying_price": current_price,
                "underlying_pair": self.pair,
                "tp_price": premium * (1 + self.TP_PREMIUM_GAIN_PCT / 100),
                "sl_price": premium * (1 - self.SL_PREMIUM_LOSS_PCT / 100),
                "setup_type": setup_type,
            },
        )]

    # ==================================================================
    # RATCHET FLOOR
    # ==================================================================

    def _update_opt_ratchet_floor(self, pnl_pct: float) -> None:
        """Ratchet profit floor — one-way lock based on premium peak."""
        for threshold, floor in self.OPT_RATCHET_FLOOR_TABLE:
            if pnl_pct >= threshold and floor > self._opt_ratchet_floor:
                self.logger.info(
                    "[%s] RATCHET FLOOR ↑ pnl +%.1f%% ≥ %+.0f%% → floor locked at +%.1f%%",
                    self.option_symbol, pnl_pct, threshold, floor,
                )
                self._opt_ratchet_floor = floor

    # ==================================================================
    # EXIT LOGIC
    # ==================================================================

    async def _check_option_exit(self) -> list[Signal]:
        """Check exit conditions for open option position.

        Phase 1 (first 30s after fill): only SL fires.
        After Phase 1:
        1. Expiry: Close 2 hours before expiry
        2. Ratchet floor: lock profit floor as premium rises
        3. SL: -30% premium drop (always active)
        4. Momentum Fade: profitable + momentum dying → exit
        5. Dead Momentum: losing + momentum dead 45s + held 3min → exit
        6. TP: +30% premium gain
        7. Trailing: activates at +15%, trails 5% behind peak
        8. Pullback: exit if lost 40% of peak gain (when peak was 8%+)
        9. Decay: exit if was +10%+ and faded to +3%
        10. Timeout: close after 5 minutes
        11. Signal reversal: opposite momentum
        """
        if not self.in_position or not self.option_symbol:
            return []

        # Position verification every 3rd tick (~30s)
        self._position_verify_tick += 1
        if self._position_verify_tick % 3 == 0:
            gone = await self._verify_option_position()
            if gone:
                return gone

        # Fetch current premium
        try:
            ticker = await self.options_exchange.fetch_ticker(self.option_symbol)
            current_premium = ticker.get("last") or ticker.get("bid") or 0
            self._consecutive_ticker_failures = 0  # reset on success
            if current_premium > 0:
                self._last_known_premium = current_premium  # track for POSITION_GONE exit
        except Exception as e:
            self._consecutive_ticker_failures += 1
            now_utc = datetime.now(timezone.utc)

            # Near/past expiry + ticker fail → treat as expired (position gone)
            if self.expiry_dt:
                mins_to_expiry = (self.expiry_dt - now_utc).total_seconds() / 60
                if mins_to_expiry <= self._EXPIRY_CLOSE_MINUTES:
                    self.logger.warning(
                        "[%s] Ticker failed near expiry (%.1f min) — marking POSITION_GONE",
                        self.option_symbol, mins_to_expiry,
                    )
                    return await self._handle_position_gone("EXPIRED_TICKER_FAIL")

            # Too many consecutive failures → position likely delisted/gone
            if self._consecutive_ticker_failures >= self._MAX_TICKER_FAILURES:
                self.logger.warning(
                    "[%s] %d consecutive ticker failures — marking POSITION_GONE",
                    self.option_symbol, self._consecutive_ticker_failures,
                )
                return await self._handle_position_gone("TICKER_FAIL_REPEATED")

            self.logger.warning(
                "[%s] Failed to fetch option ticker (%d/%d): %s",
                self.option_symbol, self._consecutive_ticker_failures,
                self._MAX_TICKER_FAILURES, e,
            )
            return []

        if current_premium <= 0:
            # May have expired worthless
            if self.expiry_dt and datetime.now(timezone.utc) >= self.expiry_dt:
                return await self._do_option_exit(0, -100.0, "EXPIRED_WORTHLESS")
            return []

        # Track peak premium
        self.highest_premium = max(self.highest_premium, current_premium)

        # Write position state to trades table every tick (~10s)
        # so dashboard shows live P&L for options positions
        await self._update_position_state_in_db(current_premium)

        # P&L
        premium_change_pct = (
            (current_premium - self.entry_premium) / self.entry_premium * 100
        ) if self.entry_premium > 0 else 0

        peak_pnl_pct = (
            (self.highest_premium - self.entry_premium) / self.entry_premium * 100
        ) if self.entry_premium > 0 else 0

        hold_seconds = time.monotonic() - self.entry_time
        in_phase1 = hold_seconds < self.PHASE1_HANDS_OFF_SEC

        # Heartbeat (every ~60s)
        if self._tick_count % 6 == 0:
            trail_tag = " [TRAILING]" if self._trailing_active else ""
            phase_tag = " [PHASE1]" if in_phase1 else ""
            self.logger.info(
                "[%s] %s | $%.4f → $%.4f (%+.1f%%) | peak=$%.4f (+%.1f%%) | %ds%s%s",
                self.option_symbol, self.option_side,
                self.entry_premium, current_premium, premium_change_pct,
                self.highest_premium, peak_pnl_pct,
                int(hold_seconds), trail_tag, phase_tag,
            )

        # ── 1. EXPIRY EXIT: close 2 hours before expiry ──────────────
        if self.expiry_dt:
            time_to_expiry = (self.expiry_dt - datetime.now(timezone.utc)).total_seconds()
            close_threshold = self.CLOSE_BEFORE_EXPIRY_HOURS * 3600
            if time_to_expiry <= close_threshold:
                self.logger.info(
                    "[%s] EXPIRY in %.1fh — closing option",
                    self.option_symbol, time_to_expiry / 3600,
                )
                return await self._do_option_exit(current_premium, premium_change_pct, "EXPIRY_CLOSE")

        # ── Ratchet floor update (always, before any exit checks) ─────
        self._update_opt_ratchet_floor(premium_change_pct)

        # ── 2a. RATCHET EXIT: premium fell below locked floor ─────────
        if self._opt_ratchet_floor > 0 and premium_change_pct < self._opt_ratchet_floor:
            self.logger.info(
                "[%s] OPT_RATCHET — pnl +%.1f%% fell below floor +%.1f%%",
                self.option_symbol, premium_change_pct, self._opt_ratchet_floor,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "OPT_RATCHET")

        # ── 2b. STOP LOSS: -30% premium drop (always active, even Phase 1)
        if premium_change_pct <= -self.SL_PREMIUM_LOSS_PCT:
            self.logger.info(
                "[%s] OPTION SL — premium %+.1f%% ($%.4f → $%.4f)",
                self.option_symbol, premium_change_pct,
                self.entry_premium, current_premium,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "OPT_SL")

        # ── Phase 1 hands-off: only SL fires in first 30s ────────────
        if in_phase1:
            return []

        # ── 3. MOMENTUM FADE: profitable + momentum dying ─────────────
        momentum_60s = 0.0
        if self._scalp and hasattr(self._scalp, "last_signal_state"):
            ss = self._scalp.last_signal_state
            if ss:
                momentum_60s = abs(ss.get("momentum_60s", 0) or 0)

        if hold_seconds >= self.OPT_MOM_FADE_MIN_HOLD and premium_change_pct > 0:
            if momentum_60s < self.OPT_MOM_FADE_THRESHOLD:
                now_m = time.monotonic()
                if self._opt_mom_fade_since is None:
                    self._opt_mom_fade_since = now_m
                # Trend-aligned positions get longer leash
                trend = self._scalp._get_15m_trend() if self._scalp else "neutral"
                trend_aligned = (
                    (self.option_side == "call" and trend == "bullish")
                    or (self.option_side == "put" and trend == "bearish")
                )
                if trend_aligned:
                    confirm_sec = self.OPT_MOM_FADE_TREND_CONFIRM
                    min_hold = self.OPT_MOM_FADE_TREND_HOLD
                else:
                    confirm_sec = self.OPT_MOM_FADE_CONFIRM_SEC
                    min_hold = self.OPT_MOM_FADE_MIN_HOLD

                elapsed = now_m - self._opt_mom_fade_since
                if elapsed >= confirm_sec and hold_seconds >= min_hold:
                    self.logger.info(
                        "[%s] OPT_MOMENTUM_FADE — profitable +%.1f%% but mom=%.4f%% dead %.0fs (aligned=%s)",
                        self.option_symbol, premium_change_pct, momentum_60s,
                        elapsed, trend_aligned,
                    )
                    return await self._do_option_exit(current_premium, premium_change_pct, "OPT_MOMENTUM_FADE")
            else:
                self._opt_mom_fade_since = None

        # ── 4. DEAD MOMENTUM: losing + momentum dead + held too long ──
        if hold_seconds >= self.OPT_DEAD_MOM_MIN_HOLD and premium_change_pct < 0:
            if momentum_60s < self.OPT_MOM_FADE_THRESHOLD:
                now_m = time.monotonic()
                if self._opt_mom_dying_since is None:
                    self._opt_mom_dying_since = now_m
                dead_elapsed = now_m - self._opt_mom_dying_since
                if dead_elapsed >= self.OPT_DEAD_MOM_CONFIRM_SEC:
                    self.logger.info(
                        "[%s] OPT_DEAD_MOMENTUM — losing %.1f%% + mom dead %.0fs + held %ds",
                        self.option_symbol, premium_change_pct, dead_elapsed, int(hold_seconds),
                    )
                    return await self._do_option_exit(current_premium, premium_change_pct, "OPT_DEAD_MOMENTUM")
            else:
                self._opt_mom_dying_since = None

        # ── 5. TAKE PROFIT: +30% premium gain ────────────────────────
        if premium_change_pct >= self.TP_PREMIUM_GAIN_PCT:
            self.logger.info(
                "[%s] OPTION TP — premium +%.1f%% ($%.4f → $%.4f)",
                self.option_symbol, premium_change_pct,
                self.entry_premium, current_premium,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "TP")

        # ── 6. TRAILING activation at +15% ───────────────────────────
        if premium_change_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
            self._trailing_active = True
            self.logger.info(
                "[%s] OPTION TRAIL ON at +%.1f%%", self.option_symbol, premium_change_pct,
            )

        # ── 7. TRAILING STOP: 5% below peak premium ─────────────────
        if self._trailing_active:
            trail_floor = self.highest_premium * (1 - self.TRAILING_DISTANCE_PCT / 100)
            if current_premium <= trail_floor:
                final_pct = (current_premium - self.entry_premium) / self.entry_premium * 100
                self.logger.info(
                    "[%s] OPTION TRAIL HIT — peak=$%.4f floor=$%.4f now=$%.4f",
                    self.option_symbol, self.highest_premium, trail_floor, current_premium,
                )
                return await self._do_option_exit(current_premium, final_pct, "OPT_TRAIL")

        # ── 6. PULLBACK: exit if lost 40% of peak gain (peak was 8%+)
        if peak_pnl_pct >= self.PULLBACK_ACTIVATE_PCT and premium_change_pct > 0:
            pct_of_peak_lost = ((peak_pnl_pct - premium_change_pct) / peak_pnl_pct) * 100
            if pct_of_peak_lost >= self.PULLBACK_EXIT_PCT:
                self.logger.info(
                    "[%s] OPTION PULLBACK — peak +%.1f%% now +%.1f%% (lost %.0f%% of gain)",
                    self.option_symbol, peak_pnl_pct, premium_change_pct, pct_of_peak_lost,
                )
                return await self._do_option_exit(current_premium, premium_change_pct, "PULLBACK")

        # ── 7. DECAY: was +10%+ and faded to +3% ─────────────────────
        if peak_pnl_pct >= 10.0 and premium_change_pct <= self.DECAY_THRESHOLD_PCT:
            self.logger.info(
                "[%s] OPTION DECAY — peak +%.1f%% faded to +%.1f%% (threshold +%.1f%%)",
                self.option_symbol, peak_pnl_pct, premium_change_pct, self.DECAY_THRESHOLD_PCT,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "DECAY")

        # ── 8. TIMEOUT: close after 5 minutes ────────────────────────
        if hold_seconds >= self.TIMEOUT_MINUTES * 60:
            self.logger.info(
                "[%s] OPTION TIMEOUT — held %dm (limit %dm) at %+.1f%%",
                self.option_symbol, int(hold_seconds / 60),
                self.TIMEOUT_MINUTES, premium_change_pct,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "OPT_TIMEOUT")

        # ── 9. SIGNAL REVERSAL ────────────────────────────────────────
        if self._scalp and hasattr(self._scalp, "last_signal_state"):
            ss = self._scalp.last_signal_state
            if ss:
                new_side = ss.get("side")
                new_strength = ss.get("strength", 0)
                signal_age = time.monotonic() - ss.get("timestamp", 0)

                if (signal_age < self.SIGNAL_STALENESS_SEC
                        and new_strength >= self.MIN_SIGNAL_STRENGTH
                        and new_side is not None):
                    is_reversal = (
                        (self.option_side == "call" and new_side == "short")
                        or (self.option_side == "put" and new_side == "long")
                    )
                    if is_reversal:
                        self.logger.info(
                            "[%s] SIGNAL REVERSAL — %s → opposite %s at %+.1f%%",
                            self.option_symbol, self.option_side,
                            new_side, premium_change_pct,
                        )
                        return await self._do_option_exit(
                            current_premium, premium_change_pct, "OPT_REVERSAL",
                        )

        return []

    # ==================================================================
    # EXIT SIGNAL BUILDER
    # ==================================================================

    async def _do_option_exit(
        self, current_premium: float, pnl_pct: float, exit_type: str,
    ) -> list[Signal]:
        """Build exit signal for option position."""
        pnl_usd = (current_premium - self.entry_premium) * self.CONTRACTS_PER_TRADE
        reason = (
            f"Option {exit_type} {self.option_side} | "
            f"${self.entry_premium:.4f} → ${current_premium:.4f} "
            f"({pnl_pct:+.1f}%) P&L=${pnl_usd:+.4f}"
        )
        self.logger.info("[%s] OPTIONS EXIT — %s", self.option_symbol, reason)

        # Log to activity_log for dashboard
        pnl_tag = f"+${pnl_usd:.4f}" if pnl_usd >= 0 else f"-${abs(pnl_usd):.4f}"
        await self._log_activity(
            "options_exit",
            f"{self.pair} — OPTIONS EXIT: {exit_type} {self.option_side} | "
            f"${self.entry_premium:.4f} -> ${current_premium:.4f} ({pnl_pct:+.1f}%) {pnl_tag}",
            {"exit_type": exit_type, "option_side": self.option_side,
             "entry_premium": self.entry_premium, "exit_premium": current_premium,
             "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4),
             "strike": self.strike_price, "symbol": self.option_symbol},
        )

        # Reset momentum / ratchet state
        self._opt_mom_fade_since = None
        self._opt_mom_dying_since = None
        self._opt_ratchet_floor = 0.0

        # Stats
        if pnl_pct >= 0:
            self.hourly_wins += 1
        else:
            self.hourly_losses += 1
        self.hourly_pnl += pnl_usd

        # Immediately clear dashboard position state so UI doesn't show stale "OPEN"
        await self._clear_dashboard_position(exit_type, pnl_pct, pnl_usd)

        return [Signal(
            side="sell",
            price=current_premium,
            amount=float(self.CONTRACTS_PER_TRADE),
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.option_symbol or self.pair,
            leverage=self.OPTIONS_LEVERAGE,
            position_type="long",
            reduce_only=True,
            exchange_id="delta",
        )]

    async def _verify_option_position(self) -> list[Signal] | None:
        """Check exchange positions to detect if option is still open.

        Called every 3rd tick (~30s). Returns _handle_position_gone result
        if position no longer exists, or None to continue normal exit checks.
        """
        if not self.options_exchange or not self.option_symbol:
            return None

        try:
            positions = await self.options_exchange.fetch_positions()
            for pos in positions:
                symbol = pos.get("symbol", "")
                contracts = float(pos.get("contracts", 0) or 0)
                if symbol == self.option_symbol and contracts != 0:
                    return None  # Position still exists — all good

            # Position not found on exchange
            now_utc = datetime.now(timezone.utc)
            if self.expiry_dt:
                mins_to_expiry = (self.expiry_dt - now_utc).total_seconds() / 60
                if mins_to_expiry <= self._EXPIRY_CLOSE_MINUTES:
                    self.logger.warning(
                        "[%s] POSITION VERIFY: not found, near expiry (%.1f min) — EXPIRY",
                        self.option_symbol, mins_to_expiry,
                    )
                    return await self._handle_position_gone("VERIFY_EXPIRY")

            self.logger.warning(
                "[%s] POSITION VERIFY: not found on exchange — POSITION_GONE",
                self.option_symbol,
            )
            return await self._handle_position_gone("VERIFY_GONE")

        except Exception as e:
            # fetch_positions failed — don't flag as gone, just log
            self.logger.debug(
                "[%s] Position verify fetch_positions failed: %s", self.option_symbol, e,
            )
            return None

    async def _handle_position_gone(self, reason: str) -> list[Signal]:
        """Handle a position that no longer exists on exchange.

        Determines if the contract expired or vanished unexpectedly.
        Uses last known premium as exit price, marks trade closed in DB,
        sends Telegram alert, applies 60s cooldown. No retry.
        """
        # Determine if this is an expiry or unexpected disappearance
        is_expiry = False
        if self.expiry_dt:
            time_past_expiry = (datetime.now(timezone.utc) - self.expiry_dt).total_seconds()
            is_expiry = time_past_expiry >= 0  # at or past expiry time

        exit_reason = "EXPIRY" if is_expiry else "POSITION_GONE"
        exit_reason_detail = f"{exit_reason}_{reason}" if reason else exit_reason

        # Use last known premium as exit price (tracked every tick)
        exit_premium = self._last_known_premium
        if exit_premium <= 0:
            exit_premium = self.entry_premium * 0.5 if self.entry_premium > 0 else 0.0

        # For expired contracts that went to zero, use 0
        if is_expiry and reason == "EXPIRED_TICKER_FAIL":
            exit_premium = 0.0

        self.logger.info(
            "[%s] %s (%s) — exit_premium=$%.4f (last_known=$%.4f entry=$%.4f)",
            self.option_symbol, exit_reason, reason,
            exit_premium, self._last_known_premium, self.entry_premium,
        )

        # Calculate P&L
        pnl_pct = 0.0
        pnl_usd = 0.0
        if self.entry_premium > 0:
            pnl_pct = (exit_premium - self.entry_premium) / self.entry_premium * 100
            pnl_usd = (exit_premium - self.entry_premium) * self.CONTRACTS_PER_TRADE

        # Mark trade closed in DB directly (no exchange order needed)
        if self._db:
            try:
                from alpha.utils import iso_now
                open_trade = await self._db.get_open_trade(
                    pair=self.option_symbol or self.pair,
                    exchange="delta",
                    strategy="options_scalp",
                )
                if open_trade:
                    from alpha.trade_executor import calc_pnl
                    entry_price = float(open_trade.get("entry_price", self.entry_premium) or self.entry_premium)
                    amount = open_trade.get("amount", self.CONTRACTS_PER_TRADE)
                    leverage = open_trade.get("leverage", self.OPTIONS_LEVERAGE) or 1

                    result = calc_pnl(
                        entry_price, exit_premium, amount,
                        "long", leverage,
                        "delta", self.option_symbol or self.pair,
                    )

                    await self._db.update_trade(open_trade["id"], {
                        "status": "closed",
                        "exit_price": exit_premium,
                        "closed_at": iso_now(),
                        "pnl": round(result.net_pnl, 8),
                        "pnl_pct": round(result.pnl_pct, 4),
                        "reason": exit_reason_detail.lower(),
                        "exit_reason": exit_reason,
                        "position_state": None,
                    })
                    pnl_pct = result.pnl_pct
                    pnl_usd = result.net_pnl
                    self.logger.info(
                        "[%s] Trade %s closed as %s — exit=$%.4f P&L=$%.4f (%.2f%%)",
                        self.option_symbol, open_trade["id"], exit_reason,
                        exit_premium, result.net_pnl, result.pnl_pct,
                    )
                else:
                    self.logger.info(
                        "[%s] No open trade found in DB — already closed", self.option_symbol,
                    )
            except Exception:
                self.logger.exception("[%s] Failed to close trade as %s", self.option_symbol, exit_reason)

        # Send Telegram alert
        try:
            alerts = getattr(self.executor, "alerts", None)
            if alerts is not None:
                pair_short = self._base_asset
                pnl_tag = f"+${pnl_usd:.4f}" if pnl_usd >= 0 else f"-${abs(pnl_usd):.4f}"
                if is_expiry:
                    msg = (
                        f"\u23f0 {pair_short} option expired\n"
                        f"{self.option_side} ${self.strike_price:.0f} | "
                        f"${self.entry_premium:.4f} \u2192 ${exit_premium:.4f} "
                        f"({pnl_pct:+.1f}%) {pnl_tag}"
                    )
                else:
                    msg = (
                        f"\u2139\ufe0f {pair_short} option position gone\n"
                        f"{self.option_side} ${self.strike_price:.0f} | "
                        f"${self.entry_premium:.4f} \u2192 ${exit_premium:.4f} "
                        f"({pnl_pct:+.1f}%) {pnl_tag}\n"
                        f"Closed in DB, no action needed."
                    )
                await alerts.send_text(msg)
        except Exception:
            self.logger.debug("[%s] Failed to send %s Telegram alert", self.option_symbol, exit_reason)

        # Log to activity feed
        await self._log_activity(
            f"options_{exit_reason.lower()}",
            f"{self.pair} — OPTIONS {exit_reason}: {reason} | "
            f"{self.option_side} strike=${self.strike_price:.0f} | "
            f"exit=${exit_premium:.4f} P&L={pnl_pct:+.1f}% ${pnl_usd:+.4f}",
            {"reason": reason, "exit_reason": exit_reason,
             "option_side": self.option_side,
             "strike": self.strike_price, "symbol": self.option_symbol,
             "exit_premium": exit_premium,
             "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 4)},
        )

        # Stats
        if pnl_pct >= 0:
            self.hourly_wins += 1
        else:
            self.hourly_losses += 1
        self.hourly_pnl += pnl_usd

        # Clear dashboard + position state
        await self._clear_dashboard_position(exit_reason_detail, pnl_pct, pnl_usd)

        # Apply 60s cooldown before next options entry
        self._position_gone_cooldown_until = time.monotonic() + self._POSITION_GONE_COOLDOWN_SEC
        self.logger.info(
            "[%s] %s cooldown: no new options entries for %ds",
            self.pair, exit_reason, self._POSITION_GONE_COOLDOWN_SEC,
        )

        # Clear all position state — no retry, we're done
        self.in_position = False
        self.option_side = None
        self.option_symbol = None
        self.entry_premium = 0.0
        self.highest_premium = 0.0
        self._last_known_premium = 0.0
        self._trailing_active = False
        self.strike_price = 0.0
        self.expiry_dt = None
        self._consecutive_ticker_failures = 0
        self._last_state_write = 0.0

        return []  # No signal needed — handled directly in DB

    async def _clear_dashboard_position(
        self, exit_type: str = "", pnl_pct: float = 0.0, pnl_usd: float = 0.0,
    ) -> None:
        """Write a final options_state update that clears all position fields.

        Called on exit so the dashboard immediately shows 'No Position'
        instead of stale 'CALL OPEN'.
        """
        if not self._db:
            return

        # Build state with position fields explicitly nulled
        # Keep market data (spot, expiry, premiums) intact for display
        signal_strength = 0
        signal_side: str | None = None
        signal_reason = ""
        spot_price = 0.0

        if self._scalp and hasattr(self._scalp, "last_signal_state"):
            ss = self._scalp.last_signal_state
            if ss:
                signal_strength = ss.get("strength", 0)
                signal_side = ss.get("side")
                signal_reason = ss.get("reason", "")
                spot_price = ss.get("current_price", 0)

        state = {
            "spot_price": spot_price or None,
            "expiry": self._selected_expiry.isoformat() if self._selected_expiry else None,
            "expiry_label": None,
            "atm_strike": None,
            "call_premium": None,
            "put_premium": None,
            "signal_strength": signal_strength,
            "signal_side": signal_side,
            "signal_reason": signal_reason,
            # Position fields: ALL cleared
            "position_side": None,
            "position_strike": None,
            "position_symbol": None,
            "entry_premium": None,
            "current_premium": None,
            "pnl_pct": None,
            "pnl_usd": None,
            "trailing_active": False,
            "highest_premium": None,
            # Exit info for dashboard (last exit summary)
            "last_exit_type": exit_type,
            "last_exit_pnl_pct": round(pnl_pct, 2),
            "last_exit_pnl_usd": round(pnl_usd, 4),
        }

        try:
            await self._db.upsert_options_state(self.pair, state)
            self.logger.info(
                "[%s] Dashboard options state cleared (exit=%s pnl=%+.1f%%)",
                self.pair, exit_type, pnl_pct,
            )
        except Exception as e:
            self.logger.warning("[%s] Failed to clear dashboard options state: %s", self.pair, e)

    # ==================================================================
    # CALLBACKS
    # ==================================================================

    def on_fill(self, signal: Signal, order: dict) -> None:
        """Track option position state on fill."""
        pending_side = signal.metadata.get("pending_side")
        if pending_side:
            # Entry fill
            fill_price = order.get("average") or order.get("price") or signal.price
            self.in_position = True
            self.option_side = pending_side
            self.option_symbol = signal.pair
            self.entry_premium = fill_price
            self.entry_time = time.monotonic()
            self.highest_premium = fill_price
            self._last_known_premium = fill_price
            self._trailing_active = False
            self._consecutive_ticker_failures = 0
            # Reset momentum / ratchet state on entry
            self._opt_mom_fade_since = None
            self._opt_mom_dying_since = None
            self._opt_ratchet_floor = 0.0
            self.strike_price = signal.metadata.get("strike", 0)
            expiry_str = signal.metadata.get("expiry")
            if expiry_str:
                self.expiry_dt = datetime.fromisoformat(expiry_str)
            self.logger.info(
                "[%s] OPTION FILLED — %s strike=$%.0f premium=$%.4f exp=%s",
                self.option_symbol, self.option_side,
                self.strike_price, fill_price,
                self.expiry_dt.strftime("%b %d %H:%M") if self.expiry_dt else "?",
            )
        else:
            # Exit fill
            self.logger.info(
                "[%s] OPTION EXIT FILLED — %s closed",
                self.option_symbol or self.pair, self.option_side,
            )
            self.in_position = False
            self.option_side = None
            self.option_symbol = None
            self.entry_premium = 0.0
            self.highest_premium = 0.0
            self._trailing_active = False
            self.strike_price = 0.0
            self.expiry_dt = None
            # Force next check() to immediately write cleared state to dashboard
            self._last_state_write = 0.0

    def on_rejected(self, signal: Signal) -> None:
        """Handle rejected option orders.

        For entry: just log (no state to clear).
        For exit: clear in_position so we don't keep generating exit signals
        for a position the exchange no longer has. The trade was already
        marked closed in DB by _mark_position_gone.
        """
        pending_side = signal.metadata.get("pending_side")
        if pending_side:
            self.logger.warning(
                "[%s] Option entry REJECTED — not tracking", signal.pair,
            )
        elif signal.reduce_only and self.in_position:
            # Exit was rejected (position likely already gone on exchange)
            self.logger.warning(
                "[%s] Option EXIT rejected — clearing in_position (position likely closed externally)",
                self.option_symbol or signal.pair,
            )
            self.in_position = False
            self.option_side = None
            self.option_symbol = None
            self.entry_premium = 0.0
            self.highest_premium = 0.0
            self._trailing_active = False
            self.strike_price = 0.0
            self.expiry_dt = None
            self._last_state_write = 0.0  # Force dashboard state clear on next tick

    # ==================================================================
    # STATS
    # ==================================================================

    def reset_hourly_stats(self) -> dict[str, Any]:
        """Return stats and reset counters."""
        stats = {
            "wins": self.hourly_wins,
            "losses": self.hourly_losses,
            "pnl": self.hourly_pnl,
            "in_position": self.in_position,
            "option_side": self.option_side,
            "option_symbol": self.option_symbol,
        }
        self.hourly_wins = 0
        self.hourly_losses = 0
        self.hourly_pnl = 0.0
        return stats
