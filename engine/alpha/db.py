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
    TABLE_ACTIVITY_LOG = "activity_log"
    TABLE_CHANGELOG = "changelog"

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
        try:
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
            logger.info(
                "Trade logged (id=%s): %s %s %s @ %s on %s",
                row_id, data.get("side"), data.get("pair"),
                data.get("strategy"), data.get("entry_price"), data.get("exchange"),
            )
            return row_id
        except Exception as e:
            logger.error(
                "DB INSERT FAILED for trade: %s | pair=%s side=%s strategy=%s exchange=%s | %s",
                type(e).__name__, data.get("pair"), data.get("side"),
                data.get("strategy"), data.get("exchange"), e,
            )
            return None

    async def close_trade(
        self, order_id: str, exit_price: float, pnl: float, pnl_pct: float,
        reason: str = "", exit_reason: str = "",
    ) -> None:
        """Mark a trade as closed with exit price and realised P&L."""
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        data: dict[str, Any] = {
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "status": "closed",
            "closed_at": iso_now(),
        }
        if reason:
            data["reason"] = reason
        if exit_reason:
            data["exit_reason"] = exit_reason
        await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update(data)
                .eq("order_id", order_id)
                .execute()
            ),
        )
        logger.debug("Trade closed: order_id=%s pnl=%.8f reason=%s", order_id, pnl, reason)

    async def update_trade(self, trade_id: int, data: dict[str, Any]) -> None:
        """Update an existing trade row by its Supabase row ID."""
        if not self.is_connected:
            logger.warning("update_trade: DB not connected, skipping id=%s data=%s", trade_id, data)
            return
        loop = asyncio.get_running_loop()

        def _do_update() -> Any:
            result = (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .update(data)
                .eq("id", trade_id)
                .execute()
            )
            # Verify the update actually matched a row
            if not result.data:
                logger.error(
                    "update_trade: NO ROWS UPDATED for id=%s — data=%s (row may not exist or RLS blocked)",
                    trade_id, data,
                )
            else:
                row = result.data[0]
                logger.info(
                    "update_trade OK: id=%s status=%s pnl=%s pnl_pct=%s exit_price=%s",
                    trade_id, row.get("status"), row.get("pnl"), row.get("pnl_pct"), row.get("exit_price"),
                )
            return result

        await loop.run_in_executor(None, _do_update)

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

    async def get_latest_closed_trade(
        self, pair: str, exchange: str,
    ) -> dict[str, Any] | None:
        """Get the most recently closed trade for a pair+exchange."""
        if not self.is_connected:
            return None
        loop = asyncio.get_running_loop()

        def _query() -> Any:
            return (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("*")
                .eq("pair", pair)
                .eq("exchange", exchange)
                .eq("status", "closed")
                .order("closed_at", desc=True)
                .limit(1)
                .execute()
            )

        result = await loop.run_in_executor(None, _query)
        return result.data[0] if result.data else None

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

    # Core columns guaranteed to exist in every bot_status table.
    # If the full insert fails (e.g. new columns missing from Supabase),
    # we retry with ONLY these so the dashboard never goes completely stale.
    _BOT_STATUS_CORE_KEYS = frozenset({
        "timestamp", "total_pnl", "daily_pnl", "daily_loss_pct", "win_rate",
        "total_trades", "open_positions", "active_strategy", "market_condition",
        "capital", "pair", "is_running", "is_paused", "pause_reason",
        "binance_balance", "delta_balance", "delta_balance_inr",
        "bybit_balance", "kraken_balance",
        "binance_connected", "delta_connected",
        "bybit_connected", "kraken_connected",
        "bot_state", "shorting_enabled", "leverage",
    })

    async def save_bot_status(self, status: dict[str, Any]) -> None:
        if not self.is_connected:
            return
        status.setdefault("timestamp", iso_now())
        try:
            await self._insert_strict(self.TABLE_BOT_STATUS, status)
        except Exception:
            # Full insert failed — likely missing columns in Supabase.
            # Retry with only core fields so dashboard still gets fresh data.
            core = {k: v for k, v in status.items() if k in self._BOT_STATUS_CORE_KEYS}
            logger.warning(
                "[DB] Full bot_status insert failed — retrying with %d core fields "
                "(run supabase/fix_schema_cache.sql to fix permanently)",
                len(core),
            )
            try:
                await self._insert_strict(self.TABLE_BOT_STATUS, core)
                logger.info("[DB] Core bot_status insert succeeded")
            except Exception:
                logger.exception("[DB] Even core bot_status insert failed")

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

    # ── Aggregated trade stats (source of truth for dashboard) ───────────────

    async def get_trade_stats(self) -> dict[str, Any]:
        """Query actual P&L stats from the trades table.

        Returns dict with total_pnl, win_rate, total_trades.
        This is the SOURCE OF TRUTH — never trust in-memory calculations.
        """
        if not self.is_connected:
            return {"total_pnl": 0, "win_rate": 0, "total_trades": 0}

        loop = asyncio.get_running_loop()

        # Get all closed trades
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("pnl")
                .eq("status", "closed")
                .execute()
            ),
        )
        rows = result.data or []

        total_pnl = sum(float(r.get("pnl", 0) or 0) for r in rows)
        total_trades = len(rows)
        wins = sum(1 for r in rows if float(r.get("pnl", 0) or 0) > 0)
        win_rate = round((wins / total_trades * 100), 2) if total_trades > 0 else 0

        return {
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "total_trades": total_trades,
        }

    async def get_today_trade_stats(self, previous_day: bool = False) -> dict[str, Any]:
        """Query closed trade stats for an IST day.

        Args:
            previous_day: If True, query the day that just ended (yesterday IST).
                          Used by _daily_reset which fires at midnight IST —
                          at that moment "today" has 0 trades, so we need yesterday.

        Returns dict with total_trades, wins, losses, daily_pnl, pnl_by_pair,
        best_trade, worst_trade. This survives bot restarts — reads from DB.
        """
        if not self.is_connected:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "daily_pnl": 0.0, "pnl_by_pair": {},
                "best_trade": None, "worst_trade": None,
            }

        loop = asyncio.get_running_loop()

        # Get start of today in IST (UTC+5:30)
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(ist)
        today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

        if previous_day:
            # Daily report fires at midnight IST — we want the day that just ended
            day_end_ist = today_start_ist  # midnight = end of previous day
            day_start_ist = day_end_ist - timedelta(days=1)
            start_utc = day_start_ist.astimezone(timezone.utc).isoformat()
            end_utc = day_end_ist.astimezone(timezone.utc).isoformat()
        else:
            start_utc = today_start_ist.astimezone(timezone.utc).isoformat()
            end_utc = None  # no upper bound — current day, still ongoing

        def _query() -> Any:
            q = (
                self._client.table(self.TABLE_TRADES)  # type: ignore[union-attr]
                .select("pair, pnl")
                .eq("status", "closed")
                .gte("closed_at", start_utc)
            )
            if end_utc is not None:
                q = q.lt("closed_at", end_utc)
            return q.execute()

        result = await loop.run_in_executor(None, _query)
        rows = result.data or []

        total_trades = len(rows)
        daily_pnl = 0.0
        wins = 0
        losses = 0
        pnl_by_pair: dict[str, float] = {}

        for r in rows:
            pnl_val = float(r.get("pnl", 0) or 0)
            pair = r.get("pair", "unknown")
            daily_pnl += pnl_val
            pnl_by_pair[pair] = pnl_by_pair.get(pair, 0.0) + pnl_val
            if pnl_val >= 0:
                wins += 1
            else:
                losses += 1

        win_rate = round((wins / total_trades * 100), 2) if total_trades > 0 else -1.0
        best_trade = None
        worst_trade = None
        if pnl_by_pair:
            best_pair = max(pnl_by_pair, key=pnl_by_pair.get)  # type: ignore[arg-type]
            worst_pair = min(pnl_by_pair, key=pnl_by_pair.get)  # type: ignore[arg-type]
            best_trade = {"pair": best_pair, "pnl": pnl_by_pair[best_pair]}
            worst_trade = {"pair": worst_pair, "pnl": pnl_by_pair[worst_pair]}

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "daily_pnl": daily_pnl,
            "win_rate": win_rate,
            "pnl_by_pair": pnl_by_pair,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }

    # ── Control Panel config (pair_config, setup_config, signal_state) ──────

    async def get_pair_configs(self) -> dict[str, dict[str, Any]]:
        """Fetch all pair_config rows. Returns {pair: {enabled, allocation_pct}}."""
        if not self.is_connected:
            return {}
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table("pair_config")  # type: ignore[union-attr]
                .select("*")
                .execute()
            ),
        )
        return {
            r["pair"]: {"enabled": r["enabled"], "allocation_pct": r["allocation_pct"]}
            for r in (result.data or [])
        }

    async def get_setup_configs(self) -> dict[str, bool]:
        """Fetch all setup_config rows. Returns {setup_type: enabled}."""
        if not self.is_connected:
            return {}
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: (
                self._client.table("setup_config")  # type: ignore[union-attr]
                .select("*")
                .execute()
            ),
        )
        return {r["setup_type"]: r["enabled"] for r in (result.data or [])}

    async def upsert_signal_state(self, pair: str, signals: list[dict[str, Any]]) -> None:
        """Upsert signal_state rows for a pair.

        Each signal dict: {signal_id, value, threshold, firing, direction}.
        """
        if not self.is_connected:
            return
        now = iso_now()
        rows = [
            {
                "pair": pair,
                "signal_id": s["signal_id"],
                "value": s.get("value"),
                "threshold": s.get("threshold"),
                "firing": s.get("firing", False),
                "direction": s.get("direction", "neutral"),
                "updated_at": now,
            }
            for s in signals
        ]
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: (
                    self._client.table("signal_state")  # type: ignore[union-attr]
                    .upsert(rows, on_conflict="pair,signal_id")
                    .execute()
                ),
            )
        except Exception as e:
            logger.error("signal_state upsert failed for %s: %s", pair, e)

    # ── Options state (dashboard real-time options monitoring) ──────────────

    TABLE_OPTIONS_STATE = "options_state"

    async def upsert_options_state(self, pair: str, state: dict[str, Any]) -> None:
        """Upsert options monitoring state for a pair (BTC or ETH).

        Called every ~30s from options_scalp. Dashboard subscribes via realtime.
        """
        if not self.is_connected:
            return
        state["pair"] = pair
        state["updated_at"] = iso_now()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: (
                    self._client.table(self.TABLE_OPTIONS_STATE)  # type: ignore[union-attr]
                    .upsert(state, on_conflict="pair")
                    .execute()
                ),
            )
        except Exception as e:
            logger.error("options_state upsert failed for %s: %s", pair, e)

    # ── Activity log (live feed events for dashboard) ───────────────────────

    async def log_activity(
        self,
        event_type: str,
        pair: str,
        description: str,
        exchange: str = "delta",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert an activity event visible in the dashboard Live Activity feed.

        event_type: options_entry, options_skip, options_exit, risk_alert, etc.
        """
        if not self.is_connected:
            return
        row: dict[str, Any] = {
            "event_type": event_type,
            "pair": pair,
            "description": description,
            "exchange": exchange,
            "created_at": iso_now(),
        }
        if metadata:
            row["metadata"] = metadata
        await self._insert(self.TABLE_ACTIVITY_LOG, row)

    # ── Changelog ──────────────────────────────────────────────────────────

    async def get_latest_changelog(
        self, change_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the most recent changelog entry, optionally filtered by type."""
        if not self.is_connected:
            return None
        loop = asyncio.get_running_loop()

        def _query() -> Any:
            q = (
                self._client.table(self.TABLE_CHANGELOG)  # type: ignore[union-attr]
                .select("*")
                .order("created_at", desc=True)
                .limit(1)
            )
            if change_type:
                q = q.eq("change_type", change_type)
            return q.execute()

        try:
            result = await loop.run_in_executor(None, _query)
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error("get_latest_changelog failed: %s", e)
            return None

    async def log_changelog(self, data: dict[str, Any]) -> int | None:
        """Insert a changelog entry. Returns the row ID."""
        if not self.is_connected:
            return None
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: (
                    self._client.table(self.TABLE_CHANGELOG)  # type: ignore[union-attr]
                    .insert(data)
                    .execute()
                ),
            )
            row_id = result.data[0].get("id") if result.data else None
            logger.info(
                "Changelog logged (id=%s): [%s] %s",
                row_id, data.get("change_type"), data.get("title"),
            )
            return row_id
        except Exception as e:
            logger.error("Changelog INSERT failed: %s | %s", type(e).__name__, e)
            return None

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _insert(self, table: str, data: dict[str, Any]) -> None:
        try:
            await self._insert_strict(table, data)
        except Exception as e:
            logger.error(
                "DB INSERT FAILED for %s: %s | pair=%s | %s",
                table, type(e).__name__, data.get("pair", "?"), e,
            )

    async def _insert_strict(self, table: str, data: dict[str, Any]) -> None:
        """Insert that re-raises on failure (caller handles retry logic)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.table(table).insert(data).execute(),  # type: ignore[union-attr]
        )
