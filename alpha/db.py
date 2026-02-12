"""Supabase client for trade logging and bot state persistence."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from supabase import Client, create_client

from alpha.config import config
from alpha.utils import iso_now, setup_logger

logger = setup_logger("db")


class Database:
    """Async-friendly wrapper around the Supabase Python client.

    The supabase-py client is synchronous, so we run DB calls in a thread
    executor to avoid blocking the event loop.
    """

    TABLE_TRADES = "trades"
    TABLE_STRATEGY_LOG = "strategy_log"
    TABLE_BOT_STATUS = "bot_status"

    def __init__(self) -> None:
        self._client: Client | None = None

    async def connect(self) -> None:
        url = config.supabase.url
        key = config.supabase.key
        if not url or not key:
            logger.warning("Supabase credentials not set — DB logging disabled")
            return
        loop = asyncio.get_running_loop()
        self._client = await loop.run_in_executor(None, partial(create_client, url, key))
        logger.info("Connected to Supabase")

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    # -- Trade logging ---------------------------------------------------------

    async def log_trade(self, data: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        await self._insert(self.TABLE_TRADES, data)
        logger.debug("Trade logged: %s %s @ %s", data.get("side"), data.get("pair"), data.get("price"))

    async def update_trade_pnl(self, order_id: str, pnl: float) -> None:
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update({"pnl": pnl, "status": "closed"})
                .eq("order_id", order_id)
                .execute()
            ),
        )

    # -- Strategy log ----------------------------------------------------------

    async def log_strategy_selection(self, data: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        await self._insert(self.TABLE_STRATEGY_LOG, data)

    # -- Bot status ------------------------------------------------------------

    async def save_bot_status(self, status: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        status["timestamp"] = iso_now()
        await self._insert(self.TABLE_BOT_STATUS, status)

    async def get_last_bot_status(self) -> dict[str, Any] | None:
        if not self.is_connected:
            return None
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_BOT_STATUS)  # type: ignore[union-attr]
                .select("*")
                .order("timestamp", desc=True)
                .limit(1)
                .execute()
            ),
        )
        rows = result.data
        return rows[0] if rows else None

    # -- Recent trades for win-rate --------------------------------------------

    async def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.is_connected:
            return []
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("*")
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            ),
        )
        return result.data

    # -- Internal --------------------------------------------------------------

    async def _insert(self, table: str, data: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.table(table).insert(data).execute(),  # type: ignore[union-attr]
        )
