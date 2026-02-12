"""Market analyzer — fetches candles and classifies market condition."""

from __future__ import annotations

from dataclasses import dataclass

import ccxt.async_support as ccxt
import pandas as pd
import ta

from alpha.config import config
from alpha.strategies.base import MarketCondition
from alpha.utils import iso_now, setup_logger

logger = setup_logger("market_analyzer")


@dataclass
class MarketAnalysis:
    """Result of a single analysis pass."""
    condition: MarketCondition
    adx: float
    atr: float
    bb_width: float
    rsi: float
    volume_ratio: float  # current vol vs 20-period avg
    reason: str
    timestamp: str


class MarketAnalyzer:
    """Fetches OHLCV data and determines the current market regime."""

    def __init__(self, exchange: ccxt.Exchange, pair: str | None = None) -> None:
        self.exchange = exchange
        self.pair = pair or config.trading.pair
        self.timeframe = config.trading.candle_timeframe
        self.limit = config.trading.candle_limit
        self._last_analysis: MarketAnalysis | None = None

    @property
    def last_analysis(self) -> MarketAnalysis | None:
        return self._last_analysis

    async def analyze(self) -> MarketAnalysis:
        """Fetch candles, compute indicators, classify condition."""
        logger.info("Analyzing market for %s (%s, %d candles)", self.pair, self.timeframe, self.limit)

        ohlcv = await self.exchange.fetch_ohlcv(self.pair, self.timeframe, limit=self.limit)
        df = self._to_dataframe(ohlcv)
        analysis = self._classify(df)
        self._last_analysis = analysis

        logger.info(
            "Market condition: %s | ADX=%.1f ATR=%.2f BBW=%.4f RSI=%.1f VolRatio=%.2f | %s",
            analysis.condition.value,
            analysis.adx,
            analysis.atr,
            analysis.bb_width,
            analysis.rsi,
            analysis.volume_ratio,
            analysis.reason,
        )
        return analysis

    # -- Internal helpers ------------------------------------------------------

    @staticmethod
    def _to_dataframe(ohlcv: list[list]) -> pd.DataFrame:
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    @staticmethod
    def _classify(df: pd.DataFrame) -> MarketAnalysis:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # ADX — trend strength
        adx_indicator = ta.trend.ADXIndicator(high, low, close, window=14)
        adx = adx_indicator.adx().iloc[-1]
        plus_di = adx_indicator.adx_pos().iloc[-1]
        minus_di = adx_indicator.adx_neg().iloc[-1]

        # ATR — volatility
        atr_indicator = ta.volatility.AverageTrueRange(high, low, close, window=14)
        atr = atr_indicator.average_true_range().iloc[-1]
        atr_pct = (atr / close.iloc[-1]) * 100  # ATR as % of price

        # Bollinger Band width
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_mid = bb.bollinger_mavg().iloc[-1]
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid else 0.0

        # RSI
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

        # Volume ratio (current vs 20-bar average)
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_current = volume.iloc[-1]
        volume_ratio = vol_current / vol_avg if vol_avg else 1.0

        # -- Classification logic --
        condition: MarketCondition
        reason: str

        # Volatile: ATR spike + high volume
        if atr_pct > 2.0 and volume_ratio > 1.5:
            condition = MarketCondition.VOLATILE
            reason = f"ATR%={atr_pct:.2f} (>2%) + volume ratio={volume_ratio:.2f} (>1.5)"

        # Trending: strong ADX + clear directional bias
        elif adx > 25 and abs(plus_di - minus_di) > 5:
            direction = "bullish" if plus_di > minus_di else "bearish"
            condition = MarketCondition.TRENDING
            reason = f"ADX={adx:.1f} (>25), {direction} (+DI={plus_di:.1f}, -DI={minus_di:.1f})"

        # Sideways: weak ADX + tight bands
        elif adx < 20 and bb_width < 0.04:
            condition = MarketCondition.SIDEWAYS
            reason = f"ADX={adx:.1f} (<20) + tight BBands width={bb_width:.4f} (<0.04)"

        # Default fallback — sideways unless moderate trend
        elif adx >= 20:
            condition = MarketCondition.TRENDING
            reason = f"Moderate trend ADX={adx:.1f}, defaulting to trending"
        else:
            condition = MarketCondition.SIDEWAYS
            reason = f"Low ADX={adx:.1f}, defaulting to sideways"

        return MarketAnalysis(
            condition=condition,
            adx=adx,
            atr=atr,
            bb_width=bb_width,
            rsi=rsi,
            volume_ratio=volume_ratio,
            reason=reason,
            timestamp=iso_now(),
        )
