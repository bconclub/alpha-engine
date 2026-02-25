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
    leverage: int = field(default_factory=lambda: _env_int("DELTA_LEVERAGE", 20))
    pairs: list[str] = field(default_factory=lambda: _env_list("DELTA_PAIRS"))
    enable_shorting: bool = field(default_factory=lambda: _env_bool("ENABLE_SHORTING", True))

    # Options overlay
    options_enabled: bool = field(default_factory=lambda: _env_bool("DELTA_OPTIONS_ENABLED", False))
    options_pairs: list[str] = field(default_factory=lambda: _env_list("DELTA_OPTIONS_PAIRS", "BTC/USD:USD,ETH/USD:USD"))

    # Delta India fee structure (base rates BEFORE GST)
    taker_fee: float = field(default_factory=lambda: _env_float("DELTA_TAKER_FEE", 0.0005))   # 0.05% per side
    maker_fee: float = field(default_factory=lambda: _env_float("DELTA_MAKER_FEE", 0.0002))   # 0.02% per side
    gst_rate: float = field(default_factory=lambda: _env_float("DELTA_GST_RATE", 0.18))       # 18% GST on fees

    @property
    def taker_fee_with_gst(self) -> float:
        """Taker fee per side including 18% GST: 0.05% * 1.18 = 0.059%."""
        return self.taker_fee * (1 + self.gst_rate)

    @property
    def maker_fee_with_gst(self) -> float:
        """Maker fee per side including 18% GST: 0.02% * 1.18 = 0.024%."""
        return self.maker_fee * (1 + self.gst_rate)

    @property
    def taker_round_trip(self) -> float:
        """Round-trip taker fee (both sides): 0.059% * 2 = 0.118%."""
        return self.taker_fee_with_gst * 2

    @property
    def maker_round_trip(self) -> float:
        """Round-trip maker fee (both sides): 0.024% * 2 = 0.048%."""
        return self.maker_fee_with_gst * 2

    @property
    def mixed_round_trip(self) -> float:
        """Mixed round-trip: maker entry + taker exit = 0.024% + 0.059% = 0.083%."""
        return self.maker_fee_with_gst + self.taker_fee_with_gst

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
    max_loss_daily_pct: float = field(default_factory=lambda: _env_float("MAX_LOSS_DAILY_PCT", 20.0))
    max_loss_daily_hard_pct: float = field(default_factory=lambda: _env_float("MAX_LOSS_DAILY_HARD_PCT", 30.0))
    loss_cooldown_minutes: int = field(default_factory=lambda: int(_env_float("LOSS_COOLDOWN_MINUTES", 30)))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 80.0))
    max_total_exposure_pct: float = 90.0  # with $12, need most of it working
    max_concurrent_positions: int = 3     # 3 scalp positions max
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
