"""Strategy selector — picks the best strategy per pair based on market conditions.

Supports both spot (Binance) and futures (Delta) pairs with different strategy mappings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alpha.market_analyzer import MarketAnalysis
from alpha.strategies.base import MarketCondition, StrategyName
from alpha.utils import iso_now, setup_logger

if TYPE_CHECKING:
    from alpha.db import Database

logger = setup_logger("strategy_selector")


class StrategySelector:
    """Maps market conditions to the optimal strategy, tracked per pair."""

    def __init__(
        self,
        db: Any | None = None,
        arb_enabled: bool = True,
        futures_pairs: set[str] | None = None,
    ) -> None:
        self.db: Database | None = db
        self.arb_enabled = arb_enabled
        self.futures_pairs: set[str] = futures_pairs or set()
        # Per-pair strategy tracking
        self._current: dict[str, StrategyName | None] = {}

    def current_strategy(self, pair: str | None = None) -> StrategyName | None:
        """Return the currently selected strategy for a pair."""
        if pair is None:
            # Backward-compat: return first entry
            return next(iter(self._current.values()), None)
        return self._current.get(pair)

    async def select(
        self,
        analysis: MarketAnalysis,
        arb_opportunity: bool = False,
    ) -> StrategyName | None:
        """Choose a strategy for the pair in the analysis.

        Returns None if the pair should pause (e.g. extreme volatility).
        """
        pair = analysis.pair
        previous = self._current.get(pair)

        # ── Futures pairs (Delta) ────────────────────────────────────────
        if pair in self.futures_pairs:
            return await self._select_futures(analysis, previous)

        # ── Spot pairs (Binance) ─────────────────────────────────────────

        # Priority 1: arbitrage if detected
        if self.arb_enabled and arb_opportunity:
            selected = StrategyName.ARBITRAGE
            reason = f"[{pair}] Arbitrage opportunity detected (cross-exchange spread > threshold)"

        # Priority 2: market-condition mapping (no grid — momentum only)
        elif analysis.condition == MarketCondition.SIDEWAYS:
            # Sideways: pause (no grid — not aligned with scalping approach)
            logger.info("[%s] Sideways market -- pausing (grid disabled)", pair)
            self._current[pair] = None
            await self._log_selection(analysis, None, f"[{pair}] Sideways -- pausing (grid disabled)")
            return None

        elif analysis.condition == MarketCondition.TRENDING:
            selected = StrategyName.MOMENTUM
            reason = f"[{pair}] Trending market -- {analysis.reason}"

        elif analysis.condition == MarketCondition.VOLATILE:
            if analysis.atr and analysis.volume_ratio > 2.0:
                # Extreme -- pause
                logger.warning("[%s] Extreme volatility detected -- pausing", pair)
                self._current[pair] = None
                await self._log_selection(analysis, None, f"[{pair}] Extreme volatility -- pausing")
                return None
            else:
                # Moderate volatility -- use momentum (not grid)
                selected = StrategyName.MOMENTUM
                reason = f"[{pair}] Moderate volatility -- using momentum -- {analysis.reason}"
        else:
            # Fallback: momentum (not grid)
            selected = StrategyName.MOMENTUM
            reason = f"[{pair}] Fallback to momentum"

        switched = previous != selected
        self._current[pair] = selected

        if switched:
            logger.info(
                "[%s] Strategy switched: %s -> %s | %s",
                pair,
                previous.value if previous else "none",
                selected.value,
                reason,
            )
        else:
            logger.debug("[%s] Strategy unchanged: %s", pair, selected.value)

        await self._log_selection(analysis, selected, reason)
        return selected

    async def _select_futures(
        self, analysis: MarketAnalysis, previous: StrategyName | None,
    ) -> StrategyName | None:
        """Strategy selection logic for futures (Delta) pairs."""
        pair = analysis.pair

        if analysis.condition == MarketCondition.TRENDING:
            selected = StrategyName.FUTURES_MOMENTUM
            reason = f"[{pair}] Trending futures market -- {analysis.reason}"

        elif analysis.condition == MarketCondition.VOLATILE:
            if analysis.volume_ratio > 2.0:
                # Extreme volatility -- pause
                logger.warning("[%s] Extreme volatility on futures -- pausing", pair)
                self._current[pair] = None
                await self._log_selection(
                    analysis, None, f"[{pair}] Extreme volatility on futures -- pausing",
                )
                return None
            # Moderate volatility -- still trade futures momentum
            selected = StrategyName.FUTURES_MOMENTUM
            reason = f"[{pair}] Moderate volatility on futures -- {analysis.reason}"

        elif analysis.condition == MarketCondition.SIDEWAYS:
            # No grid on futures -- pause
            logger.info("[%s] Sideways futures market -- pausing (no grid on futures)", pair)
            self._current[pair] = None
            await self._log_selection(
                analysis, None, f"[{pair}] Sideways futures market -- pausing",
            )
            return None

        else:
            selected = StrategyName.FUTURES_MOMENTUM
            reason = f"[{pair}] Futures fallback to momentum"

        switched = previous != selected
        self._current[pair] = selected

        if switched:
            logger.info(
                "[%s] Strategy switched: %s -> %s | %s",
                pair,
                previous.value if previous else "none",
                selected.value,
                reason,
            )
        else:
            logger.debug("[%s] Strategy unchanged: %s", pair, selected.value)

        await self._log_selection(analysis, selected, reason)
        return selected

    async def _log_selection(
        self,
        analysis: MarketAnalysis,
        strategy: StrategyName | None,
        reason: str,
    ) -> None:
        if self.db is None:
            return
        try:
            exchange = "delta" if analysis.pair in self.futures_pairs else "binance"

            # entry_distance_pct: how far RSI is from the momentum entry
            # threshold (RSI > 55 for bullish, RSI < 45 for bearish).
            if analysis.rsi >= 50:
                entry_distance_pct = analysis.rsi - 55.0
            else:
                entry_distance_pct = 45.0 - analysis.rsi

            await self.db.log_strategy_selection({
                "timestamp": iso_now(),
                "pair": analysis.pair,
                "exchange": exchange,
                "market_condition": analysis.condition.value,
                "adx": analysis.adx,
                "atr": analysis.atr,
                "bb_width": analysis.bb_width,
                "bb_upper": analysis.bb_upper,
                "bb_lower": analysis.bb_lower,
                "rsi": analysis.rsi,
                "volume_ratio": analysis.volume_ratio,
                "signal_strength": analysis.signal_strength,
                "macd_value": analysis.macd_value,
                "macd_signal": analysis.macd_signal,
                "macd_histogram": analysis.macd_histogram,
                "current_price": analysis.current_price,
                "price_change_15m": analysis.price_change_pct,
                "entry_distance_pct": entry_distance_pct,
                "plus_di": analysis.plus_di,
                "minus_di": analysis.minus_di,
                "direction": analysis.direction,
                "strategy_selected": strategy.value if strategy else "paused",
                "reason": reason,
            })
        except Exception:
            logger.exception("Failed to log strategy selection to DB")
