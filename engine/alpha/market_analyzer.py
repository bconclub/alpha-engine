"""Market analyzer — fetches candles and classifies market condition per pair."""

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
    """Result of a single analysis pass for one pair."""
    pair: str
    condition: MarketCondition
    adx: float
    atr: float
    bb_width: float
    rsi: float
    volume_ratio: float  # current vol vs 20-period avg
    signal_strength: float  # 0-100 — how strong is the signal for trading
    reason: str
    timestamp: str
    direction: str = "neutral"  # "bullish", "bearish", or "neutral"
    # MACD values
    macd_value: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    # Directional indicators
    plus_di: float = 0.0
    minus_di: float = 0.0
    # Price snapshot
    current_price: float = 0.0


class MarketAnalyzer:
    """Fetches OHLCV data and determines the current market regime.

    One instance per pair, or call analyze(pair) with any pair.
    """

    def __init__(self, exchange: ccxt.Exchange, pair: str | None = None) -> None:
        self.exchange = exchange
        self.pair = pair or config.trading.pair
        self.timeframe = config.trading.candle_timeframe
        self.limit = config.trading.candle_limit
        self._last_analysis: dict[str, MarketAnalysis] = {}

    def last_analysis_for(self, pair: str) -> MarketAnalysis | None:
        return self._last_analysis.get(pair)

    @property
    def last_analysis(self) -> MarketAnalysis | None:
        """Backward-compat: return analysis for the default pair."""
        return self._last_analysis.get(self.pair)

    async def analyze(self, pair: str | None = None) -> MarketAnalysis:
        """Fetch candles, compute indicators, classify condition for a given pair."""
        pair = pair or self.pair
        logger.info("Analyzing %s (%s, %d candles)", pair, self.timeframe, self.limit)

        ohlcv = await self.exchange.fetch_ohlcv(pair, self.timeframe, limit=self.limit)
        df = self._to_dataframe(ohlcv)
        analysis = self._classify(df, pair)
        self._last_analysis[pair] = analysis

        logger.info(
            "[%s] %s | ADX=%.1f ATR=%.2f BBW=%.4f RSI=%.1f VolR=%.2f Str=%.0f | %s",
            pair, analysis.condition.value,
            analysis.adx, analysis.atr, analysis.bb_width,
            analysis.rsi, analysis.volume_ratio, analysis.signal_strength,
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
    def _classify(df: pd.DataFrame, pair: str) -> MarketAnalysis:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # ADX — trend strength
        adx_indicator = ta.trend.ADXIndicator(high, low, close, window=14)
        adx = adx_indicator.adx().iloc[-1]
        plus_di = adx_indicator.adx_pos().iloc[-1]
        minus_di = adx_indicator.adx_neg().iloc[-1]

        # ATR — volatility
        atr_indicator = ta.volatility.AverageTrueRange(high, low, close, window=14)
        atr = atr_indicator.average_true_range().iloc[-1]
        atr_pct = (atr / current_price) * 100  # ATR as % of price

        # Bollinger Band width
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_mid = bb.bollinger_mavg().iloc[-1]
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid else 0.0

        # RSI
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

        # MACD (12, 26, 9)
        macd_indicator = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_value = macd_indicator.macd().iloc[-1]
        macd_signal = macd_indicator.macd_signal().iloc[-1]
        macd_histogram = macd_indicator.macd_diff().iloc[-1]

        # Volume ratio (current vs 20-bar average)
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_current = volume.iloc[-1]
        volume_ratio = vol_current / vol_avg if vol_avg else 1.0

        # -- Classification logic --
        condition: MarketCondition
        reason: str
        direction: str = "neutral"

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
            direction = "bullish" if plus_di > minus_di else "bearish"
            condition = MarketCondition.TRENDING
            reason = f"Moderate trend ADX={adx:.1f}, defaulting to trending"
        else:
            condition = MarketCondition.SIDEWAYS
            reason = f"Low ADX={adx:.1f}, defaulting to sideways"

        # -- Signal strength (0-100) --
        # Higher = stronger signal = should be prioritised for trading
        strength = _compute_signal_strength(adx, rsi, bb_width, volume_ratio, condition)

        return MarketAnalysis(
            pair=pair,
            condition=condition,
            adx=adx,
            atr=atr,
            bb_width=bb_width,
            rsi=rsi,
            volume_ratio=volume_ratio,
            signal_strength=strength,
            reason=reason,
            timestamp=iso_now(),
            direction=direction,
            macd_value=macd_value,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            plus_di=plus_di,
            minus_di=minus_di,
            current_price=current_price,
        )


def _compute_signal_strength(
    adx: float, rsi: float, bb_width: float, volume_ratio: float, condition: MarketCondition,
) -> float:
    """Score 0-100 indicating how tradeable the current setup is.

    Used by the orchestrator to prioritise pairs when capital is limited.
    """
    score = 0.0

    # RSI divergence from neutral (50) — extreme values = stronger signal
    rsi_divergence = abs(rsi - 50)
    score += min(rsi_divergence, 30)  # max 30 pts

    # ADX strength — stronger trend = better momentum signal
    score += min(adx / 2, 20)  # max 20 pts

    # Volume confirmation
    if volume_ratio > 1.2:
        score += min((volume_ratio - 1.0) * 15, 20)  # max 20 pts

    # Bollinger Band width — wider = more room for grid profit
    if condition == MarketCondition.SIDEWAYS:
        score += min(bb_width * 500, 15)  # max 15 pts
    elif condition == MarketCondition.TRENDING:
        score += min(adx - 20, 15) if adx > 20 else 0  # max 15 pts

    # Penalise volatile — uncertain
    if condition == MarketCondition.VOLATILE:
        score *= 0.5

    return min(score, 100.0)
