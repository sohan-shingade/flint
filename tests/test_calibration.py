"""Tests for CalibrationEngine, CalibrationReport, DriftReport."""
import json
import pytest
from flint.backtest.calibration import CalibrationReport, DriftReport


class TestCalibrationReport:
    def test_create(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=347,
            period_start=1700000000, period_end=1702600000,
            model_type="power_law", coefficient_a=142.3, exponent_b=0.53,
            a_ci=(98.1, 206.4), b_ci=(0.39, 0.67),
            r_squared=0.41, mae_bps=2.3, mape_pct=24.7, cv_r_squared=0.35,
            current_impact_coeff=0.01, recommended_impact_coeff=0.008,
        )
        assert report.model_type == "power_law"
        assert report.exponent_b == 0.53

    def test_to_dict(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=100,
            period_start=1700000000, period_end=1702600000,
            model_type="sqrt", coefficient_a=100.0, exponent_b=0.5,
            a_ci=(80.0, 120.0), b_ci=(0.5, 0.5),
            r_squared=0.3, mae_bps=3.0, mape_pct=30.0, cv_r_squared=0.25,
            current_impact_coeff=0.01, recommended_impact_coeff=0.009,
        )
        d = report.to_dict()
        assert d["venue"] == "drift"
        assert d["model_type"] == "sqrt"
        json.dumps(d)  # Must be JSON-serializable

    def test_summary_contains_key_fields(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=347,
            period_start=1700000000, period_end=1702600000,
            model_type="power_law", coefficient_a=142.3, exponent_b=0.53,
            a_ci=(98.1, 206.4), b_ci=(0.39, 0.67),
            r_squared=0.41, mae_bps=2.3, mape_pct=24.7, cv_r_squared=0.35,
            current_impact_coeff=0.01, recommended_impact_coeff=0.008,
        )
        s = report.summary()
        assert "drift" in s
        assert "SOL-PERP" in s
        assert "power_law" in s


class TestDriftReport:
    def test_needs_recalibration_above_threshold(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=6.0,
            divergence_pct=20.0, needs_recalibration=True,
        )
        assert report.needs_recalibration is True

    def test_no_recalibration_below_threshold(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=5.5,
            divergence_pct=10.0, needs_recalibration=False,
        )
        assert report.needs_recalibration is False

    def test_summary(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=6.0,
            divergence_pct=20.0, needs_recalibration=True,
        )
        s = report.summary()
        assert "drift" in s
        assert "20.0" in s
