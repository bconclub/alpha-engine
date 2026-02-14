"""Configuration management — loads .env and exposes typed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    raw = os.getenv(key)
    return float(raw) if raw else default


def _env_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key)
    return int(raw) if raw else default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "")
    return raw.lower() in ("true", "1", "yes") if raw else default


def _env_list(key: str, default: str = "") -> list[str]:
    """Parse a comma-separated env var into a list of stripped strings."""
    raw = os.getenv(key, default)
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class BinanceConfig:
    api_key: str = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    secret: str = field(default_factory=lambda: _env("BINANCE_SECRET"))


@dataclass(frozen=True)
class KuCoinConfig:
    api_key: str = field(default_factory=lambda: _env("KUCOIN_API_KEY"))
    secret: str = field(default_factory=lambda: _env("KUCOIN_SECRET"))
    passphrase: str = field(default_factory=lambda: _env("KUCOIN_PASSPHRASE"))


@dataclass(frozen=True)
class DeltaConfig:
    api_key: str = field(default_factory=lambda: _env("DELTA_API_KEY"))
    secret: str = field(default_factory=lambda: _env("DELTA_SECRET"))
    testnet: bool = field(default_factory=lambda: _env_bool("DELTA_TESTNET", True))
    leverage: int = field(default_factory=lambda: _env_int("DELTA_LEVERAGE", 5))
    pairs: list[str] = field(default_factory=lambda: _env_list("DELTA_PAIRS"))
    enable_shorting: bool = field(default_factory=lambda: _env_bool("ENABLE_SHORTING", True))

    @property
    def base_url(self) -> str:
        if self.testnet:
            return "https://cdn-ind.testnet.deltaex.org"
        return "https://api.india.delta.exchange"


@dataclass(frozen=True)
class SupabaseConfig:
    url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    key: str = field(default_factory=lambda: _env("SUPABASE_KEY"))


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))


@dataclass(frozen=True)
class TradingConfig:
    # Multi-pair: comma-separated.  Falls back to single TRADING_PAIR for compat.
    pairs: list[str] = field(
        default_factory=lambda: _env_list("TRADING_PAIRS") or [_env("TRADING_PAIR", "BTC/USDT")]
    )
    capital_per_pair: str = field(default_factory=lambda: _env("CAPITAL_PER_PAIR", "auto"))

    starting_capital: float = field(default_factory=lambda: _env_float("STARTING_CAPITAL", 10.0))
    max_loss_daily_pct: float = field(default_factory=lambda: _env_float("MAX_LOSS_DAILY_PCT", 5.0))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 30.0))
    max_total_exposure_pct: float = 60.0  # total across all pairs
    max_concurrent_positions: int = 2
    per_trade_stop_loss_pct: float = 2.0

    # Timeframes
    futures_check_interval_sec: int = 15
    candle_timeframe: str = "15m"
    candle_limit: int = 100
    analysis_interval_sec: int = 300  # 5 minutes
    grid_check_interval_sec: int = 30
    momentum_check_interval_sec: int = 15

    # Arbitrage
    arb_min_spread_pct: float = 1.5

    @property
    def primary_pair(self) -> str:
        """First pair in the list is the primary."""
        return self.pairs[0]

    @property
    def pair(self) -> str:
        """Backward-compat alias — returns primary pair."""
        return self.primary_pair


@dataclass(frozen=True)
class Config:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    kucoin: KuCoinConfig = field(default_factory=KuCoinConfig)
    delta: DeltaConfig = field(default_factory=DeltaConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


# Singleton
config = Config()
