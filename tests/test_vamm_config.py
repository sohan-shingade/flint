"""Tests for vAMM config fields."""
from flint.config import FlintConfig

class TestVammConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.vamm_enabled is False
        assert config.vamm_default_sqrt_k == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_VAMM_ENABLED", "true")
        monkeypatch.setenv("FLINT_VAMM_DEFAULT_SQRT_K", '{"SOL-PERP": 5000000}')
        config = FlintConfig()
        assert config.vamm_enabled is True
        assert "SOL-PERP" in config.vamm_default_sqrt_k
