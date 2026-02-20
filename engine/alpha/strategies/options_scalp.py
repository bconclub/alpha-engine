"""Alpha Options Scalp — Buy CALLs/PUTs on strong momentum signals.

PHILOSOPHY: Options are the safest momentum play. Max loss = premium paid.
No leverage, no liquidation. When scalp sees 3/4+ momentum, buy the option.

Entry: 3-of-4 or 4-of-4 momentum signals from scalp strategy
       Bullish → buy CALL, Bearish → buy PUT
       1 contract per trade, ATM or 1-strike OTM

Exit:
  - TP: 100% premium gain (premium doubles)
  - SL: 50% premium loss (premium halves)
  - Trailing: activates at 50% gain, trails 30% behind peak
  - Time: close 2 hours before expiry
  - Signal reversal: close if opposite momentum fires
  - Check every 10 seconds

Position Sizing:
  - Max 20% of capital on options ($2 max)
  - Max 1 option position at a time
  - Premium must be $0.01 to $2.00

Risk: Max loss = premium paid. No liquidation. Safest momentum play.

I use options to amplify strong momentum signals. Options give me asymmetric
risk — I can lose only what I pay, but win multiples. I only buy options on
3/4+ signals. I am a buyer, never a seller of options.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.db import Database
from alpha.strategies.base import BaseStrategy, Signal, StrategyName
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
    Only enters on 3-of-4 or 4-of-4 signals. Max loss = premium paid.
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

    # ── Premium limits ────────────────────────────────────────────
    MAX_PREMIUM_CAPITAL_PCT = 20.0       # Max 20% of capital
    MAX_PREMIUM_USD = 2.00               # Hard cap $2
    MIN_PREMIUM_USD = 0.01               # Skip illiquid < $0.01

    # ── Entry ─────────────────────────────────────────────────────
    MIN_SIGNAL_STRENGTH = 3              # 3-of-4 or 4-of-4 required
    SIGNAL_STALENESS_SEC = 30            # Signal must be < 30s old
    CONTRACTS_PER_TRADE = 1              # 1 contract per trade

    # ── Exit thresholds ───────────────────────────────────────────
    TP_PREMIUM_GAIN_PCT = 100.0          # 100% gain (premium doubles)
    SL_PREMIUM_LOSS_PCT = 50.0           # 50% loss (premium halves)
    TRAILING_ACTIVATE_PCT = 50.0         # Activate trail at +50%
    TRAILING_DISTANCE_PCT = 30.0         # Trail 30% behind peak

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
        """Load option markets on startup."""
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
        self.logger.info(
            "[%s] OPTIONS SCALP ACTIVE — min_strength=%d, "
            "TP=%d%% SL=%d%% Trail=%d%%/%d%% MaxPremium=$%.2f",
            self.pair, self.MIN_SIGNAL_STRENGTH,
            int(self.TP_PREMIUM_GAIN_PCT), int(self.SL_PREMIUM_LOSS_PCT),
            int(self.TRAILING_ACTIVATE_PCT), int(self.TRAILING_DISTANCE_PCT),
            self.MAX_PREMIUM_USD,
        )

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

    def _find_strike(
        self, current_price: float, option_type: str,
    ) -> tuple[float, float] | None:
        """Find ATM and OTM strike for the given option type.

        Returns (atm_strike, otm_strike) or None if no valid strikes.
        ATM: Nearest available strike to current price
        OTM call: 1 strike above ATM, OTM put: 1 strike below ATM
        """
        if not self._available_strikes:
            return None

        # ATM: nearest strike to spot
        atm_strike = min(self._available_strikes, key=lambda s: abs(s - current_price))

        # OTM: 1 strike away in the out-of-money direction
        if option_type == "call":
            otm_candidates = [s for s in self._available_strikes if s > atm_strike]
            otm_strike = min(otm_candidates) if otm_candidates else atm_strike
        else:
            otm_candidates = [s for s in self._available_strikes if s < atm_strike]
            otm_strike = max(otm_candidates) if otm_candidates else atm_strike

        self.logger.debug(
            "[%s] %s spot $%.0f → ATM $%.0f, OTM $%.0f",
            self.pair, self._base_asset, current_price, atm_strike, otm_strike,
        )
        return (atm_strike, otm_strike)

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
        # 1. Read scalp strategy's latest signal
        if not self._scalp or not hasattr(self._scalp, "last_signal_state"):
            if self._tick_count % 30 == 0:
                self.logger.info("[%s] OPTIONS: no scalp ref (scalp=%s)", self.pair, bool(self._scalp))
            return []

        signal_state = self._scalp.last_signal_state

        # Log full signal check on every tick for debugging
        if signal_state is not None:
            self.logger.info(
                "[%s] OPTIONS CHECK: strength=%s side=%s age=%.1fs reason=%s",
                self.pair,
                signal_state.get("strength", 0),
                signal_state.get("side"),
                time.monotonic() - signal_state.get("timestamp", 0),
                (signal_state.get("reason", "") or "")[:60],
            )
        else:
            if self._tick_count % 6 == 0:
                self.logger.info("[%s] OPTIONS: signal_state is None", self.pair)
            return []

        # 2. Check signal freshness
        signal_age = time.monotonic() - signal_state.get("timestamp", 0)
        if signal_age > self.SIGNAL_STALENESS_SEC:
            self.logger.info(
                "[%s] OPTIONS STALE: age=%.1fs > %ds — skipping",
                self.pair, signal_age, self.SIGNAL_STALENESS_SEC,
            )
            return []

        # 3. Check signal strength (3-of-4 minimum)
        strength = signal_state.get("strength", 0)
        if strength < self.MIN_SIGNAL_STRENGTH:
            if self._tick_count % 6 == 0:
                self.logger.info(
                    "[%s] OPTIONS WEAK: strength=%d < %d — waiting",
                    self.pair, strength, self.MIN_SIGNAL_STRENGTH,
                )
            return []

        side = signal_state.get("side")
        if side is None:
            self.logger.info("[%s] OPTIONS: 3/4+ but side=None — skipping", self.pair)
            return []

        # 4. Determine option type
        option_type = "call" if side == "long" else "put"
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

        # 7. Find strike
        strikes = self._find_strike(current_price, option_type)
        if strikes is None:
            if self._tick_count % 30 == 0:
                self.logger.info("[%s] No valid strikes found", self.pair)
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: no valid strikes for {option_type.upper()}",
                {"option_type": option_type, "strength": strength, "price": current_price},
            )
            return []

        atm_strike, _otm_strike = strikes

        # 8. Build option symbol (prefer ATM for liquidity)
        symbol = self._build_option_symbol(atm_strike, option_type, self._selected_expiry)
        if symbol is None:
            return []

        # 9. Fetch current premium
        try:
            ticker = await self.options_exchange.fetch_ticker(symbol)
            premium = ticker.get("last") or ticker.get("ask") or 0
        except Exception as e:
            self.logger.debug("[%s] Ticker fetch failed for %s: %s", self.pair, symbol, e)
            return []

        if premium <= 0:
            return []

        # 10. Premium checks
        exchange_capital = self.risk_manager.get_exchange_capital(self._exchange_id)
        max_premium = min(
            exchange_capital * (self.MAX_PREMIUM_CAPITAL_PCT / 100),
            self.MAX_PREMIUM_USD,
        )

        if premium > max_premium:
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Premium $%.4f > max $%.2f — skipping %s",
                    self.pair, premium, max_premium, symbol,
                )
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: premium ${premium:.2f} > ${max_premium:.2f} cap",
                {"option_type": option_type, "strike": atm_strike, "premium": premium,
                 "max_premium": max_premium, "strength": strength},
            )
            return []

        if premium < self.MIN_PREMIUM_USD:
            if self._tick_count % 30 == 0:
                self.logger.info(
                    "[%s] Premium $%.4f < min $%.2f — illiquid %s",
                    self.pair, premium, self.MIN_PREMIUM_USD, symbol,
                )
            await self._log_skip(
                f"{self.pair} — OPTIONS SKIP: premium ${premium:.4f} illiquid (min ${self.MIN_PREMIUM_USD})",
                {"option_type": option_type, "strike": atm_strike, "premium": premium, "strength": strength},
            )
            return []

        # 11. Build entry signal
        signals_str = signal_state.get("reason", "")
        expiry_str = self._selected_expiry.strftime('%b %d %H:%M')
        reason = (
            f"OPTIONS {option_type.upper()} | {strength}/4 signals "
            f"({signals_str}) | "
            f"Strike=${atm_strike:.0f} "
            f"Exp={expiry_str} "
            f"Premium=${premium:.4f}"
        )
        self.logger.info("[%s] OPTIONS ENTRY — %s", self.pair, reason)

        # Log to activity_log for dashboard
        await self._log_activity(
            "options_entry",
            f"{self.pair} — OPTIONS: {option_type.upper()} ATM ${atm_strike:.0f} | "
            f"premium=${premium:.4f} | expiry={expiry_str} | signals={strength}/4 {signals_str}",
            {"option_type": option_type, "strike": atm_strike, "premium": premium,
             "expiry": self._selected_expiry.isoformat(), "strength": strength,
             "underlying_price": current_price, "symbol": symbol},
        )

        return [Signal(
            side="buy",
            price=premium,
            amount=float(self.CONTRACTS_PER_TRADE),
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=symbol,
            leverage=1,
            position_type="long",
            exchange_id="delta",
            metadata={
                "pending_side": option_type,
                "pending_amount": float(self.CONTRACTS_PER_TRADE),
                "option_type": option_type,
                "strike": atm_strike,
                "expiry": self._selected_expiry.isoformat(),
                "underlying_price": current_price,
                "underlying_pair": self.pair,
                "tp_price": premium * (1 + self.TP_PREMIUM_GAIN_PCT / 100),
                "sl_price": premium * (1 - self.SL_PREMIUM_LOSS_PCT / 100),
            },
        )]

    # ==================================================================
    # EXIT LOGIC
    # ==================================================================

    async def _check_option_exit(self) -> list[Signal]:
        """Check exit conditions for open option position.

        Exit conditions:
        1. Time: Close 2 hours before expiry
        2. SL: 50% premium loss
        3. TP: 100% premium gain → activates trailing
        4. Trailing: 50% activate, 30% behind peak
        5. Signal reversal: opposite 3/4+ momentum
        """
        if not self.in_position or not self.option_symbol:
            return []

        # Fetch current premium
        try:
            ticker = await self.options_exchange.fetch_ticker(self.option_symbol)
            current_premium = ticker.get("last") or ticker.get("bid") or 0
        except Exception as e:
            self.logger.warning(
                "[%s] Failed to fetch option ticker: %s", self.option_symbol, e,
            )
            return []

        if current_premium <= 0:
            # May have expired worthless
            if self.expiry_dt and datetime.now(timezone.utc) >= self.expiry_dt:
                return await self._do_option_exit(0, -100.0, "EXPIRED_WORTHLESS")
            return []

        # Track peak premium
        self.highest_premium = max(self.highest_premium, current_premium)

        # P&L
        premium_change_pct = (
            (current_premium - self.entry_premium) / self.entry_premium * 100
        ) if self.entry_premium > 0 else 0

        hold_seconds = time.monotonic() - self.entry_time

        # Heartbeat (every ~60s)
        if self._tick_count % 6 == 0:
            trail_tag = " [TRAILING]" if self._trailing_active else ""
            self.logger.info(
                "[%s] %s | $%.4f → $%.4f (%+.1f%%) | peak=$%.4f | %ds%s",
                self.option_symbol, self.option_side,
                self.entry_premium, current_premium, premium_change_pct,
                self.highest_premium, int(hold_seconds), trail_tag,
            )

        # ── 1. TIME EXIT: close 2 hours before expiry ────────────────
        if self.expiry_dt:
            time_to_expiry = (self.expiry_dt - datetime.now(timezone.utc)).total_seconds()
            close_threshold = self.CLOSE_BEFORE_EXPIRY_HOURS * 3600
            if time_to_expiry <= close_threshold:
                self.logger.info(
                    "[%s] EXPIRY in %.1fh — closing option",
                    self.option_symbol, time_to_expiry / 3600,
                )
                return await self._do_option_exit(current_premium, premium_change_pct, "EXPIRY_CLOSE")

        # ── 2. STOP LOSS: 50% premium loss ───────────────────────────
        if premium_change_pct <= -self.SL_PREMIUM_LOSS_PCT:
            self.logger.info(
                "[%s] OPTION SL — premium %+.1f%% ($%.4f → $%.4f)",
                self.option_symbol, premium_change_pct,
                self.entry_premium, current_premium,
            )
            return await self._do_option_exit(current_premium, premium_change_pct, "SL")

        # ── 3. TRAILING activation ────────────────────────────────────
        if premium_change_pct >= self.TRAILING_ACTIVATE_PCT and not self._trailing_active:
            self._trailing_active = True
            self.logger.info(
                "[%s] OPTION TRAIL ON at +%.1f%%", self.option_symbol, premium_change_pct,
            )

        # ── 4. TRAILING STOP check ───────────────────────────────────
        if self._trailing_active:
            trail_floor = self.highest_premium * (1 - self.TRAILING_DISTANCE_PCT / 100)
            if current_premium <= trail_floor:
                final_pct = (current_premium - self.entry_premium) / self.entry_premium * 100
                self.logger.info(
                    "[%s] OPTION TRAIL HIT — peak=$%.4f floor=$%.4f now=$%.4f",
                    self.option_symbol, self.highest_premium, trail_floor, current_premium,
                )
                return await self._do_option_exit(current_premium, final_pct, "TRAIL")

        # ── 5. SIGNAL REVERSAL ────────────────────────────────────────
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
                            current_premium, premium_change_pct, "REVERSAL",
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

        # Stats
        if pnl_pct >= 0:
            self.hourly_wins += 1
        else:
            self.hourly_losses += 1
        self.hourly_pnl += pnl_usd

        return [Signal(
            side="sell",
            price=current_premium,
            amount=float(self.CONTRACTS_PER_TRADE),
            order_type="market",
            reason=reason,
            strategy=self.name,
            pair=self.option_symbol or self.pair,
            leverage=1,
            position_type="long",
            reduce_only=True,
            exchange_id="delta",
        )]

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
            self._trailing_active = False
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
            self._trailing_active = False
            self.expiry_dt = None

    def on_rejected(self, signal: Signal) -> None:
        """Handle rejected option orders."""
        pending_side = signal.metadata.get("pending_side")
        if pending_side:
            self.logger.warning(
                "[%s] Option entry REJECTED — not tracking", signal.pair,
            )

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
