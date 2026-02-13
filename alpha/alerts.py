"""Telegram bot notifications for trade alerts and daily summaries.

Multi-pair + multi-exchange aware: includes exchange name, leverage info,
per-pair P&L breakdown, and liquidation warnings for futures positions.
"""

from __future__ import annotations

from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import format_usd, setup_logger

logger = setup_logger("alerts")


class AlertManager:
    """Sends Telegram messages for trades, strategy switches, and risk events."""

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._chat_id: str = config.telegram.chat_id

    async def connect(self) -> None:
        token = config.telegram.bot_token
        if not token or not self._chat_id:
            logger.warning("Telegram credentials not set -- alerts disabled")
            return
        self._bot = Bot(token=token)
        logger.info("Telegram bot initialized")

    @property
    def is_connected(self) -> bool:
        return self._bot is not None and bool(self._chat_id)

    # -- Trade alerts ----------------------------------------------------------

    async def send_trade_alert(
        self,
        side: str,
        pair: str,
        price: float,
        amount: float,
        value: float,
        strategy: str,
        reason: str,
        exchange: str = "binance",
        leverage: int = 1,
        position_type: str = "spot",
    ) -> None:
        # Emoji: short gets down triangle, buy green, sell red
        if position_type == "short":
            emoji = "\U0001f53b"  # red down-pointing triangle
        elif side == "buy":
            emoji = "\U0001f7e2"  # green circle
        else:
            emoji = "\U0001f534"  # red circle

        # Position badge for futures
        pos_label = ""
        if position_type in ("long", "short"):
            pos_label = f"\nPosition: `{position_type.upper()} {leverage}x`"

        msg = (
            f"{emoji} *{side.upper()}* {pair} [{exchange.upper()}]\n"
            f"Price: `{price:,.2f}`\n"
            f"Amount: `{amount:.8f}`\n"
            f"Value: `{format_usd(value)}`{pos_label}\n"
            f"Strategy: `{strategy}`\n"
            f"Reason: _{reason}_"
        )
        await self._send(msg)

    # -- Strategy switch -------------------------------------------------------

    async def send_strategy_switch(
        self, pair: str, old: str | None, new: str | None, reason: str
    ) -> None:
        msg = (
            f"\U0001f504 *Strategy Switch* [{pair}]\n"
            f"{old or 'none'} \u2192 {new or 'paused'}\n"
            f"Reason: _{reason}_"
        )
        await self._send(msg)

    # -- Daily summary ---------------------------------------------------------

    async def send_daily_summary(
        self,
        total_pnl: float,
        win_rate: float,
        trades_count: int,
        capital: float,
        active_strategies: dict[str, str | None],
        pnl_by_pair: dict[str, float] | None = None,
    ) -> None:
        pnl_emoji = "\U0001f4c8" if total_pnl >= 0 else "\U0001f4c9"
        lines = [
            f"\U0001f4ca *Daily Summary*",
            f"{pnl_emoji} Total PnL: `{format_usd(total_pnl)}`",
            f"Win Rate: `{win_rate:.1f}%`",
            f"Trades: `{trades_count}`",
            f"Capital: `{format_usd(capital)}`",
        ]

        # Per-pair P&L breakdown
        if pnl_by_pair:
            lines.append("")
            lines.append("*Per-pair P&L:*")
            for pair, pnl in sorted(pnl_by_pair.items(), key=lambda x: x[1], reverse=True):
                icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                lines.append(f"  {icon} {pair}: `{format_usd(pnl)}`")

        # Active strategies
        if active_strategies:
            lines.append("")
            lines.append("*Active strategies:*")
            for pair, strat in active_strategies.items():
                lines.append(f"  {pair}: `{strat or 'paused'}`")

        await self._send("\n".join(lines))

    # -- Bot started (multi-pair + multi-exchange) -----------------------------

    async def send_bot_started(self, pairs: list[str], capital: float) -> None:
        pairs_str = ", ".join(f"`{p}`" for p in pairs)
        msg = (
            f"\U0001f916 *Alpha Bot Started*\n"
            f"Pairs: {pairs_str}\n"
            f"Capital: `{format_usd(capital)}`"
        )
        await self._send(msg)

    # -- Risk alerts -----------------------------------------------------------

    async def send_risk_alert(self, message: str) -> None:
        msg = f"\u26a0\ufe0f *RISK ALERT*\n{message}"
        await self._send(msg)

    async def send_error_alert(self, message: str) -> None:
        msg = f"\u274c *ERROR*\n`{message}`"
        await self._send(msg)

    # -- Liquidation warning (futures) -----------------------------------------

    async def send_liquidation_warning(
        self, pair: str, distance_pct: float, position_type: str, leverage: int,
    ) -> None:
        msg = (
            f"\U0001f6a8 *LIQUIDATION WARNING* \U0001f6a8\n"
            f"Pair: `{pair}`\n"
            f"Position: `{position_type.upper()} {leverage}x`\n"
            f"Distance to liquidation: `{distance_pct:.1f}%`\n"
            f"_Consider reducing position or adding margin_"
        )
        await self._send(msg)

    # -- Bot lifecycle ---------------------------------------------------------

    async def send_bot_stopped(self, reason: str) -> None:
        msg = f"\U0001f6d1 *Alpha Bot Stopped*\n_{reason}_"
        await self._send(msg)

    # -- Internal --------------------------------------------------------------

    async def _send(self, text: str) -> None:
        if not self.is_connected:
            logger.debug("Alert (Telegram disabled): %s", text[:100])
            return
        try:
            await self._bot.send_message(  # type: ignore[union-attr]
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Failed to send Telegram message")
