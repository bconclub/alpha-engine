"""Telegram bot notifications — rich, conversational alerts.

Message types:
1. Startup message (on boot)
2. Market update (strategy changes only, per 5-min cycle)
3. Trade alerts (open / close with full detail)
4. Hourly summary
5. Daily summary (midnight UTC)
6. Risk / liquidation alerts
7. Command confirmations (dashboard → bot)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import format_usd, setup_logger

logger = setup_logger("alerts")

LINE = "\u2501" * 18  # ━━━━━━━━━━━━━━━━━━


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
        binance_short = ", ".join(p.split("/")[0] for p in binance_pairs)
        delta_short = ", ".join(delta_pairs) if delta_pairs else "None"
        exchanges = "Binance (Spot)"
        if delta_pairs:
            exchanges += ", Delta (Futures)"
        shorting = "Enabled" if shorting_enabled else "Disabled"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Capital lines — show per-exchange balances if available
        capital_line = f"\U0001f4b0 Capital: `{format_usd(capital)}`"
        balance_lines = ""
        if binance_balance is not None:
            balance_lines += f"\n   \U0001f7e1 Binance: `{format_usd(binance_balance)}`"
        if delta_balance is not None:
            balance_lines += f"\n   \U0001f7e0 Delta: `{format_usd(delta_balance)}`"

        msg = (
            f"{LINE}\n"
            f"\U0001f7e2 *ALPHA BOT ONLINE*\n"
            f"{LINE}\n"
            f"\U0001f4ca Exchanges: `{exchanges}`\n"
            f"{capital_line}{balance_lines}\n"
            f"\U0001f4c8 Pairs: `{binance_short}`\n"
            f"\u26a1 Delta Pairs: `{delta_short}`\n"
            f"\U0001f504 Shorting: `{shorting}`\n"
            f"\U0001f550 Started: `{now}`\n"
            f"{LINE}"
        )
        await self._send(msg)

    # ── 2. MARKET UPDATE (strategy changes only) ─────────────────────────────

    async def send_market_update(
        self, changes: list[dict[str, Any]]
    ) -> None:
        """Send market update only when strategies changed.

        Each change dict: {pair, condition, adx, rsi, old_strategy, new_strategy, direction}
        """
        if not changes:
            return

        condition_emoji = {
            "trending": "\u2197\ufe0f",    # ↗️
            "sideways": "\u2194\ufe0f",    # ↔️
            "volatile": "\u26a1",          # ⚡
        }

        lines = ["\U0001f4ca *MARKET UPDATE*", ""]
        for c in changes:
            emoji = condition_emoji.get(c.get("condition", ""), "\u2753")
            pair_short = c["pair"].split("/")[0] if "/" in c["pair"] else c["pair"]
            strat = c.get("new_strategy") or "Paused"
            adx = c.get("adx", 0)
            rsi = c.get("rsi", 0)
            lines.append(
                f"`{pair_short}`: {c.get('condition', '?')} {emoji} "
                f"(ADX=`{adx:.0f}`, RSI=`{rsi:.0f}`) \u2192 {strat}"
            )
        await self._send("\n".join(lines))

    # ── 3. TRADE ALERTS (open + close) ───────────────────────────────────────

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
        # Header emoji + title
        if position_type == "short":
            header = "\U0001f534 *SHORT OPENED*"
        elif position_type == "long":
            header = "\U0001f7e2 *LONG OPENED*"
        elif side == "buy":
            header = "\U0001f7e2 *BUY OPENED*"
        else:
            header = "\U0001f534 *SELL OPENED*"

        if exchange.lower() != "binance":
            header += f" (Delta)"

        # Size line
        cap_pct = f" ({value / capital * 100:.0f}% capital)" if capital > 0 else ""
        size_line = f"\U0001f4b5 Size: `{format_usd(value)}`{cap_pct}"

        # Leverage line
        lev_line = ""
        if leverage > 1:
            lev_line = f"\n\u2696\ufe0f Leverage: `{leverage}x`"

        lines = [
            header,
            f"\U0001f4b1 Pair: `{pair}` | Exchange: `{exchange.capitalize()}`",
            f"\U0001f4cd Entry: `${price:,.2f}`",
            size_line + lev_line,
            f"\U0001f3af Strategy: `{strategy}`",
            f"\U0001f4ac Reason: _{reason}_",
        ]

        # TP / SL lines
        if tp_price is not None:
            tp_pct = abs((tp_price - price) / price * 100)
            lines.append(f"\u2705 TP: `${tp_price:,.2f}` (+{tp_pct:.1f}%)")
        if sl_price is not None:
            sl_pct = abs((sl_price - price) / price * 100)
            lines.append(f"\U0001f6d1 SL: `${sl_price:,.2f}` (-{sl_pct:.1f}%)")

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
            header = "\u2705 *TRADE CLOSED — PROFIT*"
            pnl_sign = "+"
        else:
            header = "\u274c *TRADE CLOSED — LOSS*"
            pnl_sign = ""

        lines = [
            header,
            f"\U0001f4b1 Pair: `{pair}`",
            f"\U0001f4cd Entry \u2192 Exit: `${entry_price:,.2f}` \u2192 `${exit_price:,.2f}`",
            f"\U0001f4b0 P&L: `{pnl_sign}{format_usd(pnl)}` (`{pnl_sign}{pnl_pct:.1f}%`)",
        ]

        if leverage > 1:
            lines.append(f"\u2696\ufe0f Leveraged: `{leverage}x` ({position_type.upper()})")

        if duration_min is not None:
            if duration_min >= 60:
                dur_str = f"{duration_min / 60:.1f} hr"
            else:
                dur_str = f"{duration_min:.0f} min"
            lines.append(f"\u23f1 Duration: `{dur_str}`")

        await self._send("\n".join(lines))

    # backward compat — old call signature routes to send_trade_opened
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

    # ── 4. HOURLY SUMMARY ────────────────────────────────────────────────────

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
        # Open positions description
        if open_positions:
            pos_parts = []
            for p in open_positions:
                ptype = p.get("position_type", "spot")
                exch = p.get("exchange", "binance")
                pair_short = p["pair"].split("/")[0] if "/" in p["pair"] else p["pair"]
                if ptype in ("long", "short"):
                    pos_parts.append(f"{pair_short} {ptype} on {exch.capitalize()}")
                else:
                    pos_parts.append(f"{pair_short} on {exch.capitalize()}")
            pos_str = f"`{len(open_positions)}` ({', '.join(pos_parts)})"
        else:
            pos_str = "`0`"

        # Active strategies grouped
        strat_groups: dict[str, list[str]] = {}
        for pair, strat in active_strategies.items():
            name = strat or "paused"
            short = pair.split("/")[0] if "/" in pair else pair
            strat_groups.setdefault(name, []).append(short)
        strat_line = ", ".join(
            f"{name.capitalize()} ({', '.join(pairs)})"
            for name, pairs in strat_groups.items()
        )

        hourly_trades = hourly_wins + hourly_losses
        lines = [
            "\u23f1 *HOURLY REPORT*",
            "",
            f"\U0001f4c2 Open positions: {pos_str}",
            f"\U0001f4ca Trades this hour: `{hourly_trades}` ({hourly_wins}W / {hourly_losses}L)",
            f"\U0001f4b0 Hourly P&L: `{'+' if hourly_pnl >= 0 else ''}{format_usd(hourly_pnl)}`",
            f"\U0001f4c8 Daily P&L: `{'+' if daily_pnl >= 0 else ''}{format_usd(daily_pnl)}`",
            f"\U0001f4b5 Capital: `{format_usd(capital)}`",
        ]
        if binance_balance is not None:
            lines.append(f"   \U0001f7e1 Binance: `{format_usd(binance_balance)}`")
        if delta_balance is not None:
            lines.append(f"   \U0001f7e0 Delta: `{format_usd(delta_balance)}`")
        lines.extend([
            f"\U0001f3af Strategies: `{strat_line}`",
            f"\U0001f3c6 Win rate (24h): `{win_rate_24h:.0f}%`",
        ])
        await self._send("\n".join(lines))

    # ── 5. DAILY SUMMARY ─────────────────────────────────────────────────────

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
        # backward compat — accept old kwargs and ignore
        total_pnl: float | None = None,
        trades_count: int | None = None,
        active_strategies: dict[str, str | None] | None = None,
    ) -> None:
        """Midnight daily report with per-pair breakdown."""
        # Use backward-compat fallbacks
        if total_trades == 0 and trades_count:
            total_trades = trades_count
        if daily_pnl == 0 and total_pnl is not None:
            daily_pnl = total_pnl

        pnl_emoji = "\U0001f4c8" if daily_pnl >= 0 else "\U0001f4c9"

        lines = [
            f"\U0001f4c5 *DAILY REPORT*",
            "",
            f"\U0001f4ca Total trades: `{total_trades}`",
            f"\u2705 Wins: `{wins}` | \u274c Losses: `{losses}`",
            f"\U0001f3c6 Win rate: `{win_rate:.1f}%`",
            f"{pnl_emoji} Daily P&L: `{'+' if daily_pnl >= 0 else ''}{format_usd(daily_pnl)}`",
        ]

        # Per-pair breakdown
        if pnl_by_pair:
            lines.append("")
            lines.append("*Per pair:*")
            sorted_pairs = sorted(pnl_by_pair.items(), key=lambda x: x[1], reverse=True)
            for pair, pnl in sorted_pairs:
                icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                short = pair.split("/")[0] if "/" in pair else pair
                lines.append(f"  {icon} `{short}`: `{'+' if pnl >= 0 else ''}{format_usd(pnl)}`")

        lines.append(f"\n\U0001f4b5 Capital: `{format_usd(capital)}`")
        if binance_balance is not None:
            lines.append(f"   \U0001f7e1 Binance: `{format_usd(binance_balance)}`")
        if delta_balance is not None:
            lines.append(f"   \U0001f7e0 Delta: `{format_usd(delta_balance)}`")

        # Best / worst trades
        if best_trade:
            bp = best_trade.get("pair", "?").split("/")[0] if "/" in best_trade.get("pair", "") else best_trade.get("pair", "?")
            lines.append(f"\U0001f31f Best: `{bp}` `+{format_usd(best_trade.get('pnl', 0))}`")
        if worst_trade:
            wp = worst_trade.get("pair", "?").split("/")[0] if "/" in worst_trade.get("pair", "") else worst_trade.get("pair", "?")
            lines.append(f"\U0001f4a9 Worst: `{wp}` `{format_usd(worst_trade.get('pnl', 0))}`")

        await self._send("\n".join(lines))

    # ── 6. RISK ALERTS ───────────────────────────────────────────────────────

    async def send_risk_alert(self, message: str) -> None:
        msg = f"\u26a0\ufe0f *RISK ALERT*\n{message}"
        await self._send(msg)

    async def send_daily_loss_warning(
        self, current_loss_pct: float, limit_pct: float,
    ) -> None:
        """Fired when daily loss approaches the limit."""
        msg = (
            f"\u26a0\ufe0f *RISK ALERT*\n\n"
            f"Daily loss limit approaching: `{current_loss_pct:.1f}%` (limit: `{limit_pct:.0f}%`)\n"
            f"Bot will pause if `{limit_pct:.0f}%` hit."
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
            f"\U0001f6a8 *LIQUIDATION WARNING* \U0001f6a8",
            "",
            f"`{pair}` {position_type.upper()} on Delta within `{distance_pct:.1f}%` of liquidation",
        ]
        if current_price is not None and liq_price is not None:
            lines.append(f"Current: `${current_price:,.2f}` | Liq: `${liq_price:,.2f}`")
        lines.append(f"Leverage: `{leverage}x`")
        lines.append("\n_Consider reducing position or adding margin_")
        await self._send("\n".join(lines))

    async def send_error_alert(self, message: str) -> None:
        msg = f"\u274c *ERROR*\n`{message}`"
        await self._send(msg)

    # ── 7. COMMAND CONFIRMATIONS ─────────────────────────────────────────────

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
        msg = f"{emoji} *{title}* (via dashboard)"
        if detail:
            msg += f"\n{detail}"
        await self._send(msg)

    # ── STRATEGY SWITCH (kept for backward compat) ───────────────────────────

    async def send_strategy_switch(
        self, pair: str, old: str | None, new: str | None, reason: str,
    ) -> None:
        msg = (
            f"\U0001f504 *Strategy Switch* [{pair}]\n"
            f"{old or 'none'} \u2192 {new or 'paused'}\n"
            f"Reason: _{reason}_"
        )
        await self._send(msg)

    # ── BOT LIFECYCLE ────────────────────────────────────────────────────────

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
            f"\U0001f6d1 *ALPHA BOT OFFLINE*\n"
            f"{LINE}\n"
            f"_{reason}_"
        )
        await self._send(msg)

    # ── INTERNAL ─────────────────────────────────────────────────────────────

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
