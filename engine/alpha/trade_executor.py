"""Trade executor — places orders via ccxt with retry logic and logging.

Multi-exchange aware: routes orders to Binance (spot) or Delta (futures)
based on signal.exchange_id. Sets leverage for futures orders.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.strategies.base import Signal
from alpha.utils import iso_now, setup_logger

logger = setup_logger("trade_executor")

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds

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
        """Ensure Binance spot ENTRY orders meet the $5 minimum notional.

        If order value < $5.01, bump the amount up to 5.01 / price.
        Also round amount up to the exchange's LOT_SIZE step if available.
        Skips exit orders — exits sell what was bought, even if below minimum.
        """
        if signal.exchange_id != "binance" or signal.position_type != "spot":
            return signal
        # Don't bump exit orders — sell what you have
        if self._is_exit_order(signal):
            return signal

        order_value = signal.price * signal.amount
        min_required = 5.01  # Binance $5 min + buffer

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
                    leverage=signal.leverage,
                    position_type=signal.position_type,
                    reduce_only=signal.reduce_only,
                    exchange_id=signal.exchange_id,
                )

        # Enforce Binance $5.01 minimum notional for ENTRY orders only
        if not is_exit:
            signal = self._enforce_binance_min(signal)

        # Convert Delta coin amounts to integer contracts BEFORE validation
        # Skip for option symbols — options use their own contract sizing (1 contract = 1 option)
        if signal.exchange_id == "delta" and not self._is_option_symbol(signal.pair):
            contracts = self._to_delta_contracts(signal.pair, signal.amount, signal.price)
            logger.info("[%s] Delta: %.8f coins -> %d contracts", signal.pair, signal.amount, contracts)
            signal = Signal(
                side=signal.side, price=signal.price,
                amount=float(contracts),
                order_type=signal.order_type, reason=signal.reason,
                strategy=signal.strategy, pair=signal.pair,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
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
                if is_exit and signal.exchange_id == "binance" and "MIN_NOTIONAL" in str(e).upper():
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

        if is_exit:
            # EXIT: update the existing open trade row in DB
            await self._close_trade_in_db(signal, order)
        else:
            # ENTRY: insert a new trade row in DB
            await self._open_trade_in_db(signal, order)

        # Send Telegram alert (different message for entry vs exit)
        if is_exit:
            await self._notify_trade_closed(signal, order)
        else:
            await self._notify_trade_opened(signal, order)

        return order

    async def _open_trade_in_db(self, signal: Signal, order: dict) -> None:
        """INSERT a new trade row for an entry/open position."""
        if self.db is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount

            # For Delta: filled_amount is in contracts. Convert to coin for notional calc.
            coin_qty = filled_amount
            if signal.exchange_id == "delta":
                contract_size = DELTA_CONTRACT_SIZE.get(signal.pair, 0.01)
                coin_qty = filled_amount * contract_size  # 1 contract × 0.01 = 0.01 ETH

            notional = fill_price * coin_qty
            # Cost = collateral (actual capital at risk)
            is_futures = signal.leverage > 1 and signal.position_type in ("long", "short")
            cost = notional / signal.leverage if is_futures else notional

            trade_id = await self.db.log_trade({
                "pair": signal.pair,
                "side": signal.side,
                "entry_price": fill_price,
                "amount": filled_amount,
                "cost": cost,
                "strategy": signal.strategy.value,
                "order_type": signal.order_type,
                "exchange": signal.exchange_id,
                "status": "open",
                "reason": signal.reason,
                "order_id": order.get("id"),
                "leverage": signal.leverage,
                "position_type": signal.position_type,
            })
            logger.info(
                "Trade opened in DB: id=%s %s %s @ $%.2f [%s]",
                trade_id, signal.side, signal.pair, fill_price, signal.exchange_id,
            )
        except Exception:
            logger.exception("Failed to log open trade to DB")

    async def _close_trade_in_db(self, signal: Signal, order: dict) -> None:
        """UPDATE the existing open trade row with exit price and P&L."""
        if self.db is None:
            return
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
                return

            # Calculate P&L from the original entry
            entry_price = open_trade.get("entry_price", fill_price)
            entry_amount = open_trade.get("amount", filled_amount)
            position_type = open_trade.get("position_type", signal.position_type)
            trade_leverage = open_trade.get("leverage", signal.leverage) or 1

            # PnL calculation:
            # LONG/spot buy-then-sell: (exit - entry) * coin_amount
            # SHORT sell-then-buy: (entry - exit) * coin_amount
            #
            # IMPORTANT: For Delta futures, entry_amount is in CONTRACTS (e.g. 1),
            # not coin amount (e.g. 0.01 ETH). Must convert back using contract_size.
            #
            # Fees are deducted: round-trip fee = (entry_notional + exit_notional) * taker_rate
            coin_amount = entry_amount
            exchange_id = open_trade.get("exchange", signal.exchange_id)
            if exchange_id == "delta":
                contract_size = DELTA_CONTRACT_SIZE.get(signal.pair, 0.01)
                coin_amount = entry_amount * contract_size  # 1 contract × 0.01 = 0.01 ETH

            if position_type in ("long", "spot"):
                gross_pnl = (fill_price - entry_price) * coin_amount
            else:  # short
                gross_pnl = (entry_price - fill_price) * coin_amount

            # Calculate round-trip trading fees (including GST for Delta India)
            # Entry: maker fee if limit order, taker if market
            # Exit: always taker (market orders for speed)
            entry_notional = entry_price * coin_amount
            exit_notional = fill_price * coin_amount
            if exchange_id == "delta":
                entry_order_type = open_trade.get("order_type", "market")
                entry_fee_rate = self._delta_maker_fee if entry_order_type == "limit" else self._delta_taker_fee
                exit_fee_rate = self._delta_taker_fee  # exits always market
            else:
                entry_fee_rate = self._binance_taker_fee
                exit_fee_rate = self._binance_taker_fee
            entry_fee = entry_notional * entry_fee_rate
            exit_fee = exit_notional * exit_fee_rate
            total_fees = entry_fee + exit_fee
            fee_rate = total_fees / (entry_notional + exit_notional) if (entry_notional + exit_notional) > 0 else 0

            # Net P&L = gross - fees
            pnl = gross_pnl - total_fees

            # P&L percentage is against COLLATERAL (actual capital at risk)
            # For futures: collateral = notional / leverage
            notional_cost = entry_notional
            if trade_leverage > 1:
                collateral = notional_cost / trade_leverage
            else:
                collateral = notional_cost
            pnl_pct = (pnl / collateral * 100) if collateral > 0 else 0.0

            trade_id = open_trade["id"]

            await self.db.update_trade(trade_id, {
                "status": "closed",
                "exit_price": fill_price,
                "closed_at": iso_now(),
                "pnl": round(pnl, 8),
                "pnl_pct": round(pnl_pct, 4),
                "reason": signal.reason,
            })

            logger.info(
                "Trade closed in DB: id=%s %s %s entry=$%.2f exit=$%.2f "
                "gross=$%.6f fees=$%.6f net=$%.6f (%.2f%%) [%s] fee_rate=%.4f%%",
                trade_id, position_type, signal.pair,
                entry_price, fill_price,
                gross_pnl, total_fees, pnl, pnl_pct, signal.exchange_id,
                fee_rate * 100,
            )

            # Record P&L in the risk manager
            if self.risk_manager is not None:
                self.risk_manager.record_close(signal.pair, pnl)

        except Exception:
            logger.exception("Failed to close trade in DB")

    async def _notify_trade_opened(self, signal: Signal, order: dict) -> None:
        """Telegram notification for a new position opened."""
        if self.alerts is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount
            # For Delta: amount is in contracts, convert to coin for value display
            coin_qty = filled_amount
            if signal.exchange_id == "delta":
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

    async def _notify_trade_closed(self, signal: Signal, order: dict) -> None:
        """Telegram notification for a position closed, with P&L."""
        if self.alerts is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price

            # Look up the entry from the DB to compute P&L
            entry_price = fill_price  # fallback
            pnl = 0.0
            pnl_pct = 0.0
            duration_min: float | None = None

            if self.db is not None:
                # The trade was just closed in _close_trade_in_db — fetch it
                closed_trade = await self.db.get_latest_closed_trade(
                    pair=signal.pair,
                    exchange=signal.exchange_id,
                )
                if closed_trade:
                    entry_price = closed_trade.get("entry_price", fill_price)
                    pnl = closed_trade.get("pnl", 0.0) or 0.0
                    pnl_pct = closed_trade.get("pnl_pct", 0.0) or 0.0
                    # Duration from timestamps
                    created = closed_trade.get("created_at") or closed_trade.get("timestamp")
                    closed_at = closed_trade.get("closed_at")
                    if created and closed_at:
                        from datetime import datetime
                        try:
                            t_open = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            t_close = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                            duration_min = (t_close - t_open).total_seconds() / 60
                        except Exception:
                            pass

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
            )
        except Exception:
            logger.exception("Failed to send trade closed alert")

    async def _notify_error(self, signal: Signal, error: str) -> None:
        """Send error alert to Telegram, with 5-minute dedup per pair."""
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
            await self.alerts.send_error_alert(
                f"Order failed [{signal.exchange_id}]: {signal.side} {signal.pair} -- {error}"
            )
        except Exception:
            logger.exception("Failed to send error alert")

    async def _notify_exit_failure(self, signal: Signal, error: Exception | None) -> None:
        """Send critical Telegram alert when an exit order fails completely.

        Only sends ONCE per pair — suppressed permanently after the first alert
        to avoid spamming on unsellable dust or stuck positions.
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
            msg = (
                f"\u26a0\ufe0f EXIT FAILED — {signal.pair} stuck in position, "
                f"manual intervention needed!\n"
                f"Side: {signal.side} | Exchange: {signal.exchange_id} | "
                f"Amount: {signal.amount:.8f} | Strategy: {signal.strategy.value}\n"
                f"Error: {error}"
            )
            await self.alerts.send_error_alert(msg)
        except Exception:
            logger.exception("Failed to send exit failure alert")
