"""Tests for calibration config fields."""
from flint.config import FlintConfig


class TestCalibrationConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.calibration_drift_threshold_pct == 15.0
        assert config.calibration_min_fills == 100

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_CALIBRATION_DRIFT_THRESHOLD_PCT", "20.0")
        monkeypatch.setenv("FLINT_CALIBRATION_MIN_FILLS", "200")
        config = FlintConfig()
        assert config.calibration_drift_threshold_pct == 20.0
        assert config.calibration_min_fills == 200
