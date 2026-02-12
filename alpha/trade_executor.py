"""Trade executor — places orders via ccxt with retry logic and logging."""

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
    ) -> None:
        self.exchange = exchange
        self.db = db  # alpha.db.Database
        self.alerts = alerts  # alpha.alerts.AlertManager

    async def execute(self, signal: Signal) -> dict | None:
        """Place an order for the given signal, with retry + logging."""
        logger.info(
            "Executing %s %s %s %.8f @ %.2f [%s] — %s",
            signal.order_type, signal.side, signal.pair,
            signal.amount, signal.price, signal.strategy.value, signal.reason,
        )

        order: dict | None = None
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if signal.order_type == "market":
                    order = await self.exchange.create_order(
                        symbol=signal.pair,
                        type="market",
                        side=signal.side,
                        amount=signal.amount,
                    )
                else:
                    order = await self.exchange.create_order(
                        symbol=signal.pair,
                        type="limit",
                        side=signal.side,
                        amount=signal.amount,
                        price=signal.price,
                    )
                break
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                last_error = e
                delay = BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Order attempt %d/%d failed (retryable): %s — retrying in %.1fs",
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
            "Order filled: id=%s %s %s %.8f @ %.2f",
            order_id, signal.side, signal.pair, filled_amount, fill_price,
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
                "exchange": signal.metadata.get("buy_exchange", "binance"),
                "status": "open" if signal.side == "buy" else "closed",
                "reason": signal.reason,
                "order_id": order.get("id"),
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
            )
        except Exception:
            logger.exception("Failed to send trade alert")

    async def _notify_error(self, signal: Signal, error: str) -> None:
        if self.alerts is None:
            return
        try:
            await self.alerts.send_error_alert(
                f"Order failed: {signal.side} {signal.pair} — {error}"
            )
        except Exception:
            logger.exception("Failed to send error alert")
