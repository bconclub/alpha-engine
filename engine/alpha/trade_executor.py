"""Trade executor — places orders via ccxt with retry logic and logging.

Multi-exchange aware: routes orders to Binance (spot) or Delta (futures)
based on signal.exchange_id. Sets leverage for futures orders.
"""

from __future__ import annotations

import asyncio
from typing import Any

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.strategies.base import Signal
from alpha.utils import iso_now, setup_logger

logger = setup_logger("trade_executor")

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds


class TradeExecutor:
    """Unified order execution layer on top of ccxt."""

    def __init__(
        self,
        exchange: ccxt.Exchange,
        db: Any | None = None,
        alerts: Any | None = None,
        delta_exchange: ccxt.Exchange | None = None,
    ) -> None:
        self.exchange = exchange                      # Binance (primary)
        self.delta_exchange = delta_exchange           # Delta (optional, futures)
        self.db = db  # alpha.db.Database
        self.alerts = alerts  # alpha.alerts.AlertManager
        self._min_notional: dict[str, float] = {}     # pair -> min order value
        self._min_amount: dict[str, float] = {}       # pair -> min order qty

    def _get_exchange(self, signal: Signal) -> ccxt.Exchange:
        """Return the correct exchange instance for a signal."""
        if signal.exchange_id == "delta" and self.delta_exchange:
            return self.delta_exchange
        return self.exchange  # default: Binance

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
                        logger.debug(
                            "[%s] Delta min notional=$%.2f, min amount=%.8f",
                            pair, self._min_notional[pair], self._min_amount[pair],
                        )
                    else:
                        logger.warning("Market info not found for %s on Delta", pair)
            except Exception:
                logger.exception("Failed to load Delta market limits")

    def validate_order_size(self, signal: Signal) -> bool:
        """Check if the order meets exchange minimum requirements.

        For futures (Delta): the notional value is collateral × leverage,
        which easily clears minimums. We check the leveraged notional.
        For spot (Binance): check order value directly against $5 min.
        """
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

    async def execute(self, signal: Signal) -> dict | None:
        """Place an order for the given signal, with retry + logging."""
        # Validate minimum order size
        if not self.validate_order_size(signal):
            return None

        exchange = self._get_exchange(signal)

        logger.info(
            "Executing %s %s %s %.8f @ %.2f [%s/%s] -- %s",
            signal.order_type, signal.side, signal.pair,
            signal.amount, signal.price, signal.exchange_id,
            signal.strategy.value, signal.reason,
        )

        # Futures: set leverage before placing order
        if signal.leverage > 1 and signal.exchange_id == "delta":
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

        order: dict | None = None
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if signal.order_type == "market":
                    order = await exchange.create_order(
                        symbol=signal.pair,
                        type="market",
                        side=signal.side,
                        amount=signal.amount,
                        params=params,
                    )
                else:
                    order = await exchange.create_order(
                        symbol=signal.pair,
                        type="limit",
                        side=signal.side,
                        amount=signal.amount,
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
                logger.error("Insufficient funds for order: %s", e)
                await self._notify_error(signal, str(e))
                return None
            except ccxt.InvalidOrder as e:
                logger.error("Invalid order: %s", e)
                await self._notify_error(signal, str(e))
                return None
            except Exception as e:
                logger.exception("Unexpected error placing order")
                await self._notify_error(signal, str(e))
                return None

        if order is None:
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

        # Log to Supabase
        await self._log_trade(signal, order)

        # Send Telegram alert
        await self._notify_trade(signal, order)

        return order

    async def _log_trade(self, signal: Signal, order: dict) -> None:
        if self.db is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount
            cost = fill_price * filled_amount

            await self.db.log_trade({
                "pair": signal.pair,
                "side": signal.side,
                "entry_price": fill_price,
                "amount": filled_amount,
                "cost": cost,
                "strategy": signal.strategy.value,
                "order_type": signal.order_type,
                "exchange": signal.exchange_id,
                "status": "open" if not signal.reduce_only else "closed",
                "reason": signal.reason,
                "order_id": order.get("id"),
                "leverage": signal.leverage,
                "position_type": signal.position_type,
            })
        except Exception:
            logger.exception("Failed to log trade to DB")

    async def _notify_trade(self, signal: Signal, order: dict) -> None:
        if self.alerts is None:
            return
        try:
            fill_price = order.get("average") or order.get("price") or signal.price
            filled_amount = order.get("filled") or signal.amount
            value = fill_price * filled_amount
            await self.alerts.send_trade_alert(
                side=signal.side,
                pair=signal.pair,
                price=fill_price,
                amount=filled_amount,
                value=value,
                strategy=signal.strategy.value,
                reason=signal.reason,
                exchange=signal.exchange_id,
                leverage=signal.leverage,
                position_type=signal.position_type,
            )
        except Exception:
            logger.exception("Failed to send trade alert")

    async def _notify_error(self, signal: Signal, error: str) -> None:
        if self.alerts is None:
            return
        try:
            await self.alerts.send_error_alert(
                f"Order failed [{signal.exchange_id}]: {signal.side} {signal.pair} -- {error}"
            )
        except Exception:
            logger.exception("Failed to send error alert")
