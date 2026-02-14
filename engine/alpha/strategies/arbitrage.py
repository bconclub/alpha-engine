"""Cross-exchange arbitrage strategy — Binance vs KuCoin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ccxt.async_support as ccxt

from alpha.config import config
from alpha.strategies.base import BaseStrategy, Signal, StrategyName
from alpha.utils import pct_change

if TYPE_CHECKING:
    from alpha.risk_manager import RiskManager
    from alpha.trade_executor import TradeExecutor


class ArbitrageStrategy(BaseStrategy):
    """
    Monitors the same pair on Binance and KuCoin.

    If the price difference exceeds the threshold (after fees),
    buys on the cheaper exchange and sells on the expensive one.
    """

    name = StrategyName.ARBITRAGE
    check_interval_sec = 10  # fast checks for arb windows

    # Estimated fees (taker fee per side)
    BINANCE_FEE_PCT = 0.1
    KUCOIN_FEE_PCT = 0.1
    TOTAL_FEE_PCT = BINANCE_FEE_PCT + KUCOIN_FEE_PCT  # round-trip
    # Withdrawal fee estimate (in quote currency, varies by coin)
    WITHDRAWAL_FEE_USD = 1.0

    def __init__(
        self,
        pair: str,
        executor: TradeExecutor,
        risk_manager: RiskManager,
        kucoin_exchange: ccxt.Exchange | None = None,
    ) -> None:
        super().__init__(pair, executor, risk_manager)
        self.kucoin = kucoin_exchange
        self.min_spread_pct = config.trading.arb_min_spread_pct

    async def on_start(self) -> None:
        if self.kucoin is None:
            self.logger.warning("KuCoin exchange not configured — arbitrage will be limited")

    async def check(self) -> list[Signal]:
        signals: list[Signal] = []

        if self.kucoin is None:
            return signals

        try:
            binance_ticker, kucoin_ticker = (
                await self.executor.exchange.fetch_ticker(self.pair),
                await self.kucoin.fetch_ticker(self.pair),
            )
        except Exception:
            self.logger.exception("Failed to fetch tickers for arbitrage")
            return signals

        binance_price: float = binance_ticker["last"]
        kucoin_price: float = kucoin_ticker["last"]

        spread_pct = abs(pct_change(binance_price, kucoin_price))
        net_spread = spread_pct - self.TOTAL_FEE_PCT

        self.logger.debug(
            "Arb check: Binance=%.2f, KuCoin=%.2f, spread=%.3f%%, net=%.3f%%",
            binance_price, kucoin_price, spread_pct, net_spread,
        )

        if net_spread < self.min_spread_pct:
            return signals

        # Determine direction
        if kucoin_price < binance_price:
            buy_exchange = "kucoin"
            sell_exchange = "binance"
            buy_price = kucoin_price
            sell_price = binance_price
        else:
            buy_exchange = "binance"
            sell_exchange = "kucoin"
            buy_price = binance_price
            sell_price = kucoin_price

        # Check if profitable after withdrawal fee
        capital = self.risk_manager.capital * (config.trading.max_position_pct / 100)
        amount = capital / buy_price
        gross_profit = (sell_price - buy_price) * amount
        net_profit = gross_profit - self.WITHDRAWAL_FEE_USD - (capital * self.TOTAL_FEE_PCT / 100)

        if net_profit <= 0:
            self.logger.info("Arb spread exists but not profitable after fees ($%.2f)", net_profit)
            return signals

        self.logger.info(
            "ARB OPPORTUNITY: Buy %s @ %.2f (%s), Sell @ %.2f (%s) | "
            "Spread=%.2f%% | Est. profit=$%.2f",
            self.pair, buy_price, buy_exchange, sell_price, sell_exchange,
            net_spread, net_profit,
        )

        # Emit buy signal (the executor handles Binance; arb buy on other exchange is logged)
        signals.append(Signal(
            side="buy",
            price=buy_price,
            amount=amount,
            order_type="market",
            reason=(
                f"Arbitrage: buy on {buy_exchange} @ {buy_price:.2f}, "
                f"sell on {sell_exchange} @ {sell_price:.2f}, "
                f"net spread={net_spread:.2f}%, est. profit=${net_profit:.2f}"
            ),
            strategy=self.name,
            pair=self.pair,
            metadata={
                "buy_exchange": buy_exchange,
                "sell_exchange": sell_exchange,
                "spread_pct": net_spread,
                "est_profit": net_profit,
            },
        ))

        return signals
