"""Telegram bot notifications — clean HTML-formatted alerts.

Message types:
1. Startup message (on boot)
2. Market update (all pairs, grouped by exchange, every analysis cycle)
3. Strategy changes (batched into one message, only when something changes)
4. Trade alerts (open / close with full detail)
5. Hourly summary
6. Daily summary (midnight IST)
7. Risk / liquidation alerts
8. Command confirmations (dashboard -> bot)
"""

from __future__ import annotations

from html import escape as html_escape
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from alpha.config import config
from alpha.utils import format_usd, get_version, ist_now, setup_logger

logger = setup_logger("alerts")

LINE = "\u2501" * 20  # ━━━━━━━━━━━━━━━━━━━━

# Strategy display names
_STRAT_DISPLAY: dict[str | None, str] = {
    "grid": "Grid",
    "momentum": "Momentum",
    "arbitrage": "Arbitrage",
    "futures_momentum": "Futures Momentum",
    "scalp": "Scalp",
    "options_scalp": "Options Scalp",
    None: "Paused",
}

# Condition emoji
_COND_EMOJI: dict[str, str] = {
    "trending": "\u2197\ufe0f",   # ↗️
    "sideways": "\u2194\ufe0f",   # ↔️
    "volatile": "\u26a1",         # ⚡
}


