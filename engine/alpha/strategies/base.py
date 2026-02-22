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
    SCALP = "scalp"
    OPTIONS_SCALP = "options_scalp"


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
        self._wake_event = asyncio.Event()
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
                        order = await self.executor.execute(signal)
                        if order is not None:
                            # Track position in risk manager for opening trades
                            is_opening = (
                                (signal.position_type == "spot" and signal.side == "buy")
                                or (signal.position_type in ("long", "short") and not signal.reduce_only)
                            )
                            if is_opening:
                                self.risk_manager.record_open(signal)
                            # Notify strategy that the order filled
                            self.on_fill(signal, order)
                        else:
                            # Order failed or was skipped — notify strategy
                            self.on_rejected(signal)
                    else:
                        self.logger.info("Risk manager rejected signal: %s", signal.reason)
                        self.on_rejected(signal)
            except asyncio.CancelledError:
                break
            except Exception:
                # Include position context so we know the state when the crash happened
                pos_info = ""
                try:
                    if hasattr(self, "in_position") and self.in_position:
                        pos_info = (
                            f" | IN POSITION: {getattr(self, 'position_side', '?')} "
                            f"@ ${getattr(self, 'entry_price', 0):.2f} "
                            f"peak={getattr(self, '_peak_unrealized_pnl', 0):.2f}%"
                        )
                except Exception:
                    pass
                self.logger.exception(
                    "Error in %s check loop [%s]%s",
                    self.name.value, self.pair, pos_info,
                )
            # Sleep until timeout OR wake event (momentum alert), whichever first
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self.get_tick_interval(),
                )
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    def wake(self) -> None:
        """Wake the check loop immediately (called by PriceFeed on momentum spike)."""
        self._wake_event.set()

    def get_tick_interval(self) -> int:
        """Return the current tick interval in seconds.

        Override in subclass for dynamic intervals (e.g., faster when in position).
        Default: returns the static check_interval_sec.
        """
        return self.check_interval_sec

    # -- Hooks for subclasses --------------------------------------------------

    async def on_start(self) -> None:
        """Called once when the strategy starts. Override to init state."""

    async def on_stop(self) -> None:
        """Called once when the strategy stops. Override to clean up."""

    def on_fill(self, signal: Signal, order: dict) -> None:
        """Called when an order fills successfully. Override to update position state."""

    def on_rejected(self, signal: Signal) -> None:
        """Called when an order fails or is rejected. Override to clean up pending state."""

    @abstractmethod
    async def check(self) -> list[Signal]:
        """Run one iteration of the strategy logic. Return signals to execute."""
        ...
