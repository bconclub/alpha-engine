"""Supabase client for trade logging, strategy logs, bot status, and dashboard commands."""

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
    TABLE_BOT_COMMANDS = "bot_commands"

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

    # ── Trades ────────────────────────────────────────────────────────────────

    async def log_trade(self, data: dict[str, Any]) -> int | None:
        """Insert a new trade row and return its Supabase row ID."""
        if not self.is_connected:
            return None
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .insert(data)
                .execute()
            ),
        )
        row_id = result.data[0].get("id") if result.data else None
        logger.debug(
            "Trade logged (id=%s): %s %s %s @ %s",
            row_id, data.get("side"), data.get("pair"),
            data.get("strategy"), data.get("entry_price"),
        )
        return row_id

    async def close_trade(
        self, order_id: str, exit_price: float, pnl: float, pnl_pct: float
    ) -> None:
        """Mark a trade as closed with exit price and realised P&L."""
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update({
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "status": "closed",
                    "closed_at": iso_now(),
                })
                .eq("order_id", order_id)
                .execute()
            ),
        )
        logger.debug("Trade closed: order_id=%s pnl=%.8f", order_id, pnl)

    async def update_trade(self, trade_id: int, data: dict[str, Any]) -> None:
        """Update an existing trade row by its Supabase row ID."""
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update(data)
                .eq("id", trade_id)
                .execute()
            ),
        )
        logger.debug("Trade updated: id=%d, data=%s", trade_id, data)

    async def get_open_trade(
        self, pair: str, exchange: str, strategy: str | None = None,
    ) -> dict[str, Any] | None:
        """Find the most recent open trade for a pair+exchange (optionally filtered by strategy)."""
        if not self.is_connected:
            return None
        loop = asyncio.get_running_loop()

        def _query() -> Any:
            q = (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("*")
                .eq("pair", pair)
                .eq("exchange", exchange)
                .eq("status", "open")
            )
            if strategy:
                q = q.eq("strategy", strategy)
            return q.order("opened_at", desc=True).limit(1).execute()

        result = await loop.run_in_executor(None, _query)
        return result.data[0] if result.data else None

    async def cancel_trade(self, order_id: str, reason: str = "cancelled") -> None:
        """Mark a trade as cancelled."""
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update({"status": "cancelled", "reason": reason, "closed_at": iso_now()})
                .eq("order_id", order_id)
                .execute()
            ),
        )

    async def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch the N most recent trades (newest first)."""
        if not self.is_connected:
            return []
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("*")
                .order("opened_at", desc=True)
                .limit(limit)
                .execute()
            ),
        )
        return result.data

    async def get_open_trades(self, pair: str | None = None) -> list[dict[str, Any]]:
        """Fetch all currently open trades, optionally filtered by pair."""
        if not self.is_connected:
            return []
        loop = asyncio.get_running_loop()

        def _query() -> Any:
            q = (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("*")
                .eq("status", "open")
            )
            if pair:
                q = q.eq("pair", pair)
            return q.order("opened_at", desc=True).execute()

        result = await loop.run_in_executor(None, _query)
        return result.data

    async def get_all_open_trades(self) -> list[dict[str, Any]]:
        """Fetch ALL open trades across all pairs and exchanges."""
        return await self.get_open_trades(pair=None)

    # ── Strategy log ─────────────────────────────────────────────────────────

    async def log_strategy_selection(self, data: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        await self._insert(self.TABLE_STRATEGY_LOG, data)

    # ── Bot status ───────────────────────────────────────────────────────────

    async def save_bot_status(self, status: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        status.setdefault("timestamp", iso_now())
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

    # ── Bot commands (dashboard → bot) ───────────────────────────────────────

    async def poll_pending_commands(self) -> list[dict[str, Any]]:
        """Fetch all unexecuted commands, oldest first."""
        if not self.is_connected:
            return []
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_BOT_COMMANDS)  # type: ignore[union-attr]
                .select("*")
                .eq("executed", False)
                .order("created_at", desc=False)
                .execute()
            ),
        )
        return result.data

    async def mark_command_executed(
        self, command_id: int, result_msg: str = "ok"
    ) -> None:
        """Mark a command as executed."""
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_BOT_COMMANDS)  # type: ignore[union-attr]
                .update({
                    "executed": True,
                    "executed_at": iso_now(),
                    "result": result_msg,
                })
                .eq("id", command_id)
                .execute()
            ),
        )
        logger.info("Command %d marked executed: %s", command_id, result_msg)

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _insert(self, table: str, data: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.table(table).insert(data).execute(),  # type: ignore[union-attr]
        )