def _pair_short(pair: str) -> str:
    """BTC/USD:USD -> BTC/USD, ETH/USDT -> ETH/USDT, BTCUSD -> BTCUSD.

    Strips the settlement suffix (:USD) but keeps the base/quote pair.
    """
    # Strip settlement currency suffix (e.g. ":USD" from "BTC/USD:USD")
    if ":" in pair:
        pair = pair.split(":")[0]
    return pair


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
        """Clean startup banner.

        Format:
        ━━━━━━━━━━━━━━━━━━━━
        🟢 ALPHA v3.6.0

        💰 Capital: $28.41
        Binance: $10.11
        Delta: $18.30

        ⚡ Pairs: BTC | ETH | SOL | XRP

        📊 Exchange: Binance, Delta
        💪 Leverage: 20x | Shorting: Yes
        🕐 Started: 2026-02-16 18:41 IST
        ━━━━━━━━━━━━━━━━━━━━
        """
        # Clean pair names: "ETH/USD:USD" → "ETH", "BTC/USDT" → "BTC"
        all_bases = sorted(
            {p.split("/")[0] if "/" in p else p for p in binance_pairs}
            | {p.split("/")[0] if "/" in p else p for p in delta_pairs}
        )
        pairs_str = " | ".join(all_bases) if all_bases else "None"

        exchanges: list[str] = []
        if binance_pairs:
            exchanges.append("Binance")
        if delta_pairs:
            exchanges.append("Delta")
        exchanges_str = ", ".join(exchanges) if exchanges else "None"

        shorting = "Yes" if shorting_enabled else "No"
        now = ist_now().strftime("%Y-%m-%d %H:%M IST")
        leverage = config.delta.leverage
        engine_ver = get_version()

        # Capital block
        cap_lines = f"\U0001f4b0Capital: <code>{format_usd(capital)}</code>"
        if binance_balance is not None or delta_balance is not None:
            cap_lines += f"\n       Binance: <code>{_bal(binance_balance)}</code>"
            cap_lines += f"\n       Delta: <code>{_bal(delta_balance)}</code>"

        msg = (
            f"{LINE}\n"
            f"\U0001f7e2 <b>ALPHA v{engine_ver}</b>\n"
            f"{LINE}\n"
            f"{cap_lines}\n"
            f"\u26a1 Pairs: <code>{pairs_str}</code>\n"
            f"\U0001f4ca Exchange: <code>{exchanges_str}</code>\n"
            f"\U0001f4aa Leverage: <code>{leverage}x</code> | Shorting: <code>{shorting}</code>\n"
            f"\U0001f552 Started: <code>{now}</code>"
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

        now = ist_now().strftime("%H:%M IST")

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
        """Clean 4-line trade entry notification.

        Format:
        🟢 LONG XRP/USD $1.48 →
        TP $1.51 (+2.0%)
        SL $1.47 (-0.6%)
        20x | Scalp | RSI:39 + BB:low
        """
        pair_short = _pair_short(pair)
        side_label = position_type.upper() if position_type in ("long", "short") else side.upper()
        emoji = "\U0001f7e2" if side_label in ("LONG", "BUY") else "\U0001f534"

        # Line 1: emoji SIDE PAIR $price →
        line1 = f"{emoji} <b>{side_label} {pair_short}</b> <code>${price:,.2f}</code> \u2192"

        # Line 2: TP line
        lines: list[str] = [line1]
        if tp_price is not None:
            tp_pct = abs((tp_price - price) / price * 100)
            lines.append(f"TP <code>${tp_price:,.2f}</code> (+{tp_pct:.1f}%)")

        # Line 3: SL line
        if sl_price is not None:
            sl_pct = abs((sl_price - price) / price * 100)
            lines.append(f"SL <code>${sl_price:,.2f}</code> (-{sl_pct:.1f}%)")

        # Line 4: leverage | strategy | signal summary
        signal_summary = self._parse_signal_summary(reason)
        parts_4: list[str] = []
        if leverage > 1:
            parts_4.append(f"{leverage}x")
        parts_4.append(strategy.capitalize())
        if signal_summary:
            parts_4.append(signal_summary)
        lines.append(" | ".join(parts_4))

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
        exit_reason: str = "",
    ) -> None:
        """Clean 4-line trade exit notification.

        Format:
        🔴 CLOSED ETH/USD
        $2,005.85 → $2,004.95
        P&L: -0.8% | $-0.02
        Hold: 3m | Profit decay
        """
        pair_short = _pair_short(pair)
        emoji = "\u2705" if pnl >= 0 else "\U0001f534"
        pnl_sign = "+" if pnl >= 0 else ""

        # Line 1: emoji + CLOSED + pair
        line1 = f"{emoji} <b>CLOSED {pair_short}</b>"

        # Line 2: entry → exit
        line2 = f"<code>${entry_price:,.2f}</code> \u2192 <code>${exit_price:,.2f}</code>"

        # Line 3: P&L: pct | $amount
        line3 = f"P&amp;L: <code>{pnl_sign}{pnl_pct:.1f}%</code> | <code>{pnl_sign}{format_usd(pnl)}</code>"

        # Line 4: hold duration | exit reason
        parts_4: list[str] = []
        if duration_min is not None:
            if duration_min >= 60:
                parts_4.append(f"Hold: {duration_min / 60:.1f}h")
            else:
                parts_4.append(f"Hold: {duration_min:.0f}m")
        if exit_reason:
            parts_4.append(self._humanize_exit_reason(exit_reason))
        line4 = " | ".join(parts_4) if parts_4 else ""

        msg = f"{line1}\n{line2}\n{line3}"
        if line4:
            msg += f"\n{line4}"
        await self._send(msg)

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
        unrealized_pnl: float = 0.0,
    ) -> None:
        """Hourly report with real exchange-verified positions and full portfolio value."""
        if open_positions:
            pos_parts = []
            for p in open_positions:
                ptype = p.get("position_type", "spot")
                exch = p.get("exchange", "binance")
                short = _pair_short(p["pair"])
                held_value = p.get("held_value")
                if ptype in ("long", "short"):
                    part = f"{short} {ptype} on {exch.capitalize()}"
                else:
                    part = f"{short} on {exch.capitalize()}"
                # Show held value if available (from exchange verification)
                if held_value is not None and held_value > 0:
                    part += f" ({format_usd(held_value)})"
                pos_parts.append(part)
            pos_str = f"<code>{len(open_positions)}</code> ({', '.join(pos_parts)})"
        else:
            pos_str = "<code>0</code>"

        # Active strategies grouped (with position info for scalp)
        strat_groups: dict[str, list[str]] = {}
        for pair, strat in active_strategies.items():
            if strat and strat.startswith("scalp_"):
                # e.g. "scalp_long" → "Scalp 🟢 LONG"
                side = strat.split("_", 1)[1].upper()
                side_icon = "\U0001f7e2" if side == "LONG" else "\U0001f534"
                name = f"Scalp {side_icon}{side}"
            elif strat == "scalp":
                name = "Scalp (scanning)"
            else:
                name = (strat or "paused").capitalize()
            strat_groups.setdefault(name, []).append(_pair_short(pair))
        strat_line = ", ".join(
            f"{name} ({', '.join(pairs)})"
            for name, pairs in strat_groups.items()
        )

        hourly_trades = hourly_wins + hourly_losses
        h_sign = "+" if hourly_pnl >= 0 else ""
        d_sign = "+" if daily_pnl >= 0 else ""

        # Unrealized P&L line (only show when positions are open)
        u_sign = "+" if unrealized_pnl >= 0 else ""
        unreal_line = (
            f"\U0001f4ad Unrealized P&L: <code>{u_sign}{format_usd(unrealized_pnl)}</code>"
            if open_positions and unrealized_pnl != 0
            else None
        )

        lines = [
            "\u23f1 <b>HOURLY REPORT</b>",
            "",
            f"\U0001f4c2 Open positions: {pos_str}",
            f"\U0001f4ca Trades this hour: <code>{hourly_trades}</code> ({hourly_wins}W / {hourly_losses}L)",
            f"\U0001f4b0 Hourly P&amp;L: <code>{h_sign}{format_usd(hourly_pnl)}</code>",
            f"\U0001f4c8 Daily P&amp;L: <code>{d_sign}{format_usd(daily_pnl)}</code>",
        ]
        if unreal_line:
            lines.append(unreal_line)
        lines += [
            f"\U0001f4b5 Capital: <code>{format_usd(capital)}</code> (USDT + assets)",
            f"   \U0001f7e1 Binance: <code>{_bal(binance_balance)}</code>",
            f"   \U0001f7e0 Delta: <code>{_bal(delta_balance)}</code>",
            f"\U0001f3af Strategies: <code>{strat_line}</code>",
            f"\U0001f3c6 Win rate (24h): <code>{'N/A' if win_rate_24h < 0 else f'{win_rate_24h:.0f}%'}</code>",
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

        version = get_version()
        lines = [
            f"\U0001f4c5 <b>DAILY REPORT</b> <code>v{version}</code>",
            "",
            f"\U0001f4ca Total trades: <code>{total_trades}</code>",
            f"\u2705 Wins: <code>{wins}</code> | \u274c Losses: <code>{losses}</code>",
            f"\U0001f3c6 Win rate: <code>{'N/A' if win_rate < 0 else f'{win_rate:.1f}%'}</code>",
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

    async def send_orphan_alert(
        self,
        pair: str,
        side: str,
        contracts: float,
        action: str,
        detail: str = "",
    ) -> None:
        """Urgent orphan position alert — capital-destroying bug notification."""
        msg = (
            f"\u26a0\ufe0f <b>ORPHAN POSITION</b> \u26a0\ufe0f\n\n"
            f"<code>{pair}</code> {side.upper()} {contracts:.0f}ct\n"
            f"Action: <b>{action}</b>\n"
        )
        if detail:
            msg += f"<i>{html_escape(detail)}</i>\n"
        msg += f"\n<i>Orphan detection protects against stuck positions</i>"
        await self._send(msg)

    async def send_text(self, text: str) -> None:
        """Send a raw text message (for ad-hoc alerts)."""
        await self._send(text)

    async def send_error_alert(self, message: str) -> None:
        """Send error alert — clean, max 3 lines, no raw JSON."""
        # Strip any JSON blobs from message
        import re
        clean = re.sub(r'\{[^}]{50,}\}', '', message)
        clean = clean.strip()[:200]  # cap length
        msg = f"\u26a0\ufe0f <b>ERROR</b>\n<code>{html_escape(clean)}</code>"
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
            "update_pair_config": ("\U0001f9e0", "SENTINEL CONFIG"),
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

    @staticmethod
    def _parse_signal_summary(reason: str) -> str:
        """Extract signal info from reason string for compact display.

        Input:  'Scalp long 2/4: RSI(<40)+BB(low) [15m bias: bullish]'
        Output: 'RSI:39 + BB:low'
        """
        import re
        # Try to find the signal part after ":"
        match = re.search(r':\s*(.+?)(?:\s*\[|$)', reason)
        if match:
            sig = match.group(1).strip()
            # Clean up: RSI(<40) → RSI:<40, BB(low) → BB:low
            sig = sig.replace("(", ":").replace(")", "").replace("+", " + ")
            return sig
        return ""

    @staticmethod
    def _humanize_exit_reason(reason: str) -> str:
        """Convert exit type codes into human-readable labels.

        Input:  'Scalp TRAIL +0.35% price (+7.0% capital at 20x)'
        Output: 'Trail exit'
        """
        reason_upper = reason.upper()
        if "TRAIL" in reason_upper:
            return "Trail exit"
        if "SL" in reason_upper and "BREAKEVEN" not in reason_upper:
            return "Stop loss"
        if "BREAKEVEN" in reason_upper:
            return "Breakeven exit"
        if "TIMEOUT" in reason_upper:
            return "Timeout"
        if "FLAT" in reason_upper:
            return "Flatline exit"
        if "PULLBACK" in reason_upper:
            return "Pullback exit"
        if "DECAY" in reason_upper:
            return "Profit decay"
        if "REVERSAL" in reason_upper:
            return "Signal reversal"
        if "SAFETY" in reason_upper:
            return "Safety exit"
        if "position_gone" in reason.lower():
            return "Position closed on exchange"
        # Fallback: clean up
        return reason.split(" ")[1] if " " in reason else reason

    async def disconnect(self) -> None:
        """Close the Telegram bot session (prevents 'Unclosed client session' warnings)."""
        if self._bot:
            try:
                await self._bot.shutdown()
            except Exception:
                pass
            self._bot = None

    async def health_check(self) -> bool:
        """Ping Telegram API to verify connection is alive. Reconnect if dead."""
        if not self._bot:
            return False
        try:
            await self._bot.get_me()
            return True
        except Exception:
            logger.warning("Telegram health check failed — reconnecting")
            try:
                await self.connect()
                return self._bot is not None
            except Exception:
                logger.exception("Telegram reconnect failed")
                return False

    async def _send(self, text: str) -> None:
        if not self._chat_id:
            logger.debug("Alert (Telegram disabled): %s", text[:100])
            return
        if not self._bot:
            # Try to reconnect before giving up
            await self.connect()
        if not self._bot:
            logger.debug("Alert (Telegram still disconnected): %s", text[:100])
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning("Telegram send failed: %s — reconnecting and retrying", e)
            try:
                await self.connect()
                if self._bot:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
            except Exception:
                logger.exception("Telegram retry also failed — message lost")
