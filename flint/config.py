"""Centralized configuration for Flint.

Loads settings from (highest priority first):
1. Environment variables (FLINT_ prefix)
2. .env file
3. flint.yaml
4. Defaults
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml_settings() -> dict:
    """Load settings from flint.yaml if it exists."""
    yaml_path = Path("flint.yaml")
    if not yaml_path.exists():
        return {}
    try:
        import yaml

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        # Flatten nested keys: {"db": {"path": "x"}} -> {"db_path": "x"}
        flat = {}
        for section, values in data.items():
            if isinstance(values, dict):
                for key, val in values.items():
                    flat[f"{section}_{key}"] = val
            else:
                flat[section] = values
        return flat
    except Exception:
        return {}


class FlintConfig(BaseSettings):
    """Flint platform configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FLINT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    db_path: str = "./data/flint.duckdb"

    # --- Trading defaults ---
    default_markets: List[str] = Field(
        default=["SOL-PERP", "BTC-PERP", "ETH-PERP"]
    )
    default_fee_rate: float = 0.0005
    default_capital: float = 10_000.0

    # --- Collector ---
    collector_enabled: bool = True
    candle_backfill_days: int = 90
    oracle_interval_s: int = 60
    funding_interval_s: int = 3600
    orderbook_interval_s: int = 300
    candle_interval_s: int = 3600

    # --- Risk defaults ---
    max_drawdown_pct: float = 0.20
    default_stop_loss_pct: float = 0.05
    max_open_trades: int = 5

    # --- Notifications ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    webhook_url: str = ""

    # --- Paper trading ---
    paper_trading_capital: float = 10_000.0

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: List[str] = Field(default=["*"])

    # --- RPC ---
    helius_api_key: str = ""
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"


def load_config() -> FlintConfig:
    """Load config with YAML settings as initial values, overridden by env vars."""
    yaml_values = _load_yaml_settings()
    return FlintConfig(**yaml_values)
