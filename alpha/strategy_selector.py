"""Strategy selector — picks the best strategy based on market conditions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alpha.market_analyzer import MarketAnalysis
from alpha.strategies.base import MarketCondition, StrategyName
from alpha.utils import iso_now, setup_logger

if TYPE_CHECKING:
    from alpha.db import Database

logger = setup_logger("strategy_selector")


class StrategySelector:
    """Maps market conditions to the optimal strategy."""

    def __init__(self, db: Any | None = None, arb_enabled: bool = True) -> None:
        self.db: Database | None = db
        self.arb_enabled = arb_enabled
        self._current: StrategyName | None = None

    @property
    def current_strategy(self) -> StrategyName | None:
        return self._current

    async def select(
        self,
        analysis: MarketAnalysis,
        arb_opportunity: bool = False,
    ) -> StrategyName | None:
        """Choose a strategy based on the latest market analysis.

        Returns None if the bot should pause (e.g. extreme volatility).
        """
        previous = self._current

        # Priority 1: arbitrage if detected
        if self.arb_enabled and arb_opportunity:
            selected = StrategyName.ARBITRAGE
            reason = "Arbitrage opportunity detected (cross-exchange spread > threshold)"

        # Priority 2: market-condition mapping
        elif analysis.condition == MarketCondition.SIDEWAYS:
            selected = StrategyName.GRID
            reason = f"Sideways market — {analysis.reason}"

        elif analysis.condition == MarketCondition.TRENDING:
            selected = StrategyName.MOMENTUM
            reason = f"Trending market — {analysis.reason}"

        elif analysis.condition == MarketCondition.VOLATILE:
            # High volatility: pause or use tight grid
            if analysis.atr and analysis.volume_ratio > 2.0:
                # Extreme — pause
                logger.warning("Extreme volatility detected — pausing strategies")
                self._current = None
                await self._log_selection(analysis, None, "Extreme volatility — pausing")
                return None
            else:
                # Moderate volatility — tight grid
                selected = StrategyName.GRID
                reason = f"Moderate volatility — using tight grid — {analysis.reason}"
        else:
            selected = StrategyName.GRID
            reason = "Fallback to grid"

        switched = previous != selected
        self._current = selected

        if switched:
            logger.info(
                "Strategy switched: %s → %s | Reason: %s",
                previous.value if previous else "none",
                selected.value,
                reason,
            )
        else:
            logger.debug("Strategy unchanged: %s", selected.value)

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
            await self.db.log_strategy_selection({
                "timestamp": iso_now(),
                "market_condition": analysis.condition.value,
                "adx": analysis.adx,
                "atr": analysis.atr,
                "bb_width": analysis.bb_width,
                "strategy_selected": strategy.value if strategy else "paused",
                "reason": reason,
            })
        except Exception:
            logger.exception("Failed to log strategy selection to DB")
