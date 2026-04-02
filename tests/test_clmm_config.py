"""Tests for CLMM config fields."""
from flint.config import FlintConfig

class TestCLMMConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.clmm_tick_fetch_enabled is False
        assert config.clmm_tick_persist_interval_s == 300

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_CLMM_TICK_FETCH_ENABLED", "true")
        monkeypatch.setenv("FLINT_CLMM_TICK_PERSIST_INTERVAL_S", "600")
        config = FlintConfig()
        assert config.clmm_tick_fetch_enabled is True
        assert config.clmm_tick_persist_interval_s == 600
