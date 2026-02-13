"""Telegram bot notifications — clean HTML-formatted alerts.

Message types:
1. Startup message (on boot)
2. Market update (all pairs, grouped by exchange, every analysis cycle)
3. Strategy changes (batched into one message, only when something changes)
4. Trade alerts (open / close with full detail)
5. Hourly summary
6. Daily summary (midnight UTC)
7. Risk / liquidation alerts
8. Command confirmations (dashboard -> bot)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import format_usd, setup_logger

logger = setup_logger("alerts")

LINE = "\u2501" * 20  # ━━━━━━━━━━━━━━━━━━━━

# Strategy display names
_STRAT_DISPLAY: dict[str | None, str] = {
    "grid": "Grid",
    "momentum": "Momentum",
    "arbitrage": "Arbitrage",
    "futures_momentum": "Futures Momentum",
    None: "Paused",
}

# Condition emoji
_COND_EMOJI: dict[str, str] = {
    "trending": "\u2197\ufe0f",   # ↗️
    "sideways": "\u2194\ufe0f",   # ↔️
    "volatile": "\u26a1",         # ⚡
}


def _pair_short(pair: str) -> str:
    """BTC/USDT -> BTC, BTCUSD -> BTCUSD (keeps short names)."""
    return pair.split("/")[0] if "/" in pair else pair


def _strat_label(name: str | None) -> str:
    return _STRAT_DISPLAY.get(name, name or "Paused")


def _bal(value: float | None) -> str:
    return format_usd(value) if value is not None else "N/A"


class AlertManager:
    """Sends Telegram messages for all bot events."""

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

    # ── 1. STARTUP MESSAGE ───────────────────────────────────────────────────

    async def send_startup(
        self,
        capital: float,
        binance_pairs: list[str],
        delta_pairs: list[str],
        shorting_enabled: bool,
        binance_balance: float | None = None,
        delta_balance: float | None = None,
    ) -> None:
        """Rich startup banner with exchange and pair info."""
        binance_short = ", ".join(_pair_short(p) for p in binance_pairs)
        delta_short = ", ".join(delta_pairs) if delta_pairs else "None"
        exchanges = "Binance (Spot)"
        if delta_pairs:
            exchanges += ", Delta (Futures)"
        shorting = "Enabled" if shorting_enabled else "Disabled"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        balance_lines = (
            f"\n   \U0001f7e1 Binance: <code>{_bal(binance_balance)}</code>"
            f"\n   \U0001f7e0 Delta: <code>{_bal(delta_balance)}</code>"
        )

        msg = (
            f"{LINE}\n"
            f"\U0001f7e2 <b>ALPHA BOT ONLINE</b>\n"
            f"{LINE}\n"
            f"\U0001f4ca Exchanges: <code>{exchanges}</code>\n"
            f"\U0001f4b0 Capital: <code>{format_usd(capital)}</code>{balance_lines}\n"
            f"\U0001f4c8 Pairs: <code>{binance_short}</code>\n"
            f"\u26a1 Delta Pairs: <code>{delta_short}</code>\n"
            f"\U0001f504 Shorting: <code>{shorting}</code>\n"
            f"\U0001f550 Started: <code>{now}</code>\n"
            f"{LINE}"
        )
        await self._send(msg)

    # ── 2. MARKET UPDATE (all pairs, grouped by exchange) ──────────────────

    async def send_market_update(
        self,
        analyses: list[dict[str, Any]],
        active_strategies: dict[str, str | None],
        capital: float,
        open_position_count: int,
    ) -> None:
        """Consolidated market update -- all pairs grouped by exchange.

        Each analysis dict: {pair, condition, adx, rsi, direction}
        active_strategies:  pair -> strategy name (or None for paused)
        """
        if not analyses:
            return

        now = datetime.now(timezone.utc).strftime("%H:%M UTC")

        # Split into Binance (spot) vs Delta (futures)
        binance_set = set(config.trading.pairs)
        delta_set = set(config.delta.pairs) if config.delta.api_key else set()

        binance_rows: list[dict[str, Any]] = []
        delta_rows: list[dict[str, Any]] = []

        for a in analyses:
            pair = a["pair"]
            if pair in delta_set:
                delta_rows.append(a)
            else:
                binance_rows.append(a)

        lines: list[str] = [
            f"\U0001f4ca <b>MARKET UPDATE</b> \u00b7 {now}",
        ]

        # ── Binance section
        if binance_rows:
            lines.append("")
            lines.append("<b>BINANCE (Spot)</b>")
            lines.append(LINE)
            for a in binance_rows:
                short = _pair_short(a["pair"])
                emoji = _COND_EMOJI.get(a.get("condition", ""), "\u2753")
                adx = round(a.get("adx", 0))
                rsi = round(a.get("rsi", 0))
                strat = _strat_label(active_strategies.get(a["pair"]))
                lines.append(
                    f"{short:<5}{emoji} ADX <code>{adx}</code> RSI <code>{rsi}</code> \u2192 {strat}"
                )

        # ── Delta section
        if delta_rows:
            lines.append("")
            lines.append("<b>DELTA (Futures)</b>")
            lines.append(LINE)
            for a in delta_rows:
                short = _pair_short(a["pair"])
                emoji = _COND_EMOJI.get(a.get("condition", ""), "\u2753")
                adx = round(a.get("adx", 0))
                rsi = round(a.get("rsi", 0))
                strat = _strat_label(active_strategies.get(a["pair"]))
                lines.append(
                    f"{short:<5}{emoji} ADX <code>{adx}</code> RSI <code>{rsi}</code> \u2192 {strat}"
                )

        # ── Footer
        lines.append("")
        lines.append(
            f"\U0001f4b0 <code>{format_usd(capital)}</code> | Positions: <code>{open_position_count}</code> open"
        )
        lines.append(LINE)

        await self._send("\n".join(lines))

    # ── 3. STRATEGY CHANGES (batched, only on actual changes) ──────────────

    async def send_strategy_changes(
        self, changes: list[dict[str, Any]],
    ) -> None:
        """Batched strategy-change alert -- one message for all switches.

        Each change dict: {pair, old_strategy, new_strategy, reason}
        Only call this when len(changes) > 0.
        """
        if not changes:
            return

        lines: list[str] = ["\U0001f500 <b>STRATEGY CHANGES</b>", ""]
        for c in changes:
            short = _pair_short(c["pair"])
            old = _strat_label(c.get("old_strategy"))
            new = _strat_label(c.get("new_strategy"))
            reason = c.get("reason", "")
            lines.append(f"{short}: {old} \u2192 {new} ({reason})")

        await self._send("\n".join(lines))

    # ── 4. TRADE ALERTS (open + close) ─────────────────────────────────────

    async def send_trade_opened(
        self,
        pair: str,
        side: str,
        price: float,
        amount: float,
        value: float,
        strategy: str,
        reason: str,
        exchange: str = "binance",
        leverage: int = 1,
        position_type: str = "spot",
        capital: float = 0,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> None:
        """Rich trade-opened notification."""
        if position_type == "short":
            header = "\U0001f534 <b>SHORT OPENED</b>"
        elif position_type == "long":
            header = "\U0001f7e2 <b>LONG OPENED</b>"
        elif side == "buy":
            header = "\U0001f7e2 <b>BUY OPENED</b>"
        else:
            header = "\U0001f534 <b>SELL OPENED</b>"

        if exchange.lower() != "binance":
            header += " (Delta)"

        cap_pct = f" ({value / capital * 100:.0f}% capital)" if capital > 0 else ""
        lev_line = f"\n\u2696\ufe0f Leverage: <code>{leverage}x</code>" if leverage > 1 else ""

        lines = [
            header,
            f"\U0001f4b1 Pair: <code>{pair}</code> | Exchange: <code>{exchange.capitalize()}</code>",
            f"\U0001f4cd Entry: <code>${price:,.2f}</code>",
            f"\U0001f4b5 Size: <code>{format_usd(value)}</code>{cap_pct}{lev_line}",
            f"\U0001f3af Strategy: <code>{strategy}</code>",
            f"\U0001f4ac Reason: <i>{reason}</i>",
        ]

        if tp_price is not None:
            tp_pct = abs((tp_price - price) / price * 100)
            lines.append(f"\u2705 TP: <code>${tp_price:,.2f}</code> (+{tp_pct:.1f}%)")
        if sl_price is not None:
            sl_pct = abs((sl_price - price) / price * 100)
            lines.append(f"\U0001f6d1 SL: <code>${sl_price:,.2f}</code> (-{sl_pct:.1f}%)")

        await self._send("\n".join(lines))

    async def send_trade_closed(
        self,
        pair: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        duration_min: float | None = None,
        exchange: str = "binance",
        leverage: int = 1,
        position_type: str = "spot",
    ) -> None:
        """Rich trade-closed notification with profit/loss badge."""
        if pnl >= 0:
            header = "\u2705 <b>TRADE CLOSED \u2014 PROFIT</b>"
            pnl_sign = "+"
        else:
            header = "\u274c <b>TRADE CLOSED \u2014 LOSS</b>"
            pnl_sign = ""

        lines = [
            header,
            f"\U0001f4b1 Pair: <code>{pair}</code>",
            f"\U0001f4cd Entry \u2192 Exit: <code>${entry_price:,.2f}</code> \u2192 <code>${exit_price:,.2f}</code>",
            f"\U0001f4b0 P&amp;L: <code>{pnl_sign}{format_usd(pnl)}</code> (<code>{pnl_sign}{pnl_pct:.1f}%</code>)",
        ]

        if leverage > 1:
            lines.append(f"\u2696\ufe0f Leveraged: <code>{leverage}x</code> ({position_type.upper()})")

        if duration_min is not None:
            dur_str = f"{duration_min / 60:.1f} hr" if duration_min >= 60 else f"{duration_min:.0f} min"
            lines.append(f"\u23f1 Duration: <code>{dur_str}</code>")

        await self._send("\n".join(lines))

    # backward compat -- old call signature routes to send_trade_opened
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
        await self.send_trade_opened(
            pair=pair, side=side, price=price, amount=amount,
            value=value, strategy=strategy, reason=reason,
            exchange=exchange, leverage=leverage, position_type=position_type,
        )

    # ── 5. HOURLY SUMMARY ─────────────────────────────────────────────────

    async def send_hourly_summary(
        self,
        open_positions: list[dict[str, Any]],
        hourly_wins: int,
        hourly_losses: int,
        hourly_pnl: float,
        daily_pnl: float,
        capital: float,
        active_strategies: dict[str, str | None],
        win_rate_24h: float,
        binance_balance: float | None = None,
        delta_balance: float | None = None,
    ) -> None:
        """Hourly report with compact position + P&L info."""
        if open_positions:
            pos_parts = []
            for p in open_positions:
                ptype = p.get("position_type", "spot")
                exch = p.get("exchange", "binance")
                short = _pair_short(p["pair"])
                if ptype in ("long", "short"):
                    pos_parts.append(f"{short} {ptype} on {exch.capitalize()}")
                else:
                    pos_parts.append(f"{short} on {exch.capitalize()}")
            pos_str = f"<code>{len(open_positions)}</code> ({', '.join(pos_parts)})"
        else:
            pos_str = "<code>0</code>"

        # Active strategies grouped
        strat_groups: dict[str, list[str]] = {}
        for pair, strat in active_strategies.items():
            name = strat or "paused"
            strat_groups.setdefault(name, []).append(_pair_short(pair))
        strat_line = ", ".join(
            f"{name.capitalize()} ({', '.join(pairs)})"
            for name, pairs in strat_groups.items()
        )

        hourly_trades = hourly_wins + hourly_losses
        h_sign = "+" if hourly_pnl >= 0 else ""
        d_sign = "+" if daily_pnl >= 0 else ""

        lines = [
            "\u23f1 <b>HOURLY REPORT</b>",
            "",
            f"\U0001f4c2 Open positions: {pos_str}",
            f"\U0001f4ca Trades this hour: <code>{hourly_trades}</code> ({hourly_wins}W / {hourly_losses}L)",
            f"\U0001f4b0 Hourly P&amp;L: <code>{h_sign}{format_usd(hourly_pnl)}</code>",
            f"\U0001f4c8 Daily P&amp;L: <code>{d_sign}{format_usd(daily_pnl)}</code>",
            f"\U0001f4b5 Capital: <code>{format_usd(capital)}</code>",
            f"   \U0001f7e1 Binance: <code>{_bal(binance_balance)}</code>",
            f"   \U0001f7e0 Delta: <code>{_bal(delta_balance)}</code>",
            f"\U0001f3af Strategies: <code>{strat_line}</code>",
            f"\U0001f3c6 Win rate (24h): <code>{win_rate_24h:.0f}%</code>",
        ]
        await self._send("\n".join(lines))

    # ── 6. DAILY SUMMARY ──────────────────────────────────────────────────

    async def send_daily_summary(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        win_rate: float,
        daily_pnl: float,
        capital: float,
        pnl_by_pair: dict[str, float] | None = None,
        best_trade: dict[str, Any] | None = None,
        worst_trade: dict[str, Any] | None = None,
        binance_balance: float | None = None,
        delta_balance: float | None = None,
        # backward compat -- accept old kwargs and ignore
        total_pnl: float | None = None,
        trades_count: int | None = None,
        active_strategies: dict[str, str | None] | None = None,
    ) -> None:
        """Midnight daily report with per-pair breakdown."""
        if total_trades == 0 and trades_count:
            total_trades = trades_count
        if daily_pnl == 0 and total_pnl is not None:
            daily_pnl = total_pnl

        pnl_emoji = "\U0001f4c8" if daily_pnl >= 0 else "\U0001f4c9"
        d_sign = "+" if daily_pnl >= 0 else ""

        lines = [
            "\U0001f4c5 <b>DAILY REPORT</b>",
            "",
            f"\U0001f4ca Total trades: <code>{total_trades}</code>",
            f"\u2705 Wins: <code>{wins}</code> | \u274c Losses: <code>{losses}</code>",
            f"\U0001f3c6 Win rate: <code>{win_rate:.1f}%</code>",
            f"{pnl_emoji} Daily P&amp;L: <code>{d_sign}{format_usd(daily_pnl)}</code>",
        ]

        if pnl_by_pair:
            lines.append("")
            lines.append("<b>Per pair:</b>")
            sorted_pairs = sorted(pnl_by_pair.items(), key=lambda x: x[1], reverse=True)
            for pair, pnl in sorted_pairs:
                icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                short = _pair_short(pair)
                p_sign = "+" if pnl >= 0 else ""
                lines.append(f"  {icon} <code>{short}</code>: <code>{p_sign}{format_usd(pnl)}</code>")

        lines.append(f"\n\U0001f4b5 Capital: <code>{format_usd(capital)}</code>")
        lines.append(f"   \U0001f7e1 Binance: <code>{_bal(binance_balance)}</code>")
        lines.append(f"   \U0001f7e0 Delta: <code>{_bal(delta_balance)}</code>")

        if best_trade:
            bp = _pair_short(best_trade.get("pair", "?"))
            lines.append(f"\U0001f31f Best: <code>{bp}</code> <code>+{format_usd(best_trade.get('pnl', 0))}</code>")
        if worst_trade:
            wp = _pair_short(worst_trade.get("pair", "?"))
            lines.append(f"\U0001f4a9 Worst: <code>{wp}</code> <code>{format_usd(worst_trade.get('pnl', 0))}</code>")

        await self._send("\n".join(lines))

    # ── 7. RISK ALERTS ────────────────────────────────────────────────────

    async def send_risk_alert(self, message: str) -> None:
        msg = f"\u26a0\ufe0f <b>RISK ALERT</b>\n{message}"
        await self._send(msg)

    async def send_daily_loss_warning(
        self, current_loss_pct: float, limit_pct: float,
    ) -> None:
        """Fired when daily loss approaches the limit."""
        msg = (
            f"\u26a0\ufe0f <b>RISK ALERT</b>\n\n"
            f"Daily loss limit approaching: <code>{current_loss_pct:.1f}%</code> "
            f"(limit: <code>{limit_pct:.0f}%</code>)\n"
            f"Bot will pause if <code>{limit_pct:.0f}%</code> hit."
        )
        await self._send(msg)

    async def send_liquidation_warning(
        self,
        pair: str,
        distance_pct: float,
        position_type: str,
        leverage: int,
        current_price: float | None = None,
        liq_price: float | None = None,
    ) -> None:
        """Urgent liquidation proximity warning for futures."""
        lines = [
            f"\U0001f6a8 <b>LIQUIDATION WARNING</b> \U0001f6a8",
            "",
            f"<code>{pair}</code> {position_type.upper()} on Delta within "
            f"<code>{distance_pct:.1f}%</code> of liquidation",
        ]
        if current_price is not None and liq_price is not None:
            lines.append(
                f"Current: <code>${current_price:,.2f}</code> | "
                f"Liq: <code>${liq_price:,.2f}</code>"
            )
        lines.append(f"Leverage: <code>{leverage}x</code>")
        lines.append("\n<i>Consider reducing position or adding margin</i>")
        await self._send("\n".join(lines))

    async def send_error_alert(self, message: str) -> None:
        msg = f"\u274c <b>ERROR</b>\n<code>{message}</code>"
        await self._send(msg)

    # ── 8. COMMAND CONFIRMATIONS ──────────────────────────────────────────

    async def send_command_confirmation(
        self, command: str, detail: str = "",
    ) -> None:
        """Confirm dashboard commands on Telegram."""
        cmd_map = {
            "pause": ("\u23f8", "BOT PAUSED"),
            "resume": ("\u25b6\ufe0f", "BOT RESUMED"),
            "force_strategy": ("\U0001f500", "STRATEGY FORCED"),
            "update_config": ("\u2699\ufe0f", "CONFIG UPDATED"),
        }
        emoji, title = cmd_map.get(command, ("\u2139\ufe0f", command.upper()))
        msg = f"{emoji} <b>{title}</b> (via dashboard)"
        if detail:
            msg += f"\n{detail}"
        await self._send(msg)

    # ── STRATEGY SWITCH (kept for backward compat) ────────────────────────

    async def send_strategy_switch(
        self, pair: str, old: str | None, new: str | None, reason: str,
        exchange: str = "",
    ) -> None:
        """No-op: strategy changes are now batched via send_strategy_changes."""
        pass

    # ── BOT LIFECYCLE ─────────────────────────────────────────────────────

    async def send_bot_started(
        self,
        pairs: list[str],
        capital: float,
        binance_balance: float | None = None,
        delta_balance: float | None = None,
    ) -> None:
        """Routes to send_startup with exchange balances."""
        await self.send_startup(
            capital=capital,
            binance_pairs=config.trading.pairs,
            delta_pairs=config.delta.pairs if config.delta.api_key else [],
            shorting_enabled=config.delta.enable_shorting,
            binance_balance=binance_balance,
            delta_balance=delta_balance,
        )

    async def send_bot_stopped(self, reason: str) -> None:
        msg = (
            f"{LINE}\n"
            f"\U0001f6d1 <b>ALPHA BOT OFFLINE</b>\n"
            f"{LINE}\n"
            f"<i>{reason}</i>"
        )
        await self._send(msg)

    # ── INTERNAL ──────────────────────────────────────────────────────────

    async def _send(self, text: str) -> None:
        if not self.is_connected:
            logger.debug("Alert (Telegram disabled): %s", text[:100])
            return
        try:
            await self._bot.send_message(  # type: ignore[union-attr]
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Failed to send Telegram message")
