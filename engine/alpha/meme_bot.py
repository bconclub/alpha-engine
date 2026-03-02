"""Meme Bot — sends crypto/trading memes to Telegram.

Uses free APIs (no keys needed):
- meme-api.com (Reddit memes from crypto/trading subreddits)
- icanhazdadjoke.com (dad jokes as fallback)

Run standalone:  python -m alpha.meme_bot
Run scheduled:   Called from main bot loop every 4 hours
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import aiohttp
from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import setup_logger

logger = setup_logger("meme_bot")

# ── Subreddits to pull memes from (crypto/trading relevant) ──────────
CRYPTO_SUBS = [
    "CryptoCurrency",
    "Bitcoin",
    "ethtrader",
    "CryptoHumor",
    "wallstreetbets",
    "cryptocurrencymemes",
]

# ── Trading-themed jokes (fallback when APIs fail) ────────────────────
TRADING_JOKES = [
    "Why did the crypto trader break up with the stock trader?\nBecause there was no mutual fund between them.",
    "What's a Bitcoin maximalist's favorite movie?\nThe Never Ending Story.",
    "Why don't crypto traders ever get cold?\nBecause they're always HODLing.",
    "I told my wife I made a killing in crypto.\nShe said, 'Show me the money.'\nI said, 'Give it 5 years.'",
    "What do you call a bear market that lasts forever?\nA grizzly situation.",
    "Why did the trader go broke?\nBecause he lost interest.",
    "What's the difference between a crypto trader and a pizza?\nA pizza can feed a family of four.",
    "My portfolio is like my dating life.\nRed all the time.",
    "What did Bitcoin say to the altcoin?\nYou're not my type.",
    "Why do traders make terrible comedians?\nTheir timing is always off.",
    "What's a short seller's favorite song?\nFree Fallin' by Tom Petty.",
    "I asked my financial advisor if I should buy Bitcoin.\nHe said 'No.' That was at $100.",
    "Why are bulls always happy?\nBecause every dip is a buy opportunity.",
    "Crypto trading is easy.\nYou buy, it drops. You sell, it moons. Simple.",
    "What's the most optimistic thing in crypto?\nA stop loss at +50%.",
]

BULL_EMOJIS = ["\U0001f402", "\U0001f680", "\U0001f4c8", "\U0001f4b0", "\U0001f911", "\U0001f48e", "\U0001f64c"]
BEAR_EMOJIS = ["\U0001f43b", "\U0001f4c9", "\U0001f480", "\U0001fae0", "\U0001f62d", "\U0001f525", "\U0001faa6"]


async def fetch_reddit_meme() -> dict[str, Any] | None:
    """Fetch a random meme from crypto/trading subreddits via meme-api.com."""
    sub = random.choice(CRYPTO_SUBS)
    url = f"https://meme-api.com/gimme/{sub}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("url") and not data.get("nsfw", False):
                    return {
                        "title": data.get("title", ""),
                        "url": data["url"],
                        "subreddit": data.get("subreddit", sub),
                        "author": data.get("author", ""),
                        "ups": data.get("ups", 0),
                    }
    except Exception as e:
        logger.debug("meme-api.com failed for r/%s: %s", sub, e)
    return None


async def fetch_dad_joke() -> str | None:
    """Fetch a random dad joke from icanhazdadjoke.com."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(
                "https://icanhazdadjoke.com/",
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                return data.get("joke")
    except Exception as e:
        logger.debug("icanhazdadjoke failed: %s", e)
    return None


def _random_caption(meme: dict[str, Any]) -> str:
    """Build a fun caption for the meme."""
    emoji = random.choice(BULL_EMOJIS + BEAR_EMOJIS)
    title = meme.get("title", "")
    sub = meme.get("subreddit", "")
    ups = meme.get("ups", 0)

    lines = [f"{emoji} <b>{title}</b>"]
    if sub:
        lines.append(f"\U0001f4cd r/{sub}")
    if ups > 100:
        lines.append(f"\U0001f525 {ups:,} upvotes")
    lines.append("")
    lines.append("\u2014 <i>Alpha Meme Bot</i> \U0001f916")
    return "\n".join(lines)


def _joke_message(joke: str) -> str:
    """Format a joke for Telegram."""
    emoji = random.choice(["\U0001f602", "\U0001f923", "\U0001f480", "\U0001f62d", "\U0001fae0", "\U0001f60f", "\U0001f921"])
    return f"{emoji} <b>Trading Joke of the Day</b>\n\n{joke}\n\n\u2014 <i>Alpha Meme Bot</i> \U0001f916"


async def send_meme(bot: Bot | None = None, chat_id: str | None = None) -> bool:
    """Fetch and send a meme to Telegram. Returns True on success."""
    if bot is None:
        token = config.telegram.bot_token
        chat_id = chat_id or config.telegram.chat_id
        if not token or not chat_id:
            logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
            return False
        bot = Bot(token=token)

    chat_id = chat_id or config.telegram.chat_id

    # Try to fetch a meme image (3 attempts from different subs)
    meme = None
    for _ in range(3):
        meme = await fetch_reddit_meme()
        if meme:
            break

    if meme and meme.get("url"):
        caption = _random_caption(meme)
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=meme["url"],
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            logger.info("Meme sent: %s (r/%s)", meme.get("title", "")[:50], meme.get("subreddit"))
            return True
        except Exception as e:
            logger.warning("send_photo failed: %s \u2014 falling back to joke", e)

    # Fallback: dad joke or trading joke
    joke = await fetch_dad_joke()
    if not joke:
        joke = random.choice(TRADING_JOKES)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_joke_message(joke),
            parse_mode=ParseMode.HTML,
        )
        logger.info("Joke sent: %s", joke[:50])
        return True
    except Exception as e:
        logger.error("Failed to send joke: %s", e)
        return False


async def send_trading_joke(bot: Bot | None = None, chat_id: str | None = None) -> bool:
    """Send a random trading-specific joke (no API needed)."""
    if bot is None:
        token = config.telegram.bot_token
        chat_id = chat_id or config.telegram.chat_id
        if not token or not chat_id:
            return False
        bot = Bot(token=token)

    chat_id = chat_id or config.telegram.chat_id
    joke = random.choice(TRADING_JOKES)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_joke_message(joke),
            parse_mode=ParseMode.HTML,
        )
        return True
    except Exception as e:
        logger.error("Failed to send trading joke: %s", e)
        return False


# ── Standalone runner ─────────────────────────────────────────────────
async def _main() -> None:
    print("Fetching meme...")
    ok = await send_meme()
    print("Sent!" if ok else "Failed to send meme.")


if __name__ == "__main__":
    asyncio.run(_main())
