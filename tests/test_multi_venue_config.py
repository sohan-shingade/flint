"""Tests for multi-venue config fields."""
from flint.config import FlintConfig


class TestMultiVenueConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.live_multi_venue_primary == ""
        assert config.live_multi_venue_tick_mode == "primary"
        assert config.live_multi_venue_leg_timeout_s == 30.0
        assert config.live_multi_venue_auto_unwind is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_PRIMARY", "drift")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_TICK_MODE", "any")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_LEG_TIMEOUT_S", "60.0")
        monkeypatch.setenv("FLINT_LIVE_MULTI_VENUE_AUTO_UNWIND", "true")
        config = FlintConfig()
        assert config.live_multi_venue_primary == "drift"
        assert config.live_multi_venue_tick_mode == "any"
        assert config.live_multi_venue_leg_timeout_s == 60.0
        assert config.live_multi_venue_auto_unwind is True
