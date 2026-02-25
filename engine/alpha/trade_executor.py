"""Trade executor — places orders via ccxt with retry logic and logging.

Multi-exchange aware: routes orders to Binance (spot) or Delta (futures)
based on signal.exchange_id. Sets leverage for futures orders.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.strategies.base import Signal, StrategyName
from alpha.utils import iso_now, setup_logger

logger = setup_logger("trade_executor")

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds

# Exit fill slippage thresholds
SLIPPAGE_WARN_PCT = 1.0   # log warning + Telegram alert (futures only)
SLIPPAGE_FLAG_PCT = 2.0   # also flag trade in DB for dashboard highlighting

# Delta Exchange India contract sizes (linear perpetual, settled in USD)
# 1 ETH contract = 0.01 ETH, 1 BTC contract = 0.001 BTC
# 1 SOL contract = 1.0 SOL, 1 XRP contract = 1.0 XRP
# The amount in create_order is the number of INTEGER contracts.
DELTA_CONTRACT_SIZE: dict[str, float] = {
    "ETH/USD:USD": 0.01,     # 1 contract = 0.01 ETH (~$20 notional)
    "ETHUSD": 0.01,          # alias
    "BTC/USD:USD": 0.001,    # 1 contract = 0.001 BTC (~$70 notional)
    "BTCUSD": 0.001,         # alias
    "SOL/USD:USD": 1.0,      # 1 contract = 1.0 SOL (~$140 notional)
    "SOLUSD": 1.0,           # alias
    "XRP/USD:USD": 1.0,      # 1 contract = 1.0 XRP (~$0.55 notional)
    "XRPUSD": 1.0,           # alias
}


# Options symbol pattern: contains date-strike-C/P (e.g. "260221-98000-C")
_OPTION_SYMBOL_RE = re.compile(r'\d{6}-\d+-[CP]')


def is_option_symbol(pair: str) -> bool:
    """Return True if pair looks like a Delta options contract symbol."""
    return bool(_OPTION_SYMBOL_RE.search(pair))


class PnLResult:
    """Structured P&L calculation result with fee breakdown."""
    __slots__ = ("net_pnl", "pnl_pct", "gross_pnl", "entry_fee", "exit_fee")

    def __init__(self, net_pnl: float, pnl_pct: float, gross_pnl: float,
                 entry_fee: float, exit_fee: float) -> None:
        self.net_pnl = net_pnl
        self.pnl_pct = pnl_pct
        self.gross_pnl = gross_pnl
        self.entry_fee = entry_fee
        self.exit_fee = exit_fee

    def __iter__(self):
        """Allow unpacking as (net_pnl, pnl_pct) for backward compat."""
        yield self.net_pnl
        yield self.pnl_pct


def calc_pnl(
    entry_price: float,
    exit_price: float,
    amount: float,
    position_type: str,
    leverage: int | float,
    exchange_id: str,
    pair: str,
    *,
    entry_fee_rate: float = 0.0,
    exit_fee_rate: float = 0.0,
) -> PnLResult:
    """Single source of truth for P&L calculation across ALL close paths.

    Used by trade_executor._close_trade_in_db (normal exits) AND
    main.py reconciliation (orphan, restart, dust, manual close detection).

    Returns PnLResult with net_pnl, pnl_pct, gross_pnl, entry_fee, exit_fee.
    Supports tuple unpacking: (net_pnl, pnl_pct) = calc_pnl(...) for backward compat.

    amount = contracts for Delta, coins for Binance spot.
    Fee rates are per-side (e.g. 0.0005 = 0.05%).  Pass 0.0 to skip fees.
    """
    if entry_price <= 0 or exit_price <= 0:
        return PnLResult(0.0, 0.0, 0.0, 0.0, 0.0)

    # Options P&L: entry/exit prices are premiums, 1 contract = 1 unit
    # No contract size conversion (options are NOT futures contracts)
    is_option = is_option_symbol(pair)

    # Convert contracts to coins for Delta futures (not options)
    coin_amount = float(amount)
    if exchange_id == "delta" and not is_option:
        contract_size = DELTA_CONTRACT_SIZE.get(pair, 0.01)
        coin_amount = float(amount) * contract_size

    # Gross P&L (notional)
    if position_type in ("long", "spot"):
        gross_pnl = (exit_price - entry_price) * coin_amount
    else:  # short
        gross_pnl = (entry_price - exit_price) * coin_amount

    # Fees (notional)
    entry_notional = entry_price * coin_amount
    exit_notional = exit_price * coin_amount
    entry_fee_dollars = entry_notional * entry_fee_rate
    exit_fee_dollars = exit_notional * exit_fee_rate
    net_pnl = gross_pnl - entry_fee_dollars - exit_fee_dollars

    # P&L % against collateral (margin posted)
    lev = max(int(leverage or 1), 1)
    collateral = entry_notional / lev if lev > 1 else entry_notional
    pnl_pct = (net_pnl / collateral * 100) if collateral > 0 else 0.0

    # Options: convert notional P&L to REAL wallet P&L (÷ leverage).
    # At 50x, you only posted 1/50th of premium as collateral.
    # Notional: (68-95)*1 = -$27.  Real wallet loss: -$27/50 = -$0.54.
    # pnl_pct stays the same (-28.52%) — it's already vs collateral.
    if is_option and lev > 1:
        gross_pnl /= lev
        entry_fee_dollars /= lev
        exit_fee_dollars /= lev
        net_pnl = gross_pnl - entry_fee_dollars - exit_fee_dollars

    return PnLResult(
        round(net_pnl, 8), round(pnl_pct, 4), round(gross_pnl, 8),
        round(entry_fee_dollars, 8), round(exit_fee_dollars, 8),
    )


def _extract_exit_reason(reason: str) -> str:
    """Extract clean exit_reason enum from verbose reason string."""
    if not reason:
        return "UNKNOWN"
    upper = reason.upper()
    for kw in ("HARD_TP", "PROFIT_LOCK", "DEAD_MOMENTUM", "MOMENTUM_FADE",
               "DECAY_EMERGENCY", "MANUAL_CLOSE", "SPOT_PULLBACK", "SPOT_DECAY",
               "SPOT_BREAKEVEN", "TRAIL", "RATCHET", "SL", "FLAT", "TIMEOUT",
               "BREAKEVEN", "REVERSAL", "PULLBACK", "DECAY", "SAFETY", "EXPIRY"):
        if kw in upper:
            return "MANUAL" if kw == "MANUAL_CLOSE" else kw
    direct = {
        "POSITION_GONE": "POSITION_GONE", "PHANTOM_CLEARED": "PHANTOM",
        "SL_EXCHANGE": "SL_EXCHANGE", "TP_EXCHANGE": "TP_EXCHANGE",
        "CLOSED_BY_EXCHANGE": "CLOSED_BY_EXCHANGE", "ORPHAN": "ORPHAN",
        "DUST": "DUST",
    }
    for key, val in direct.items():
        if key in upper:
            return val
    return "UNKNOWN"


class TradeExecutor:
    """Unified order execution layer on top of ccxt."""

    def __init__(
        self,
        exchange: ccxt.Exchange,
        db: Any | None = None,
        alerts: Any | None = None,
        delta_exchange: ccxt.Exchange | None = None,
        risk_manager: Any | None = None,
        options_exchange: ccxt.Exchange | None = None,
    ) -> None:
        self.exchange = exchange                      # Binance (primary)
        self.delta_exchange = delta_exchange           # Delta (optional, futures)
        self.options_exchange = options_exchange       # Delta options (optional)
        self.db = db  # alpha.db.Database
        self.alerts = alerts  # alpha.alerts.AlertManager
        self.risk_manager = risk_manager              # alpha.risk_manager.RiskManager
        self._min_notional: dict[str, float] = {}     # pair -> min order value
        self._min_amount: dict[str, float] = {}       # pair -> min order qty
        # Error spam suppression: pair -> (error_key, timestamp)
        self._last_error_alert: dict[str, tuple[str, float]] = {}
        self._ERROR_DEDUP_SECONDS = 300  # 5 minutes
        # EXIT FAILED alerts: only send ONCE per pair (permanent suppression)
        self._exit_failure_alerted: set[str] = set()
        # Fee rates (per side, INCLUDING GST for Delta India)
        # Delta: taker 0.05% + 18% GST = 0.059%, maker 0.02% + 18% GST = 0.024%
        self._delta_taker_fee: float = config.delta.taker_fee_with_gst  # 0.059% per side
        self._delta_maker_fee: float = config.delta.maker_fee_with_gst  # 0.024% per side
        self._binance_taker_fee: float = 0.001  # default 0.1%

    @staticmethod
    def _is_option_symbol(pair: str) -> bool:
        """Check if a pair is an option symbol (contains -C or -P suffix)."""
        return pair.endswith("-C") or pair.endswith("-P")

    def _get_exchange(self, signal: Signal) -> ccxt.Exchange:
        """Return the correct exchange instance for a signal.

        Routes option symbols (-C/-P suffix) to the options exchange,
        Delta futures to delta_exchange, and everything else to Binance.
        """
        if self._is_option_symbol(signal.pair) and self.options_exchange:
            return self.options_exchange
        if signal.exchange_id == "delta" and self.delta_exchange:
            return self.delta_exchange
        return self.exchange  # default: Binance

    @staticmethod
    def _to_delta_contracts(pair: str, coin_amount: float, price: float) -> int:
        """Convert a fractional coin amount to integer Delta Exchange contracts.

        Delta uses integer contract quantities:
          1 ETH contract = 0.01 ETH (~$20.80 notional at $2080)
          1 BTC contract = 0.001 BTC (~$69.70 notional at $69700)

        Example: coin_amount=0.01 ETH / contract_size=0.01 = 1 contract
                 → send amount=1, NOT amount=0.01

        Returns: number of contracts (minimum 1).
        """
        contract_size = DELTA_CONTRACT_SIZE.get(pair, 0)
        if contract_size <= 0:
            # Unknown pair — try to derive from ccxt market info (fallback)
            logger.warning("[%s] Unknown Delta contract size, using raw amount", pair)
            return max(1, round(coin_amount))

        # Use round() not int() to avoid floating-point truncation
        # e.g. 0.01/0.01 might be 0.999999 → int()=0, round()=1
        contracts = round(coin_amount / contract_size)
        return max(contracts, 1)

    @staticmethod
    def _delta_contracts_to_coin(pair: str, contracts: int) -> float:
        """Convert integer Delta contracts back to coin amount."""
        contract_size = DELTA_CONTRACT_SIZE.get(pair, 0)
        if contract_size <= 0:
            return float(contracts)
        return contracts * contract_size

    async def _get_spot_exit_amount(self, signal: Signal) -> float | None:
        """Fetch actual asset balance and truncate to exchange step size for spot exits.

        Trading fees reduce the held amount vs entry amount, so we must sell
        the ACTUAL balance, not the entry amount.  Truncate (floor) to the
        exchange's LOT_SIZE step so Binance doesn't reject for precision.

        Returns None for futures (they use reduce_only with contract amounts).
        """
        if signal.reduce_only:
            return None  # futures — amount handled by contract
        try:
            exchange = self._get_exchange(signal)
            balance = await exchange.fetch_balance()
            # Base asset: e.g. "ETH" from "ETH/USDT"
            base = signal.pair.split("/")[0] if "/" in signal.pair else signal.pair
            free = float(balance.get("free", {}).get(base, 0) or 0)
            total = float(balance.get("total", {}).get(base, 0) or 0)
            raw = free if free > 0 else total

            if raw <= 0:
                logger.warning("[%s] No %s balance found for exit (free=%.8f, total=%.8f)",
                               signal.pair, base, free, total)
                return None

            # Truncate to exchange step size using ccxt's precision helper
            # amount_to_precision uses TRUNCATE mode by default for Binance
            try:
                truncated = float(exchange.amount_to_precision(signal.pair, raw))
            except Exception:
                # Fallback: manual floor to step size from cached limits
                step = self._min_amount.get(signal.pair, 0)
                if step and step > 0:
                    truncated = math.floor(raw / step) * step
                else:
                    truncated = raw

            logger.info(
                "[%s] Spot exit: %s raw=%.8f → truncated=%.8f (step=%s)",
                signal.pair, base, raw, truncated,
                self._min_amount.get(signal.pair, "?"),
            )
            return truncated if truncated > 0 else None
        except Exception:
            logger.warning("[%s] Could not fetch asset balance for exit", signal.pair)
            return None

    async def _get_delta_position_size(self, signal: Signal) -> float | None:
        """Fetch actual position size from Delta Exchange for exit validation.

        Returns the number of contracts open on the exchange, or None if
        no position exists (already closed/liquidated).
        """
        if not self.delta_exchange:
            return None
        try:
            positions = await self.delta_exchange.fetch_positions([signal.pair])
            for pos in positions:
                symbol = pos.get("symbol", "")
                contracts = abs(float(pos.get("contracts", 0) or 0))
                if symbol == signal.pair and contracts > 0:
                    logger.info(
                        "[%s] Delta position found: %.0f contracts (%s)",
                        signal.pair, contracts, pos.get("side", "?"),
                    )
                    return contracts
            # No matching position
            return None
        except Exception as e:
            logger.warning(
                "[%s] Could not fetch Delta position: %s — using stored amount",
                signal.pair, e,
            )
            # On fetch failure, return the stored amount to avoid blocking exit
            return signal.amount

    async def _mark_position_gone(self, signal: Signal) -> None:
        """Mark a trade as closed in DB when the position no longer exists on exchange.

        Tries to fetch the ACTUAL fill price from exchange trade history first.
        Falls back to signal.price (current market) if trade history unavailable.
        Sends a clean info alert ONLY when a trade was actually found and closed.
        """
        actually_closed = False
        if self.db is not None:
            try:
                open_trade = await self.db.get_open_trade(
                    pair=signal.pair,
                    exchange=signal.exchange_id,
                    strategy=signal.strategy.value,
                )
                if open_trade:
                    entry_price = float(open_trade.get("entry_price", 0) or 0)
                    amount = open_trade.get("amount", signal.amount)
                    position_type = open_trade.get("position_type", signal.position_type)
                    trade_leverage = open_trade.get("leverage", signal.leverage) or 1

                    # ── Try to get the REAL exit price from exchange trade history ──
                    # When position_gone fires, the position was closed externally
                    # (exchange SL, liquidation, manual close). The actual fill price
                    # is in the exchange's trade history, NOT signal.price.
                    exit_price = await self._fetch_actual_exit_price(
                        signal, position_type, entry_price,
                    )
                    exit_source = "trade_history" if exit_price != signal.price else "signal"

                    # Calculate real P&L
                    pnl, pnl_pct = calc_pnl(
                        entry_price, exit_price, amount,
                        position_type, trade_leverage,
                        signal.exchange_id, signal.pair,
                    )

                    await self.db.update_trade(open_trade["id"], {
                        "status": "closed",
                        "exit_price": exit_price,
                        "closed_at": iso_now(),
                        "pnl": round(pnl, 8),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": "position_gone",
                        "exit_reason": "POSITION_GONE",
                    })
                    logger.info(
                        "[%s] Trade %s marked closed (position_gone) "
                        "entry=$%.4f exit=$%.4f pnl=$%.4f (%.2f%%) [exit_src=%s]",
                        signal.pair, open_trade["id"],
                        entry_price, exit_price, pnl, pnl_pct, exit_source,
                    )
                    actually_closed = True

                    # Remove from risk manager — prevents ghost entries in open_positions
                    if self.risk_manager is not None:
                        self.risk_manager.record_close(signal.pair, pnl)
                else:
                    logger.debug("[%s] position_gone: no open trade found in DB — already closed", signal.pair)
                    # Still remove from risk manager in case it's tracked there
                    if self.risk_manager is not None:
                        self.risk_manager.record_close(signal.pair, 0.0)
            except Exception:
                logger.exception("[%s] Failed to mark trade as position_gone", signal.pair)

        # Send clean info alert ONLY when we actually closed something (avoid spam)
        if actually_closed and self.alerts is not None:
            try:
                pair_short = signal.pair.split("/")[0] if "/" in signal.pair else signal.pair
                await self.alerts.send_text(
                    f"\u2139\ufe0f {pair_short} — Position not found on exchange\n"
                    f"Marked closed in DB. No action needed."
                )
            except Exception:
                pass

    async def _fetch_actual_exit_price(
        self, signal: Signal, position_type: str, entry_price: float,
    ) -> float:
        """Try to fetch the actual exit fill price from exchange trade history.

        When a position is closed externally (exchange SL, liquidation, manual),
        the real fill price is in the exchange's recent trades — not signal.price
        (which is just the current market price when the bot noticed).

        Returns the actual fill price if found, otherwise falls back to signal.price.
        """
        try:
            exchange = self._get_exchange(signal)
            # Fetch recent fills for this pair
            recent_trades = await exchange.fetch_my_trades(signal.pair, limit=20)
            if recent_trades:
                # The closing trade is the opposite side of our position
                close_side = "sell" if position_type in ("long", "spot") else "buy"
                closing_fills = [
                    t for t in recent_trades
                    if t.get("side") == close_side
                ]
                if closing_fills:
                    # Most recent closing fill is our exit
                    last_fill = closing_fills[-1]
                    fill_price = float(last_fill.get("price", 0) or 0)
                    if fill_price > 0:
                        logger.info(
                            "[%s] Found actual exit fill: $%.4f (vs signal price $%.4f)",
                            signal.pair, fill_price, signal.price,
                        )
                        return fill_price

            # No closing fills found — try ticker as second fallback
            ticker = await exchange.fetch_ticker(signal.pair)
            last_price = float(ticker.get("last", 0) or 0)
            if last_price > 0:
                logger.info(
                    "[%s] No fill found, using ticker last=$%.4f (vs signal $%.4f)",
                    signal.pair, last_price, signal.price,
                )
                return last_price

        except Exception as e:
            logger.warning(
                "[%s] Could not fetch actual exit price: %s — using signal price $%.4f",
                signal.pair, e, signal.price,
            )

        return signal.price

    async def load_market_limits(
        self, pairs: list[str], delta_pairs: list[str] | None = None,
    ) -> None:
        """Pre-load minimum order sizes for all tracked pairs on each exchange."""
        # Binance spot
        try:
            await self.exchange.load_markets()
            for pair in pairs:
                market = self.exchange.markets.get(pair)
                if market:
                    limits = market.get("limits", {})
                    cost_limits = limits.get("cost", {})
                    amount_limits = limits.get("amount", {})
                    self._min_notional[pair] = cost_limits.get("min", 0) or 0
                    self._min_amount[pair] = amount_limits.get("min", 0) or 0
                    logger.debug(
                        "[%s] min notional=$%.2f, min amount=%.8f",
                        pair, self._min_notional[pair], self._min_amount[pair],
                    )
                else:
                    logger.warning("Market info not found for %s on Binance", pair)
        except Exception:
            logger.exception("Failed to load Binance market limits")

        # Delta futures
        if self.delta_exchange and delta_pairs:
            try:
                await self.delta_exchange.load_markets()
                for pair in delta_pairs:
                    market = self.delta_exchange.markets.get(pair)
                    if market:
                        limits = market.get("limits", {})
                        cost_limits = limits.get("cost", {})
                        amount_limits = limits.get("amount", {})
                        self._min_notional[pair] = cost_limits.get("min", 0) or 0
                        self._min_amount[pair] = amount_limits.get("min", 0) or 0
                        # Extract fee rates from market info (ccxt provides these)
                        # API returns BASE rates — we add 18% GST for India
                        taker = market.get("taker")
                        maker = market.get("maker")
                        gst_mult = 1 + config.delta.gst_rate  # 1.18
                        if taker is not None:
                            self._delta_taker_fee = float(taker) * gst_mult
                        if maker is not None:
                            self._delta_maker_fee = float(maker) * gst_mult
                        logger.debug(
                            "[%s] Delta min notional=$%.2f, min amount=%.8f",
                            pair, self._min_notional[pair], self._min_amount[pair],
                        )
                    else:
                        logger.warning("Market info not found for %s on Delta", pair)
                logger.info(
                    "Delta fee rates (incl GST %.0f%%): taker=%.6f (%.4f%%), maker=%.6f (%.4f%%), "
                    "RT taker=%.4f%%, RT maker=%.4f%%, RT mixed=%.4f%%",
                    config.delta.gst_rate * 100,
                    self._delta_taker_fee, self._delta_taker_fee * 100,
                    self._delta_maker_fee, self._delta_maker_fee * 100,
                    self._delta_taker_fee * 200, self._delta_maker_fee * 200,
                    (self._delta_maker_fee + self._delta_taker_fee) * 100,
                )
            except Exception:
                logger.exception("Failed to load Delta market limits")

    def _is_exit_order(self, signal: Signal) -> bool:
        """Determine if this signal is closing/exiting an existing position."""
        return signal.reduce_only or (
            signal.position_type == "spot" and signal.side == "sell"
        )

    def validate_order_size(self, signal: Signal) -> bool:
        """Check if the order meets exchange minimum requirements.

        NEVER blocks exit orders — exits must always be attempted.
        For futures (Delta): the notional value is collateral × leverage,
        which easily clears minimums. We check the leveraged notional.
        For spot (Binance): check order value directly against $5 min.
        """
        # Never block exit orders — we must always try to close positions
        if self._is_exit_order(signal):
            order_value = signal.price * signal.amount
            logger.debug(
                "[%s] Exit order: $%.4f — skipping min notional validation (exits always allowed)",
                signal.pair, order_value,
            )
            return True

        pair = signal.pair
        order_value = signal.price * signal.amount  # for futures, amount is already leveraged
        min_notional = self._min_notional.get(pair, 0)
        min_amount = self._min_amount.get(pair, 0)

        # For Delta futures: amount is already leverage-adjusted in the signal,
        # so order_value = notional. Skip min notional for futures — Delta
        # minimums are much lower than Binance's $5.
        is_futures = signal.exchange_id == "delta" and signal.leverage > 1
        if is_futures:
            logger.debug(
                "[%s] Futures order: collateral=$%.2f, notional=$%.2f (skipping min notional check)",
                pair, order_value / signal.leverage, order_value,
            )
        elif min_notional and order_value < min_notional:
            logger.warning(
                "[%s] Order value $%.4f below min notional $%.2f -- skipping",
                pair, order_value, min_notional,
            )
            return False

        if min_amount and signal.amount < min_amount:
            logger.warning(
                "[%s] Order amount %.8f below min %.8f -- skipping",
                pair, signal.amount, min_amount,
            )
            return False
        return True

    def _enforce_binance_min(self, signal: Signal) -> Signal:
        """Ensure Binance spot ENTRY orders meet the $6 minimum notional.

        If order value < $6.01, bump the amount up to 6.01 / price.
        We use $6 (not Binance's $5) to prevent dust on exit — after fees,
        the remaining amount must still be above Binance's $5 minimum to sell.
        Also round amount up to the exchange's LOT_SIZE step if available.
        Skips exit orders — exits sell what was bought, even if below minimum.
        """
        if signal.exchange_id != "binance" or signal.position_type != "spot":
            return signal
        # Don't bump exit orders — sell what you have
        if self._is_exit_order(signal):
            return signal

        order_value = signal.price * signal.amount
        min_required = 6.01  # $6 min + buffer (prevents dust on exit)

        if order_value < min_required and signal.price > 0:
            new_amount = min_required / signal.price
            # Round up to LOT_SIZE step size
            step = self._min_amount.get(signal.pair, 0)
            if step and step > 0:
                new_amount = math.ceil(new_amount / step) * step
            logger.info(
                "[%s] Binance min notional: order $%.4f < $%.2f — bumping amount %.8f -> %.8f ($%.4f)",
                signal.pair, order_value, min_required, signal.amount, new_amount,
                new_amount * signal.price,
            )
            signal = Signal(
                side=signal.side,
                price=signal.price,
                amount=new_amount,
                order_type=signal.order_type,
                reason=signal.reason,
                strategy=signal.strategy,
                pair=signal.pair,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                metadata=signal.metadata,
                leverage=signal.leverage,
                position_type=signal.position_type,
                reduce_only=signal.reduce_only,
                exchange_id=signal.exchange_id,
            )
        return signal

    async def execute(self, signal: Signal) -> dict | None:
        """Place an order for the given signal, with retry + logging.

        Exit orders get special treatment:
        - All errors are retried (not just network errors)
        - If Binance rejects for min notional, try quoteOrderQty fallback
        - On total failure, send a critical Telegram alert (never silent)
        """
        is_exit = self._is_exit_order(signal)

        # Track whether we should try quoteOrderQty if amount-based sell fails
        use_quote_fallback = False

        # For spot exits: fetch actual balance (fees reduce held amount)
        # and truncate to exchange step size
        if is_exit and signal.exchange_id == "binance" and not signal.reduce_only:
            actual_amount = await self._get_spot_exit_amount(signal)
            if actual_amount and actual_amount > 0:
                logger.info(
                    "[%s] Exit: entry_amount=%.8f → actual_balance=%.8f (diff from fees)",
                    signal.pair, signal.amount, actual_amount,
                )
                # Check if truncated amount * price < $5 (MIN_NOTIONAL)
                est_value = actual_amount * signal.price
                if est_value < 5.0:
                    # Amount too small for normal sell — will use quoteOrderQty
                    use_quote_fallback = True
                    logger.info(
                        "[%s] Exit value $%.4f < $5 — will use quoteOrderQty mode",
                        signal.pair, est_value,
                    )
                signal = Signal(
                    side=signal.side,
                    price=signal.price,
                    amount=actual_amount,
                    order_type=signal.order_type,
                    reason=signal.reason,
                    strategy=signal.strategy,
                    pair=signal.pair,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    metadata=signal.metadata,
                    leverage=signal.leverage,
                    position_type=signal.position_type,
                    reduce_only=signal.reduce_only,
                    exchange_id=signal.exchange_id,
                )

        # ── DELTA FUTURES EXIT: verify actual position on exchange ────────
        # Fetch real position size to avoid amount mismatch errors.
        # If position is already gone on exchange, mark closed in DB.
        # Skip for options_scalp — options positions are on a separate exchange,
        # fetch_positions (futures) won't find them.
        is_options = signal.strategy == StrategyName.OPTIONS_SCALP
        if is_exit and signal.exchange_id == "delta" and signal.reduce_only and not is_options:
            actual_contracts = await self._get_delta_position_size(signal)
            if actual_contracts is None:
                # Position already gone on exchange — mark closed in DB
                logger.warning(
                    "[%s] No position found on Delta — marking closed in DB",
                    signal.pair,
                )
                await self._mark_position_gone(signal)
                return None
            elif actual_contracts != signal.amount:
                # Use actual size, not stored amount
                logger.info(
                    "[%s] Delta exit: stored=%.0f contracts, actual=%.0f — using actual",
                    signal.pair, signal.amount, actual_contracts,
                )
                signal = Signal(
                    side=signal.side, price=signal.price,
                    amount=float(actual_contracts),
                    order_type=signal.order_type, reason=signal.reason,
                    strategy=signal.strategy, pair=signal.pair,
                    stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                    metadata=signal.metadata,
                    leverage=signal.leverage, position_type=signal.position_type,
                    reduce_only=signal.reduce_only, exchange_id=signal.exchange_id,
                )

        # Enforce Binance $6.01 minimum notional for ENTRY orders only
        if not is_exit:
            signal = self._enforce_binance_min(signal)

        # Convert Delta coin amounts to integer contracts BEFORE validation
        # Skip for option symbols — options use their own contract sizing (1 contract = 1 option)
        # Skip for exits that already have contract amounts from position fetch
        is_delta_exit_with_contracts = (
            is_exit and signal.exchange_id == "delta" and signal.reduce_only
        )
        if signal.exchange_id == "delta" and not self._is_option_symbol(signal.pair) and not is_delta_exit_with_contracts:
            contracts = self._to_delta_contracts(signal.pair, signal.amount, signal.price)
            logger.info("[%s] Delta: %.8f coins -> %d contracts", signal.pair, signal.amount, contracts)
            signal = Signal(
                side=signal.side, price=signal.price,
                amount=float(contracts),
                order_type=signal.order_type, reason=signal.reason,
                strategy=signal.strategy, pair=signal.pair,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                metadata=signal.metadata,
                leverage=signal.leverage, position_type=signal.position_type,
                reduce_only=signal.reduce_only, exchange_id=signal.exchange_id,
            )

        # Validate minimum order size (exits skip validation)
        if not self.validate_order_size(signal):
            return None

        exchange = self._get_exchange(signal)

        # Log with collateral and notional for clarity
        is_futures = signal.leverage > 1 and signal.exchange_id == "delta"
        notional = signal.price * signal.amount
        collateral = notional / signal.leverage if is_futures else notional

        logger.info(
            "Executing %s %s %s %.8f @ $%.2f [%s/%s] -- collateral=$%.2f%s -- %s",
            signal.order_type, signal.side, signal.pair,
            signal.amount, signal.price, signal.exchange_id,
            signal.strategy.value,
            collateral,
            f" notional=${notional:.2f} ({signal.leverage}x)" if is_futures else "",
            signal.reason,
        )

        # Futures: set leverage before placing order
        if is_futures:
            try:
                await exchange.set_leverage(signal.leverage, signal.pair)
                logger.info("[%s] Leverage set to %dx", signal.pair, signal.leverage)
            except Exception:
                logger.warning(
                    "Failed to set leverage %dx for %s (may already be set)",
                    signal.leverage, signal.pair,
                )

        # Build extra order params
        params: dict[str, Any] = {}
        if signal.reduce_only:
            params["reduceOnly"] = True
        # Pass leverage to Delta so the exchange knows the position leverage
        if is_futures:
            params["leverage"] = signal.leverage

        # signal.amount is already in integer contracts for Delta (converted above)
        order_amount = signal.amount

        order: dict | None = None
        last_error: Exception | None = None

        # ── LIMIT EXIT OPTIMIZATION: Non-urgent Delta futures exits use limit-then-market ──
        # Saves ~60% on exit fees (maker 0.024% vs taker 0.059%)
        # Place limit at current price, wait 3s, cancel & market if unfilled
        # Urgent exits always use market — speed matters more than fees
        _URGENT_EXITS = {"SL", "HARD_TP", "SL_EXCHANGE", "PROFIT_LOCK", "DECAY_EMERGENCY"}
        _exit_reason = _extract_exit_reason(signal.reason) if is_exit else ""
        _use_limit_exit = (
            is_exit
            and signal.exchange_id == "delta"
            and _exit_reason not in _URGENT_EXITS
        )
        limit_order_id: str | None = None  # track limit order for recovery in market retry

        if _use_limit_exit:
            try:
                limit_order = await exchange.create_order(
                    symbol=signal.pair,
                    type="limit",
                    side=signal.side,
                    amount=order_amount,
                    price=signal.price,
                    params=params,
                )
                limit_order_id = limit_order.get("id")
                logger.info(
                    "[%s] Limit exit placed: %s %.0f @ $%.2f (order=%s, reason=%s) — waiting 3s for fill",
                    signal.pair, signal.side, order_amount, signal.price, limit_order_id, _exit_reason,
                )
                await asyncio.sleep(3)

                # Check if filled
                try:
                    updated = await exchange.fetch_order(limit_order_id, signal.pair)
                    status = updated.get("status", "")
                    filled = float(updated.get("filled", 0) or 0)
                except Exception:
                    status = "unknown"
                    filled = 0

                if status == "closed" or filled >= order_amount:
                    logger.info("[%s] Limit exit FILLED (maker fee)", signal.pair)
                    order = updated
                else:
                    # Before cancelling, verify position still exists on exchange.
                    # The limit order may have filled between our fetch_order and now,
                    # or the order status might be stale.
                    pos_check = await self._get_delta_position_size(signal)
                    if pos_check is None:
                        # Position is GONE — the limit order DID fill (or was liquidated).
                        # Re-fetch the order to get actual fill price.
                        logger.info(
                            "[%s] Position gone after limit exit — order likely filled. Re-checking order.",
                            signal.pair,
                        )
                        try:
                            updated = await exchange.fetch_order(limit_order_id, signal.pair)
                            status = updated.get("status", "")
                            filled = float(updated.get("filled", 0) or 0)
                        except Exception:
                            pass  # keep previous values

                        if status == "closed" or filled > 0:
                            logger.info(
                                "[%s] Limit exit confirmed FILLED on re-check (status=%s, filled=%.0f)",
                                signal.pair, status, filled,
                            )
                            order = updated
                        else:
                            # Position gone but order shows unfilled — closed externally
                            # (liquidation, manual close, etc.). Mark position_gone.
                            logger.warning(
                                "[%s] Position gone but limit order unfilled — closed externally",
                                signal.pair,
                            )
                            try:
                                await exchange.cancel_order(limit_order_id, signal.pair)
                            except Exception:
                                pass
                            await self._mark_position_gone(signal)
                            return None
                    else:
                        # Position still exists — limit genuinely didn't fill. Cancel and retry market.
                        logger.info(
                            "[%s] Limit exit NOT filled (status=%s, filled=%.0f/%.0f), "
                            "position still open (%.0f contracts) — cancelling, using market",
                            signal.pair, status, filled, order_amount, pos_check,
                        )
                        try:
                            await exchange.cancel_order(limit_order_id, signal.pair)
                        except Exception:
                            pass  # may already be cancelled/filled
                        # Fall through to market order below
            except Exception as e:
                logger.warning("[%s] Limit exit failed: %s — falling back to market", signal.pair, e)
                # Fall through to market order below

        # If limit exit already succeeded, skip the market retry loop
        if order is None:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if use_quote_fallback and is_exit:
                        # Sell by USDT value — for small balances below MIN_NOTIONAL
                        # Must NOT pass amount when using quoteOrderQty on Binance
                        quote_value = round(signal.amount * signal.price, 2)
                        logger.info(
                            "[%s] Placing quoteOrderQty sell: $%.2f (amount=%.8f too small for normal sell)",
                            signal.pair, quote_value, signal.amount,
                        )
                        order = await exchange.create_order(
                            symbol=signal.pair,
                            type="market",
                            side="sell",
                            amount=None,
                            params={**params, "quoteOrderQty": quote_value},
                        )
                    elif signal.order_type == "market":
                        # Log exact params sent to exchange (critical for debugging)
                        logger.debug(
                            "[%s] create_order(symbol=%s, type=market, side=%s, amount=%s, params=%s)",
                            signal.pair, signal.pair, signal.side, order_amount, params,
                        )
                        order = await exchange.create_order(
                            symbol=signal.pair,
                            type="market",
                            side=signal.side,
                            amount=order_amount,
                            params=params,
                        )
                    else:
                        logger.debug(
                            "[%s] create_order(symbol=%s, type=limit, side=%s, amount=%s, price=%.2f, params=%s)",
                            signal.pair, signal.pair, signal.side, order_amount, signal.price, params,
                        )
                        order = await exchange.create_order(
                            symbol=signal.pair,
                            type="limit",
                            side=signal.side,
                            amount=order_amount,
                            price=signal.price,
                            params=params,
                        )
                    break
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                    last_error = e
                    delay = BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Order attempt %d/%d failed (retryable): %s -- retrying in %.1fs",
                        attempt, MAX_RETRIES, e, delay,
                    )
                    await asyncio.sleep(delay)
                except ccxt.InsufficientFunds as e:
                    last_error = e
                    if is_exit:
                        # Exit: retry — balance may have updated
                        logger.warning(
                            "Exit attempt %d/%d insufficient funds: %s -- retrying in 2s",
                            attempt, MAX_RETRIES, e,
                        )
                        await asyncio.sleep(2)
                    else:
                        logger.error("Insufficient funds for order: %s", e)
                        await self._notify_error(signal, str(e))
                        return None
                except ccxt.InvalidOrder as e:
                    last_error = e
                    err_str = str(e).lower()
                    # Delta: "no_position_for_reduce_only" → position already closed
                    if is_exit and signal.exchange_id == "delta" and (
                        "no_position" in err_str or "reduce_only" in err_str
                    ):
                        logger.warning(
                            "[%s] Position already closed on exchange: %s",
                            signal.pair, e,
                        )
                        # Try to find actual fill price from the limit order (if we used one)
                        if _use_limit_exit and limit_order_id:
                            try:
                                final_order = await exchange.fetch_order(limit_order_id, signal.pair)
                                final_fill = float(final_order.get("filled", 0) or 0)
                                final_price = final_order.get("average") or final_order.get("price")
                                if final_fill > 0 and final_price:
                                    logger.info(
                                        "[%s] Limit order %s actually filled: %.0f @ $%.2f — using as exit",
                                        signal.pair, limit_order_id, final_fill, float(final_price),
                                    )
                                    order = final_order
                                    break  # exit retry loop — proceed to normal DB write
                            except Exception:
                                pass  # fall through to _mark_position_gone
                        # If we couldn't recover the fill, mark as position_gone
                        if order is None:
                            await self._mark_position_gone(signal)
                            return None
                    elif is_exit and signal.exchange_id == "binance" and "MIN_NOTIONAL" in str(e).upper():
                        # Binance rejected exit for min notional — switch to quoteOrderQty
                        logger.warning(
                            "[%s] Exit rejected for MIN_NOTIONAL — switching to quoteOrderQty mode",
                            signal.pair,
                        )
                        use_quote_fallback = True
                        # Don't sleep — immediately retry with quoteOrderQty on next iteration
                        continue
                    elif is_exit:
                        # Exit: retry all errors
                        logger.warning(
                            "Exit attempt %d/%d invalid order: %s -- retrying in 2s",
                            attempt, MAX_RETRIES, e,
                        )
                        await asyncio.sleep(2)
                    else:
                        logger.error("Invalid order: %s", e)
                        await self._notify_error(signal, str(e))
                        return None
                except Exception as e:
                    last_error = e
                    if is_exit:
                        # Exit: retry ALL errors — never give up silently
                        logger.warning(
                            "Exit attempt %d/%d error: %s -- retrying in 2s",
                            attempt, MAX_RETRIES, e,
                        )
                        await asyncio.sleep(2)
                    else:
                        logger.exception("Unexpected error placing order")
                        await self._notify_error(signal, str(e))
                        return None

        if order is None:
            if is_exit:
                # CRITICAL: exit failed — alert for manual intervention
                logger.error(
                    "EXIT FAILED: All %d retries exhausted for %s %s. STUCK IN POSITION! Last error: %s",
                    MAX_RETRIES, signal.pair, signal.side, last_error,
                )
                await self._notify_exit_failure(signal, last_error)
            else:
                logger.error("All %d retries exhausted for order. Last error: %s", MAX_RETRIES, last_error)
                await self._notify_error(signal, f"Retries exhausted: {last_error}")
            return None

        # Log success
        fill_price = order.get("average") or order.get("price") or signal.price
        filled_amount = order.get("filled") or signal.amount
        order_id = order.get("id", "unknown")

        logger.info(
            "Order filled: id=%s %s %s %.8f @ %.2f [%s]",
            order_id, signal.side, signal.pair, filled_amount, fill_price,
            signal.exchange_id,
        )

        # Determine if this is an entry (opening) or exit (closing) trade
        is_exit = signal.reduce_only or (
            signal.position_type == "spot" and signal.side == "sell"
        )

        # DB write FIRST — P&L is calculated here.
        # For exits, the computed P&L is passed directly to the Telegram
        # notification so both DB and Telegram show the EXACT same numbers.
        if is_exit:
            close_result = await self._close_trade_in_db(signal, order)
            await self._notify_trade_closed(signal, order, close_result)
        else:
            await self._open_trade_in_db(signal, order)
            await self._notify_trade_opened(signal, order)

        return order

    async def _open_trade_in_db(self, signal: Signal, order: dict) -> None:
        """INSERT a new trade row for an entry/open position."""
        if self.db is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount

            # For Delta futures: filled_amount is in contracts. Convert to coin for notional calc.
            # For Delta options: 1 contract = 1 unit (no conversion needed)
            coin_qty = filled_amount
            if signal.exchange_id == "delta" and not is_option_symbol(signal.pair):
                contract_size = DELTA_CONTRACT_SIZE.get(signal.pair, 0.01)
                coin_qty = filled_amount * contract_size  # 1 contract × 0.01 = 0.01 ETH

            notional = fill_price * coin_qty
            # Cost = collateral (margin posted to exchange)
            is_futures = signal.leverage > 1 and signal.position_type in ("long", "short")
            cost = notional / signal.leverage if is_futures else notional

            # Calculate entry fee for storage
            if signal.exchange_id == "delta":
                entry_fee_rate = self._delta_taker_fee  # entries are always taker (market)
            else:
                entry_fee_rate = self._binance_taker_fee
            entry_fee = round(notional * entry_fee_rate, 8)

            # Collateral = margin posted to exchange
            lev = max(int(signal.leverage or 1), 1)
            collateral = round(notional / lev, 8) if lev > 1 else round(notional, 8)

            trade_data: dict[str, Any] = {
                "pair": signal.pair,
                "side": signal.side,
                "entry_price": fill_price,
                "amount": filled_amount,
                "cost": cost,
                "collateral": collateral,
                "strategy": signal.strategy.value,
                "order_type": signal.order_type,
                "exchange": signal.exchange_id,
                "status": "open",
                "reason": signal.reason,
                "order_id": order.get("id"),
                "leverage": signal.leverage,
                "position_type": signal.position_type,
                "setup_type": signal.metadata.get("setup_type", "unknown"),
                "signals_fired": signal.metadata.get("signals_fired"),
                "entry_fee": entry_fee,
            }
            # Store SL/TP prices (from signal or metadata) for dashboard display
            if signal.stop_loss is not None:
                trade_data["stop_loss"] = round(signal.stop_loss, 8)
            elif signal.metadata.get("sl_price"):
                trade_data["stop_loss"] = round(float(signal.metadata["sl_price"]), 8)
            if signal.take_profit is not None:
                trade_data["take_profit"] = round(signal.take_profit, 8)
            elif signal.metadata.get("tp_price"):
                trade_data["take_profit"] = round(float(signal.metadata["tp_price"]), 8)
            trade_id = await self.db.log_trade(trade_data)
            logger.info(
                "Trade opened in DB: id=%s %s %s @ $%.2f [%s]",
                trade_id, signal.side, signal.pair, fill_price, signal.exchange_id,
            )
        except Exception:
            logger.exception("Failed to log open trade to DB")

    async def _close_trade_in_db(self, signal: Signal, order: dict) -> dict | None:
        """UPDATE the existing open trade row with exit price and P&L.

        Returns a dict with the computed P&L values for downstream use
        (Telegram notification), or None on failure.
        """
        if self.db is None:
            # No DB — still clean up risk manager to prevent ghost entries
            if self.risk_manager is not None:
                self.risk_manager.record_close(signal.pair, 0.0)
            return None
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount

            # Find the open trade for this pair + exchange
            open_trade = await self.db.get_open_trade(
                pair=signal.pair,
                exchange=signal.exchange_id,
                strategy=signal.strategy.value,
            )

            if not open_trade:
                logger.warning(
                    "No open trade found in DB for %s/%s/%s — inserting as closed row",
                    signal.pair, signal.exchange_id, signal.strategy.value,
                )
                # Fallback: insert a standalone closed row (legacy behavior)
                await self.db.log_trade({
                    "pair": signal.pair,
                    "side": signal.side,
                    "entry_price": fill_price,
                    "amount": filled_amount,
                    "cost": fill_price * filled_amount,
                    "strategy": signal.strategy.value,
                    "order_type": signal.order_type,
                    "exchange": signal.exchange_id,
                    "status": "closed",
                    "reason": signal.reason,
                    "order_id": order.get("id"),
                    "leverage": signal.leverage,
                    "position_type": signal.position_type,
                })
                # Still clean up risk manager — strategy already set in_position=False
                if self.risk_manager is not None:
                    self.risk_manager.record_close(signal.pair, 0.0)
                return None

            # Calculate P&L using shared function (single source of truth)
            entry_price = open_trade.get("entry_price", fill_price)
            entry_amount = open_trade.get("amount", filled_amount)
            position_type = open_trade.get("position_type", signal.position_type)
            trade_leverage = open_trade.get("leverage", signal.leverage) or 1
            exchange_id = open_trade.get("exchange", signal.exchange_id)

            # Determine fee rates for this trade
            if exchange_id == "delta":
                entry_order_type = open_trade.get("order_type", "market")
                entry_fee_rate = self._delta_maker_fee if entry_order_type == "limit" else self._delta_taker_fee
                exit_fee_rate = self._delta_taker_fee  # exits always market
            else:
                entry_fee_rate = self._binance_taker_fee
                exit_fee_rate = self._binance_taker_fee

            result = calc_pnl(
                entry_price, fill_price, entry_amount,
                position_type, trade_leverage,
                exchange_id, signal.pair,
                entry_fee_rate=entry_fee_rate,
                exit_fee_rate=exit_fee_rate,
            )
            pnl = result.net_pnl
            pnl_pct = result.pnl_pct

            trade_id = open_trade["id"]

            close_data: dict[str, Any] = {
                "status": "closed",
                "exit_price": fill_price,
                "closed_at": iso_now(),
                "pnl": round(pnl, 8),
                "pnl_pct": round(pnl_pct, 4),
                "gross_pnl": round(result.gross_pnl, 8),
                "entry_fee": round(result.entry_fee, 8),
                "exit_fee": round(result.exit_fee, 8),
                "reason": signal.reason,
                "exit_reason": _extract_exit_reason(signal.reason),
                "position_state": None,  # no longer open
            }
            # Persist peak_pnl from signal metadata (final value at close time)
            if signal.metadata.get("peak_pnl") is not None:
                close_data["peak_pnl"] = signal.metadata["peak_pnl"]

            # ── Slippage detection ──────────────────────────────────────
            expected_price = signal.price
            if expected_price and expected_price > 0:
                slippage_pct = abs(fill_price - expected_price) / expected_price * 100
            else:
                slippage_pct = 0.0

            if slippage_pct > 0:
                close_data["slippage_pct"] = round(slippage_pct, 4)
            if slippage_pct >= SLIPPAGE_FLAG_PCT:
                close_data["slippage_flag"] = True

            is_futures = position_type in ("long", "short")
            if is_futures and slippage_pct >= SLIPPAGE_WARN_PCT:
                logger.warning(
                    "SLIPPAGE_ALERT: %s %s expected=$%.2f fill=$%.2f slip=%.2f%% "
                    "(%s, trade_id=%s)",
                    position_type, signal.pair, expected_price, fill_price,
                    slippage_pct, signal.exchange_id, trade_id,
                )
                if self.alerts:
                    await self.alerts.send_slippage_alert(
                        pair=signal.pair,
                        expected_price=expected_price,
                        fill_price=fill_price,
                        slippage_pct=slippage_pct,
                        position_type=position_type,
                        exchange=signal.exchange_id,
                    )

            await self.db.update_trade(trade_id, close_data)

            logger.info(
                "Trade closed in DB: id=%s %s %s entry=$%.2f exit=$%.2f "
                "gross=$%.6f fees=$%.6f net=$%.6f (%.2f%%) [%s]",
                trade_id, position_type, signal.pair,
                entry_price, fill_price,
                result.gross_pnl, result.entry_fee + result.exit_fee,
                pnl, pnl_pct, signal.exchange_id,
            )

            # Record P&L in the risk manager
            if self.risk_manager is not None:
                self.risk_manager.record_close(signal.pair, pnl)

            # Return computed values so Telegram uses the SAME numbers
            return {
                "entry_price": entry_price,
                "exit_price": fill_price,
                "pnl": round(pnl, 8),
                "pnl_pct": round(pnl_pct, 4),
                "opened_at": open_trade.get("opened_at") or open_trade.get("created_at"),
            }

        except Exception:
            logger.exception("Failed to close trade in DB")
            # Still clean up risk manager — strategy already set in_position=False,
            # so without this the entry would persist as a ghost in open_positions
            if self.risk_manager is not None:
                self.risk_manager.record_close(signal.pair, 0.0)
            return None

    async def _notify_trade_opened(self, signal: Signal, order: dict) -> None:
        """Telegram notification for a new position opened."""
        if self.alerts is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount
            # For Delta futures: amount is in contracts, convert to coin for value display
            # For Delta options: 1 contract = 1 unit (premium-based)
            coin_qty = filled_amount
            if signal.exchange_id == "delta" and not is_option_symbol(signal.pair):
                contract_size = DELTA_CONTRACT_SIZE.get(signal.pair, 0.01)
                coin_qty = filled_amount * contract_size
            value = fill_price * coin_qty

            # Get TP/SL from signal metadata (scalp strategy sets these)
            tp_price = signal.metadata.get("tp_price")
            sl_price = signal.metadata.get("sl_price")

            await self.alerts.send_trade_opened(
                pair=signal.pair,
                side=signal.side,
                price=fill_price,
                amount=filled_amount,
                value=value,
                strategy=signal.strategy.value,
                reason=signal.reason,
                exchange=signal.exchange_id,
                leverage=signal.leverage,
                position_type=signal.position_type,
                tp_price=tp_price,
                sl_price=sl_price,
            )
        except Exception:
            logger.exception("Failed to send trade opened alert")

    async def _notify_trade_closed(
        self, signal: Signal, order: dict, close_result: dict | None = None,
    ) -> None:
        """Telegram notification for a position closed, with P&L.

        close_result is the dict returned by _close_trade_in_db containing
        the authoritative P&L values. This ensures Telegram shows the SAME
        numbers that were saved to the database.
        """
        if self.alerts is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price

            # Use the P&L computed by _close_trade_in_db (single source of truth)
            if close_result:
                entry_price = close_result.get("entry_price", fill_price)
                pnl = close_result.get("pnl", 0.0) or 0.0
                pnl_pct = close_result.get("pnl_pct", 0.0) or 0.0
                # Duration from open → close
                opened_at = close_result.get("opened_at")
                duration_min: float | None = None
                if opened_at:
                    from datetime import datetime
                    try:
                        t_open = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                        duration_min = (datetime.now(t_open.tzinfo) - t_open).total_seconds() / 60
                    except Exception:
                        pass
            else:
                # Fallback: try DB query (should rarely happen)
                entry_price = fill_price
                pnl = 0.0
                pnl_pct = 0.0
                duration_min = None
                if self.db is not None:
                    closed_trade = await self.db.get_latest_closed_trade(
                        pair=signal.pair,
                        exchange=signal.exchange_id,
                    )
                    if closed_trade:
                        entry_price = closed_trade.get("entry_price", fill_price)
                        pnl = closed_trade.get("pnl", 0.0) or 0.0
                        pnl_pct = closed_trade.get("pnl_pct", 0.0) or 0.0

            await self.alerts.send_trade_closed(
                pair=signal.pair,
                entry_price=entry_price,
                exit_price=fill_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                duration_min=duration_min,
                exchange=signal.exchange_id,
                leverage=signal.leverage,
                position_type=signal.position_type,
                exit_reason=signal.reason,
            )
        except Exception:
            logger.exception("Failed to send trade closed alert")

    @staticmethod
    def _humanize_error(error: Exception | str | None) -> str:
        """Convert raw exchange errors into human-readable messages."""
        if error is None:
            return "Unknown error"
        err_str = str(error).lower()
        if "no_position" in err_str or "reduce_only" in err_str:
            return "Position already closed on exchange"
        if "insufficient" in err_str:
            return "Insufficient balance for order"
        if "min_notional" in err_str:
            return "Order too small (below exchange minimum)"
        if "rate_limit" in err_str or "too many" in err_str:
            return "Rate limited by exchange — will retry"
        if "timeout" in err_str or "timed out" in err_str:
            return "Exchange connection timed out"
        if "maintenance" in err_str:
            return "Exchange under maintenance"
        # Fallback: truncate to readable length, strip JSON
        raw = str(error)
        # Strip JSON blobs: anything between { and }
        import re
        raw = re.sub(r'\{[^}]{50,}\}', '(details omitted)', raw)
        return raw[:120] if len(raw) > 120 else raw

    async def _notify_error(self, signal: Signal, error: str) -> None:
        """Send error alert to Telegram, with 5-minute dedup per pair.

        Clean 3-line format, no raw JSON, human-readable messages.
        """
        if self.alerts is None:
            return
        # Deduplicate: same pair + similar error within 5 minutes → log only
        error_key = f"{signal.pair}:{type(error).__name__ if isinstance(error, Exception) else str(error)[:50]}"
        now = time.monotonic()
        last = self._last_error_alert.get(signal.pair)
        if last and last[0] == error_key and (now - last[1]) < self._ERROR_DEDUP_SECONDS:
            logger.debug(
                "Suppressed duplicate error alert for %s (last sent %.0fs ago)",
                signal.pair, now - last[1],
            )
            return
        self._last_error_alert[signal.pair] = (error_key, now)
        try:
            pair_short = signal.pair.split("/")[0] if "/" in signal.pair else signal.pair
            human_error = self._humanize_error(error)
            msg = (
                f"\u26a0\ufe0f {signal.side.upper()} {pair_short} failed\n"
                f"{human_error}\n"
                f"No action needed — bot handling it."
            )
            await self.alerts.send_text(msg)
        except Exception:
            logger.exception("Failed to send error alert")

    async def _notify_exit_failure(self, signal: Signal, error: Exception | None) -> None:
        """Send critical Telegram alert when an exit order fails completely.

        Only sends ONCE per pair — suppressed permanently after the first alert
        to avoid spamming on unsellable dust or stuck positions.
        Clean 3-line message, no raw JSON, no "manual intervention needed".
        """
        pair_key = f"{signal.exchange_id}:{signal.pair}"
        if pair_key in self._exit_failure_alerted:
            logger.debug(
                "EXIT FAILED alert already sent for %s — suppressed", pair_key,
            )
            return
        self._exit_failure_alerted.add(pair_key)

        if self.alerts is None:
            return
        try:
            # Parse error into human-readable message
            error_msg = self._humanize_error(error)
            pair_short = signal.pair.split("/")[0] if "/" in signal.pair else signal.pair
            msg = (
                f"\u26a0\ufe0f Exit failed: {pair_short}\n"
                f"{error_msg}\n"
                f"Bot will retry on next tick."
            )
            await self.alerts.send_text(msg)
        except Exception:
            logger.exception("Failed to send exit failure alert")
