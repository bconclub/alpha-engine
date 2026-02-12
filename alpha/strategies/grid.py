"""Grid trading strategy — places layered buy/sell orders within a price range."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd
import ta

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor


@dataclass
class GridLevel:
    price: float
    side: str  # "buy" or "sell"
    filled: bool = False
    order_id: str | None = None


class GridStrategy(BaseStrategy):
    """
    Places a grid of limit orders between support and resistance.

    - 5-8 levels, evenly spaced
    - Buy at lower levels, sell at upper levels
    - Auto-adjusts if price breaks out of the range
    """

    name = StrategyName.GRID
    check_interval_sec = config.trading.grid_check_interval_sec

    def __init__(self, pair: str, executor: TradeExecutor, risk_manager: RiskManager) -> None:
        super().__init__(pair, executor, risk_manager)
        self.grid_levels: list[GridLevel] = []
        self.upper_bound: float = 0.0
        self.lower_bound: float = 0.0
        self.num_levels: int = 6
        self.order_amount_usd: float = 0.0  # set on start

    async def on_start(self) -> None:
        """Calculate support/resistance and build the initial grid."""
        ohlcv = await self.executor.exchange.fetch_ohlcv(
            self.pair, config.trading.candle_timeframe, limit=config.trading.candle_limit
        )
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        self._build_grid(df)

    async def on_stop(self) -> None:
        self.grid_levels.clear()
        self.logger.info("Grid cleared on stop")

    async def check(self) -> list[Signal]:
        """Check current price against grid levels, emit signals for unfilled levels."""
        ticker = await self.executor.exchange.fetch_ticker(self.pair)
        current_price: float = ticker["last"]
        signals: list[Signal] = []

        # If price broke out of range, rebuild the grid
        if current_price > self.upper_bound or current_price < self.lower_bound:
            self.logger.info(
                "Price %.2f broke range [%.2f – %.2f], rebuilding grid",
                current_price, self.lower_bound, self.upper_bound,
            )
            ohlcv = await self.executor.exchange.fetch_ohlcv(
                self.pair, config.trading.candle_timeframe, limit=config.trading.candle_limit
            )
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            self._build_grid(df)
            return signals  # wait for next check after rebuild

        for level in self.grid_levels:
            if level.filled:
                continue

            # Buy level: place if current price is near or below the buy level
            if level.side == "buy" and current_price <= level.price * 1.002:
                amount = self.order_amount_usd / level.price
                signals.append(Signal(
                    side="buy",
                    price=level.price,
                    amount=amount,
                    order_type="limit",
                    reason=f"Grid buy at level {level.price:.2f}",
                    strategy=self.name,
                    pair=self.pair,
                    metadata={"grid_level": level.price},
                ))
                level.filled = True

            # Sell level: place if current price is near or above the sell level
            elif level.side == "sell" and current_price >= level.price * 0.998:
                amount = self.order_amount_usd / level.price
                signals.append(Signal(
                    side="sell",
                    price=level.price,
                    amount=amount,
                    order_type="limit",
                    reason=f"Grid sell at level {level.price:.2f}",
                    strategy=self.name,
                    pair=self.pair,
                    metadata={"grid_level": level.price},
                ))
                level.filled = True

        return signals

    # -- Internal --------------------------------------------------------------

    def _build_grid(self, df: pd.DataFrame) -> None:
        """Determine support/resistance from recent price action and create grid levels."""
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Use Bollinger Bands for dynamic range
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        self.upper_bound = float(bb.bollinger_hband().iloc[-1])
        self.lower_bound = float(bb.bollinger_lband().iloc[-1])

        # Expand range slightly for safety
        price_range = self.upper_bound - self.lower_bound
        self.upper_bound += price_range * 0.05
        self.lower_bound -= price_range * 0.05

        # Determine order size — split capital across grid levels
        capital = config.trading.starting_capital * (config.trading.max_position_pct / 100)
        self.order_amount_usd = max(2.0, capital / self.num_levels)

        # Build levels
        step = (self.upper_bound - self.lower_bound) / (self.num_levels - 1)
        midpoint = (self.upper_bound + self.lower_bound) / 2

        self.grid_levels = []
        for i in range(self.num_levels):
            price = self.lower_bound + step * i
            side = "buy" if price < midpoint else "sell"
            self.grid_levels.append(GridLevel(price=round(price, 2), side=side))

        self.logger.info(
            "Grid built: %d levels [%.2f – %.2f], order size $%.2f",
            self.num_levels, self.lower_bound, self.upper_bound, self.order_amount_usd,
        )
        for lvl in self.grid_levels:
            self.logger.debug("  %s @ %.2f", lvl.side.upper(), lvl.price)
