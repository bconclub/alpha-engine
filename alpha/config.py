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
class SupabaseConfig:
    url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    key: str = field(default_factory=lambda: _env("SUPABASE_KEY"))


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))


@dataclass(frozen=True)
class TradingConfig:
    pair: str = field(default_factory=lambda: _env("TRADING_PAIR", "BTC/USDT"))
    starting_capital: float = field(default_factory=lambda: _env_float("STARTING_CAPITAL", 10.0))
    max_loss_daily_pct: float = field(default_factory=lambda: _env_float("MAX_LOSS_DAILY_PCT", 5.0))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 30.0))
    max_concurrent_positions: int = 2
    per_trade_stop_loss_pct: float = 2.0
    # Timeframes
    candle_timeframe: str = "15m"
    candle_limit: int = 100
    analysis_interval_sec: int = 300  # 5 minutes
    grid_check_interval_sec: int = 30
    momentum_check_interval_sec: int = 15
    # Arbitrage
    arb_min_spread_pct: float = 1.5


@dataclass(frozen=True)
class Config:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    kucoin: KuCoinConfig = field(default_factory=KuCoinConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


# Singleton
config = Config()
