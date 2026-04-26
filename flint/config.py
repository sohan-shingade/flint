"""Centralized configuration for Flint.

Loads settings from (highest priority first):
1. Environment variables (FLINT_ prefix)
2. .env file
3. flint.yaml
4. Defaults
"""
from __future__ import annotations

from pathlib import Path
from typing import List

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
    # Default venue when callers don't specify one. Was "drift" pre-
    # v1.5.4. Drift is offline post-hack (CLAUDE.md rule), so the
    # default flips to Hyperliquid — the live wedge. Override per-
    # session via `venue=` on order/session APIs, or globally via
    # `FLINT_DEFAULT_VENUE` env var. Set to "drift" to restore old
    # behavior when Drift returns.
    default_venue: str = "hyperliquid"
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
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    max_concurrent_backtests: int = 5
    cors_origins: List[str] = Field(default=[
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ])

    # --- Provider API keys ---
    birdeye_api_key: str = ""
    helius_api_key: str = ""

    # --- CCXT (optional, pip install flint[ccxt]) ---
    ccxt_exchange: str = "binance"
    ccxt_api_key: str = ""
    ccxt_secret: str = ""

    # --- RPC ---
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"

    # --- Live trading ---
    live_network: str = "devnet"
    live_tick_interval_s: int = 60
    live_on_order_failure: str = "drop"
    live_max_retries: int = 3
    live_position_sync_interval: int = 5
    live_limit_order_timeout_bars: int = 10
    live_rate_limit_orders_per_sec: int = 10
    live_rate_limit_concurrent_tx: int = 2
    live_wallet_mode: str = "keypair"

    # --- Live trading (WebSocket feeds) ---
    live_tick_mode: str = "on_candle_close"
    live_candle_resolution_s: int = 60
    live_tick_markets: List[str] = Field(default=[])

    # --- Safety rails ---
    live_dry_run: bool = False
    live_kill_switch_drawdown_pct: float = 0.15
    live_kill_switch_check_interval_s: float = 5.0
    live_max_orders_per_minute: int = 30
    live_per_market_position_limits: str = ""
    live_drawdown_warning_pct: float = 0.075

    # --- Hyperliquid ---
    live_hyperliquid_network: str = "testnet"
    live_hyperliquid_market_order_slippage: float = 0.003
    live_hyperliquid_l2_persist_interval_s: int = 60

    # --- Multi-venue ---
    live_multi_venue_primary: str = ""
    live_multi_venue_tick_mode: str = "primary"
    live_multi_venue_leg_timeout_s: float = 30.0
    live_multi_venue_auto_unwind: bool = False

    # --- Slippage Calibration ---
    calibration_drift_threshold_pct: float = 15.0
    calibration_min_fills: int = 100

    # --- Transaction costs ---
    tx_cost_priority_fee_lamports: int = 5000
    tx_cost_jito_tip_lamports: int = 10000
    tx_cost_sol_price_usd: float = 150.0

    # --- vAMM ---
    vamm_enabled: bool = False
    vamm_default_sqrt_k: str = ""

    # --- CLMM ---
    clmm_tick_fetch_enabled: bool = False
    clmm_tick_persist_interval_s: int = 300

    # --- Jupiter Perps ---
    jupiter_perps_enabled: bool = False
    jupiter_perps_sidecar_port: int = 8401
    jupiter_perps_rpc_url: str = ""
    jupiter_perps_wallet_path: str = ""

    # --- Dune Analytics (for borrow rate backfill) ---
    dune_api_key: str = ""

    # --- Price source ---
    price_source: str = "pyth"

    # --- Tardis.dev (for CEX orderbook data) ---
    tardis_api_key: str = ""
    tardis_max_gb_per_request: float = 1.0


def load_config() -> FlintConfig:
    """Load config with YAML settings as initial values, overridden by env vars."""
    yaml_values = _load_yaml_settings()
    return FlintConfig(**yaml_values)


_DEFAULT_VENUE_CACHE: list = [None]


def default_venue() -> str:
    """Resolve the default trading venue.

    Was hardcoded to ``"drift"`` in 8 call sites pre-v1.5.4. Drift is
    offline post-hack (see ``CLAUDE.md``) so the default flips to
    Hyperliquid. Override per-call by passing ``venue=`` explicitly,
    or globally via ``FLINT_DEFAULT_VENUE`` / ``flint.yaml``. Set to
    ``"drift"`` to restore old behavior when Drift returns.

    Cached on first call so we don't reload yaml on every order
    placement; cache cleared by tests via ``_DEFAULT_VENUE_CACHE[0] = None``.
    """
    if _DEFAULT_VENUE_CACHE[0] is None:
        try:
            cfg = load_config()
            _DEFAULT_VENUE_CACHE[0] = cfg.default_venue
        except Exception:
            _DEFAULT_VENUE_CACHE[0] = "hyperliquid"
    return _DEFAULT_VENUE_CACHE[0]
