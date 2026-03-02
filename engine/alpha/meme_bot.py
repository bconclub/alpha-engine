"""Meme Bot — sends a quick one-liner after losing trades.

No APIs, no images. Just short trading jokes to lighten the mood.
"""

from __future__ import annotations

import random

from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import setup_logger

logger = setup_logger("meme_bot")

# Short, punchy one-liners. No fluff.
LOSS_LINES = [
    "Buy high, sell low. As is tradition.",
    "That wasn't a loss, that was tuition.",
    "The market can stay irrational longer than you can stay solvent.",
    "At least it wasn't leverage... oh wait.",
    "Pain is temporary. Bags are forever.",
    "You miss 100% of the trades you don't take. You also lose 100% of the ones you do.",
    "It's called a stop loss, not a stop profit.",
    "Think of it as a donation to the market makers.",
    "Every loss brings you closer to a win. Statistically. Maybe.",
    "The real gains were the lessons we learned along the way.",
    "Portfolio diversification: losing money on multiple assets.",
    "This is why we don't tell our wives.",
    "Another day, another dollar... gone.",
    "Zoom out. No, further. Keep going.",
    "Sir, this is a casino.",
    "Cost of doing business.",
    "The chart looked different 5 minutes ago.",
    "Diamond hands would've made this worse.",
    "Small loss. Next trade. Move on.",
    "Even Buffett has red days.",
]


async def send_meme(bot: Bot | None = None, chat_id: str | None = None) -> bool:
    """Send a quick one-liner after a losing trade."""
    if bot is None:
        token = config.telegram.bot_token
        chat_id = chat_id or config.telegram.chat_id
        if not token or not chat_id:
            return False
        bot = Bot(token=token)

    chat_id = chat_id or config.telegram.chat_id
    line = random.choice(LOSS_LINES)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"\U0001f60f {line}",
        )
        return True
    except Exception:
        return False
