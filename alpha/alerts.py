"""Telegram bot notifications for trade alerts and daily summaries."""

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
            logger.warning("Telegram credentials not set — alerts disabled")
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
    ) -> None:
        emoji = "🟢" if side == "buy" else "🔴"
        msg = (
            f"{emoji} *{side.upper()}* {pair}\n"
            f"Price: `{price:,.2f}`\n"
            f"Amount: `{amount:.8f}`\n"
            f"Value: `{format_usd(value)}`\n"
            f"Strategy: `{strategy}`\n"
            f"Reason: _{reason}_"
        )
        await self._send(msg)

    # -- Strategy switch -------------------------------------------------------

    async def send_strategy_switch(
        self, old: str | None, new: str | None, reason: str
    ) -> None:
        msg = (
            f"🔄 *Strategy Switch*\n"
            f"{old or 'none'} → {new or 'paused'}\n"
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
        strategy: str | None,
    ) -> None:
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        msg = (
            f"📊 *Daily Summary*\n"
            f"{pnl_emoji} PnL: `{format_usd(total_pnl)}`\n"
            f"Win Rate: `{win_rate:.1f}%`\n"
            f"Trades: `{trades_count}`\n"
            f"Capital: `{format_usd(capital)}`\n"
            f"Active Strategy: `{strategy or 'none'}`"
        )
        await self._send(msg)

    # -- Risk alerts -----------------------------------------------------------

    async def send_risk_alert(self, message: str) -> None:
        msg = f"⚠️ *RISK ALERT*\n{message}"
        await self._send(msg)

    async def send_error_alert(self, message: str) -> None:
        msg = f"❌ *ERROR*\n`{message}`"
        await self._send(msg)

    # -- Bot lifecycle ---------------------------------------------------------

    async def send_bot_started(self, pair: str, capital: float) -> None:
        msg = (
            f"🤖 *Alpha Bot Started*\n"
            f"Pair: `{pair}`\n"
            f"Capital: `{format_usd(capital)}`"
        )
        await self._send(msg)

    async def send_bot_stopped(self, reason: str) -> None:
        msg = f"🛑 *Alpha Bot Stopped*\n_{reason}_"
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
