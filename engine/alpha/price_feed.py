"""WebSocket price feed — real-time price updates for instant exit checks.

Binance: ccxt.pro watch_ticker (built-in WS support)
Delta India: raw aiohttp WS to wss://socket.india.delta.exchange (ccxt.pro doesn't support Delta)

Architecture:
- PriceFeed runs as background asyncio tasks (one per exchange)
- On every price update: calls strategy.check_exits_immediate(price) if in position
- REST polling loop is NEVER removed — WS is purely additive
- If WS disconnects, auto-reconnect with exponential backoff
- Double-exit prevented by in_position=False guard in strategy
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Callable

import aiohttp

from alpha.utils import setup_logger

if TYPE_CHECKING:
    from alpha.strategies.scalp import ScalpStrategy

logger = setup_logger("price_feed")

# Delta India WS
DELTA_WS_URL = "wss://socket.india.delta.exchange"
DELTA_WS_URL_TESTNET = "wss://socket-ind.testnet.deltaex.org"

# Reconnect backoff
RECONNECT_MIN_SEC = 2
RECONNECT_MAX_SEC = 60
STALE_WARN_SEC = 30


def _ccxt_to_delta_symbol(pair: str) -> str:
    """Convert ccxt pair format to Delta WS symbol.

    'BTC/USD:USD' → 'BTCUSD'
    'ETH/USD:USD' → 'ETHUSD'
    """
    base = pair.split("/")[0]
    return f"{base}USD"


def _delta_symbol_to_ccxt(symbol: str, pairs: list[str]) -> str | None:
    """Convert Delta WS symbol back to ccxt pair format.

    'BTCUSD' → 'BTC/USD:USD' (matching from known pairs list)
    """
    base = symbol.replace("USD", "")
    for pair in pairs:
        if pair.startswith(f"{base}/"):
            return pair
    return None


class PriceFeed:
    """Real-time price feed via WebSocket for instant exit checks.

    Usage:
        feed = PriceFeed(strategies, binance_exchange, delta_pairs, binance_pairs)
        await feed.start()
        ...
        await feed.stop()
    """

    def __init__(
        self,
        strategies: dict[str, ScalpStrategy],
        binance_exchange: Any = None,
        delta_pairs: list[str] | None = None,
        binance_pairs: list[str] | None = None,
        delta_testnet: bool = False,
    ) -> None:
        self._strategies = strategies
        self._binance_exchange = binance_exchange
        self._delta_pairs = delta_pairs or []
        self._binance_pairs = binance_pairs or []
        self._delta_testnet = delta_testnet

        # Price cache
        self.price_cache: dict[str, float] = {}
        self._last_update: dict[str, float] = {}  # pair → monotonic time

        # Tasks
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

        # Stats
        self._delta_updates = 0
        self._binance_updates = 0
        self._exit_checks = 0
        self._delta_messages_total = 0
        self._delta_messages_parsed = 0
        self._last_stats_log = 0.0

    async def start(self) -> None:
        """Start WS feeds as background tasks."""
        self._running = True
        logger.info(
            "PriceFeed starting — Delta: %d pairs, Binance: %d pairs",
            len(self._delta_pairs), len(self._binance_pairs),
        )

        if self._delta_pairs:
            task = asyncio.create_task(self._delta_ws_loop())
            self._tasks.append(task)

        if self._binance_pairs and self._binance_exchange:
            for pair in self._binance_pairs:
                task = asyncio.create_task(self._binance_ws_loop(pair))
                self._tasks.append(task)

        # Stats logger
        self._tasks.append(asyncio.create_task(self._stats_loop()))
        logger.info("PriceFeed started — %d WS tasks", len(self._tasks))

    async def stop(self) -> None:
        """Stop all WS feeds."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("PriceFeed stopped")

    def get_price(self, pair: str) -> float | None:
        """Get cached price for a pair."""
        return self.price_cache.get(pair)

    def _on_price_update(self, pair: str, price: float, source: str) -> None:
        """Handle a price update — update cache and trigger exit check if in position."""
        if price <= 0:
            return

        self.price_cache[pair] = price
        self._last_update[pair] = time.monotonic()

        if source == "delta":
            self._delta_updates += 1
        else:
            self._binance_updates += 1

        # Check if strategy is in position → immediate exit check
        strategy = self._strategies.get(pair)
        if strategy and strategy.in_position:
            self._exit_checks += 1
            try:
                strategy.check_exits_immediate(price)
            except Exception:
                logger.exception("[%s] Error in check_exits_immediate", pair)

    # ══════════════════════════════════════════════════════════════════
    # DELTA INDIA — Raw WebSocket via aiohttp
    # ══════════════════════════════════════════════════════════════════

    async def _delta_ws_loop(self) -> None:
        """Connect to Delta India WS and subscribe to ticker updates."""
        ws_url = DELTA_WS_URL_TESTNET if self._delta_testnet else DELTA_WS_URL
        symbols = [_ccxt_to_delta_symbol(p) for p in self._delta_pairs]
        backoff = RECONNECT_MIN_SEC

        while self._running:
            try:
                logger.info("Delta WS connecting to %s — symbols: %s", ws_url, symbols)
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=30) as ws:
                        logger.info("Delta WS connected")
                        backoff = RECONNECT_MIN_SEC  # reset on successful connect

                        # Subscribe to v2/ticker for all pairs
                        subscribe_msg = {
                            "type": "subscribe",
                            "payload": {
                                "channels": [{
                                    "name": "v2/ticker",
                                    "symbols": symbols,
                                }]
                            }
                        }
                        await ws.send_json(subscribe_msg)
                        logger.info("Delta WS subscribed to v2/ticker: %s", symbols)

                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._handle_delta_message(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("Delta WS closed/error: %s", msg.type)
                                break

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Delta WS error — reconnecting in %ds", backoff)

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)

    def _handle_delta_message(self, raw: str) -> None:
        """Parse Delta WS ticker message and dispatch price update.

        Delta WS v2/ticker message format:
        {
            "type": "v2/ticker",
            "symbol": "BTCUSD",
            "product_id": 123,
            "mark_price": "67000.00",
            "close": 67321,
            ...
        }

        OR (some versions wrap in "ticker" key):
        {
            "type": "ticker",
            "ticker": {
                "symbol": "BTCUSD",
                "mark_price": "67000.00",
                "close": "67321",
                ...
            }
        }

        We handle BOTH formats to be robust.
        """
        self._delta_messages_total += 1
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")

            # ── Format 1: type="v2/ticker" with data at top level ──
            if msg_type == "v2/ticker":
                symbol = data.get("symbol", "")
                price_str = data.get("mark_price") or data.get("close") or data.get("last_price")
                if symbol and price_str:
                    price = float(price_str)
                    pair = _delta_symbol_to_ccxt(symbol, self._delta_pairs)
                    if pair:
                        self._delta_messages_parsed += 1
                        self._on_price_update(pair, price, "delta")
                    return

            # ── Format 2: type="ticker" with data nested in "ticker" key ──
            if msg_type == "ticker":
                ticker_data = data.get("ticker", {})
                if isinstance(ticker_data, dict):
                    symbol = ticker_data.get("symbol", "")
                    price_str = (
                        ticker_data.get("mark_price")
                        or ticker_data.get("close")
                        or ticker_data.get("last_price")
                    )
                    if symbol and price_str:
                        price = float(price_str)
                        pair = _delta_symbol_to_ccxt(symbol, self._delta_pairs)
                        if pair:
                            self._delta_messages_parsed += 1
                            self._on_price_update(pair, price, "delta")
                    return

            # ── Format 3: type="subscriptions" / "heartbeat" / "error" — skip ──
            if msg_type in ("subscriptions", "heartbeat", ""):
                return

            # ── Unknown format: log it once for debugging ──
            if self._delta_messages_total <= 5:
                logger.info(
                    "Delta WS unknown msg type=%s keys=%s",
                    msg_type, list(data.keys())[:10],
                )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            if self._delta_messages_total <= 3:
                logger.warning("Delta WS parse error: %s — raw: %s", e, raw[:200])

    # ══════════════════════════════════════════════════════════════════
    # BINANCE — ccxt.pro watch_ticker
    # ══════════════════════════════════════════════════════════════════

    async def _binance_ws_loop(self, pair: str) -> None:
        """Watch ticker for a single Binance pair using ccxt.pro."""
        backoff = RECONNECT_MIN_SEC

        while self._running:
            try:
                logger.info("Binance WS starting watch_ticker for %s", pair)
                # ccxt.pro watch_ticker returns on each price update
                while self._running:
                    ticker = await self._binance_exchange.watch_ticker(pair)
                    price = float(ticker.get("last", 0) or 0)
                    if price > 0:
                        self._on_price_update(pair, price, "binance")
                    backoff = RECONNECT_MIN_SEC  # reset on success

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Binance WS error for %s — reconnecting in %ds", pair, backoff)

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)

    # ══════════════════════════════════════════════════════════════════
    # STATS
    # ══════════════════════════════════════════════════════════════════

    async def _stats_loop(self) -> None:
        """Log stats every 60 seconds."""
        while self._running:
            try:
                await asyncio.sleep(60)
                now = time.monotonic()

                # Check for stale prices
                stale = []
                for pair, last_t in self._last_update.items():
                    age = now - last_t
                    if age > STALE_WARN_SEC:
                        stale.append(f"{pair}:{age:.0f}s")

                stale_tag = f" STALE: {', '.join(stale)}" if stale else ""
                cached = len(self.price_cache)

                # Show parse ratio for debugging
                parse_tag = ""
                if self._delta_messages_total > 0:
                    parse_pct = (self._delta_messages_parsed / self._delta_messages_total) * 100
                    parse_tag = f" delta_parse={self._delta_messages_parsed}/{self._delta_messages_total}({parse_pct:.0f}%)"

                logger.info(
                    "PriceFeed stats — Delta: %d updates, Binance: %d updates, "
                    "exit_checks: %d, cached: %d pairs%s%s",
                    self._delta_updates, self._binance_updates,
                    self._exit_checks, cached, parse_tag, stale_tag,
                )

                # Reset counters
                self._delta_updates = 0
                self._binance_updates = 0
                self._exit_checks = 0
                self._delta_messages_total = 0
                self._delta_messages_parsed = 0

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Stats loop error")
