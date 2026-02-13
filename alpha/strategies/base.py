"""Base strategy class — all strategies inherit from this."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from alpha.utils import setup_logger

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor


class StrategyName(str, Enum):
    GRID = "grid"
    MOMENTUM = "momentum"
    ARBITRAGE = "arbitrage"
    FUTURES_MOMENTUM = "futures_momentum"


class MarketCondition(str, Enum):
    TRENDING = "trending"
    SIDEWAYS = "sideways"
    VOLATILE = "volatile"


@dataclass
class Signal:
    """A trade signal emitted by a strategy."""
    side: str  # "buy" or "sell"
    price: float
    amount: float
    order_type: str  # "market" or "limit"
    reason: str
    strategy: StrategyName
    pair: str
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Futures-specific (defaults keep backward compat for spot strategies)
    leverage: int = 1                   # 1 = spot, >1 = futures
    position_type: str = "spot"         # "spot", "long", or "short"
    reduce_only: bool = False           # True when closing a futures position
    exchange_id: str = "binance"        # identifies which exchange to route to


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: StrategyName
    check_interval_sec: int  # how often the strategy runs its loop

    def __init__(self, pair: str, executor: TradeExecutor, risk_manager: RiskManager) -> None:
        self.pair = pair
        self.executor = executor
        self.risk_manager = risk_manager
        self.is_active = False
        self._task: asyncio.Task[None] | None = None
        self.logger = setup_logger(f"strategy.{self.name.value}.{pair.replace('/', '')}")

    async def start(self) -> None:
        """Activate the strategy and begin its check loop."""
        if self.is_active:
            self.logger.warning("Strategy %s already running", self.name.value)
            return
        self.is_active = True
        self.logger.info("Starting strategy: %s", self.name.value)
        await self.on_start()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Gracefully stop the strategy."""
        self.is_active = False
        self.logger.info("Stopping strategy: %s", self.name.value)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.on_stop()

    async def _run_loop(self) -> None:
        """Internal loop — calls check() at the configured interval."""
        while self.is_active:
            try:
                signals = await self.check()
                for signal in signals:
                    if self.risk_manager.approve_signal(signal):
                        await self.executor.execute(signal)
                    else:
                        self.logger.info("Risk manager rejected signal: %s", signal.reason)
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("Error in %s check loop", self.name.value)
            await asyncio.sleep(self.check_interval_sec)

    # -- Hooks for subclasses --------------------------------------------------

    async def on_start(self) -> None:
        """Called once when the strategy starts. Override to init state."""

    async def on_stop(self) -> None:
        """Called once when the strategy stops. Override to clean up."""

    @abstractmethod
    async def check(self) -> list[Signal]:
        """Run one iteration of the strategy logic. Return signals to execute."""
        ...
