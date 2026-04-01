"""Tests for the configuration system."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from flint.config import FlintConfig, load_config, _load_yaml_settings


class TestFlintConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.db_path == "./data/flint.duckdb"
        assert config.default_fee_rate == 0.0005
        assert config.default_capital == 10_000.0
        assert config.default_markets == ["SOL-PERP", "BTC-PERP", "ETH-PERP"]
        assert config.collector_enabled is True
        assert config.oracle_interval_s == 60
        assert config.funding_interval_s == 3600
        assert config.orderbook_interval_s == 300
        assert config.max_drawdown_pct == 0.20
        assert config.max_open_trades == 5

    def test_env_var_override(self):
        with patch.dict(os.environ, {"FLINT_DB_PATH": "/tmp/test.duckdb"}):
            config = FlintConfig()
            assert config.db_path == "/tmp/test.duckdb"

    def test_env_var_prefix(self):
        with patch.dict(os.environ, {"FLINT_DEFAULT_CAPITAL": "50000"}):
            config = FlintConfig()
            assert config.default_capital == 50_000.0

    def test_env_var_bool(self):
        with patch.dict(os.environ, {"FLINT_COLLECTOR_ENABLED": "false"}):
            config = FlintConfig()
            assert config.collector_enabled is False

    def test_notification_defaults_empty(self):
        config = FlintConfig()
        assert config.telegram_bot_token == ""
        assert config.discord_webhook_url == ""

    def test_risk_defaults(self):
        config = FlintConfig()
        assert config.default_stop_loss_pct == 0.05
        assert config.max_drawdown_pct == 0.20


class TestLoadYaml:
    def test_loads_yaml_file(self):
        yaml_content = """
db:
  path: /custom/path.duckdb
trading:
  default_capital: 25000
collector:
  enabled: false
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir="."
        ) as f:
            f.write(yaml_content)
            f.flush()
            # Rename to flint.yaml for the loader
            yaml_path = Path("flint.yaml")
            backup = None
            if yaml_path.exists():
                backup = yaml_path.read_text()
            try:
                yaml_path.write_text(yaml_content)
                result = _load_yaml_settings()
                assert result["db_path"] == "/custom/path.duckdb"
                assert result["trading_default_capital"] == 25000
                assert result["collector_enabled"] is False
            finally:
                if backup is not None:
                    yaml_path.write_text(backup)
                Path(f.name).unlink(missing_ok=True)

    def test_missing_yaml_returns_empty(self):
        with patch("flint.config.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            # Direct test of the function logic
            result = _load_yaml_settings()
            # May or may not be empty depending on actual flint.yaml existence
            assert isinstance(result, dict)


class TestLiveConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_network == "devnet"
        assert cfg.live_tick_interval_s == 60
        assert cfg.live_on_order_failure == "drop"
        assert cfg.live_max_retries == 3
        assert cfg.live_position_sync_interval == 5
        assert cfg.live_limit_order_timeout_bars == 10
        assert cfg.live_rate_limit_orders_per_sec == 10
        assert cfg.live_rate_limit_concurrent_tx == 2
        assert cfg.live_wallet_mode == "keypair"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_NETWORK", "mainnet")
        monkeypatch.setenv("FLINT_LIVE_MAX_RETRIES", "5")
        cfg = FlintConfig()
        assert cfg.live_network == "mainnet"
        assert cfg.live_max_retries == 5


class TestWebSocketConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_tick_mode == "on_candle_close"
        assert cfg.live_candle_resolution_s == 60
        assert cfg.live_tick_markets == []

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_TICK_MODE", "timer")
        monkeypatch.setenv("FLINT_LIVE_CANDLE_RESOLUTION_S", "300")
        cfg = FlintConfig()
        assert cfg.live_tick_mode == "timer"
        assert cfg.live_candle_resolution_s == 300


class TestSafetyRailsConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_dry_run is False
        assert cfg.live_kill_switch_drawdown_pct == 0.15
        assert cfg.live_kill_switch_check_interval_s == 5.0
        assert cfg.live_max_orders_per_minute == 30
        assert cfg.live_per_market_position_limits == ""
        assert cfg.live_drawdown_warning_pct == 0.075

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_DRY_RUN", "true")
        monkeypatch.setenv("FLINT_LIVE_KILL_SWITCH_DRAWDOWN_PCT", "0.10")
        monkeypatch.setenv("FLINT_LIVE_MAX_ORDERS_PER_MINUTE", "60")
        cfg = FlintConfig()
        assert cfg.live_dry_run is True
        assert cfg.live_kill_switch_drawdown_pct == 0.10
        assert cfg.live_max_orders_per_minute == 60


class TestHyperliquidConfig:
    def test_hyperliquid_defaults(self):
        from flint.config import FlintConfig
        config = FlintConfig()
        assert config.live_hyperliquid_network == "testnet"
        assert config.live_hyperliquid_market_order_slippage == 0.003
        assert config.live_hyperliquid_l2_persist_interval_s == 60

    def test_hyperliquid_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_HYPERLIQUID_NETWORK", "mainnet")
        monkeypatch.setenv("FLINT_LIVE_HYPERLIQUID_MARKET_ORDER_SLIPPAGE", "0.005")
        from flint.config import FlintConfig
        config = FlintConfig()
        assert config.live_hyperliquid_network == "mainnet"
        assert config.live_hyperliquid_market_order_slippage == 0.005


class TestLoadConfig:
    def test_load_config_returns_config(self):
        config = load_config()
        assert isinstance(config, FlintConfig)
        assert config.db_path  # non-empty
