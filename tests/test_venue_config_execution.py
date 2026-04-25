"""Tests for VenueConfig execution fields."""
from flint.execution.venue_config import get_venue_config


class TestVenueConfigExecution:
    def test_drift_has_impact_coefficient(self):
        cfg = get_venue_config("drift")
        assert cfg.impact_coefficient == 0.01

    def test_drift_has_latency(self):
        cfg = get_venue_config("drift")
        assert cfg.base_latency_s == 8.0
        assert cfg.latency_jitter_s == 5.0

    def test_binance_has_lower_impact(self):
        cfg = get_venue_config("binance")
        assert cfg.impact_coefficient == 0.002

    def test_default_venue_has_values(self):
        cfg = get_venue_config("default")
        assert cfg.impact_coefficient > 0
        assert cfg.base_latency_s >= 0
