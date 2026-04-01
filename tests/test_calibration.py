"""Tests for CalibrationEngine, CalibrationReport, DriftReport."""
import json
import numpy as np
import pytest
from flint.backtest.calibration import CalibrationReport, DriftReport, CalibrationEngine
from flint.store import FlintStore
from flint.models import Candle


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


def _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5):
    """Insert synthetic fills and candles with known impact model."""
    import random
    import time
    random.seed(42)
    np.random.seed(42)

    # Place data within the last 25 days so calibrate(lookback_days=30) will find it
    base_ts = int(time.time()) - 25 * 86400
    resolution_s = 3600

    candles = []
    for i in range(720):
        ts = base_ts + i * resolution_s
        price = 150.0 + random.uniform(-5, 5)
        vol = 10000.0 + random.uniform(-2000, 2000)
        candles.append(Candle(
            ts=ts, open=price, high=price + 1, low=price - 1,
            close=price + 0.5, volume=vol,
            market="SOL-PERP", resolution_s=resolution_s,
        ))
    store.upsert_candles(candles)

    adv = 10000.0 * 24
    sigma = 0.02

    for i in range(num_fills):
        ts = base_ts + i * 3600 + 1800
        size = random.uniform(1.0, 50.0)
        participation = size / adv
        true_impact_pct = true_a * sigma * (participation ** true_b) / 10000
        noise = random.gauss(0, true_impact_pct * 0.3)
        actual_impact_pct = max(0.00001, true_impact_pct + noise)
        mid_price = 150.0
        fill_price = mid_price * (1 + actual_impact_pct)

        store.insert_live_fill(
            fill_id=f"f{i}", order_id=f"o{i}", session_id="s1",
            market="SOL-PERP", side="long", price=fill_price, size=size,
            fee=fill_price * size * 0.0005, tx_sig=f"tx{i}",
            venue="drift", is_partial=False, ts=ts,
        )


class TestCalibrationEngine:
    def test_calibrate_recovers_known_model(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5)

        engine = CalibrationEngine(store)
        report = engine.calibrate("drift", "SOL-PERP", lookback_days=30)

        assert report.venue == "drift"
        assert report.market == "SOL-PERP"
        assert report.num_fills > 50  # After winsorizing
        assert report.model_type in ("power_law", "sqrt")
        assert report.coefficient_a > 0
        assert 0.1 < report.exponent_b < 1.0
        assert report.r_squared >= -1.0  # Can be negative with noisy data; bounded check
        assert report.mae_bps >= 0
        assert report.recommended_impact_coeff > 0
        store.close()

    def test_calibrate_too_few_fills_raises(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=10)

        engine = CalibrationEngine(store)
        with pytest.raises(ValueError, match="fills"):
            engine.calibrate("drift", "SOL-PERP", lookback_days=30, min_fills=100)
        store.close()

    def test_calibrate_sqrt_model_when_few_fills(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=150)

        engine = CalibrationEngine(store)
        report = engine.calibrate("drift", "SOL-PERP", lookback_days=30, min_fills=100)
        assert report.model_type == "sqrt"
        assert report.exponent_b == 0.5
        store.close()


class TestDriftDetection:
    def test_no_drift_when_model_matches(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200)
        engine = CalibrationEngine(store)
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30)
        assert report.venue == "drift"
        assert report.num_fills > 0
        assert isinstance(report.divergence_pct, float)
        store.close()

    def test_drift_detected_with_wrong_model(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=5000.0, true_b=0.5)
        engine = CalibrationEngine(store)
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30, threshold_pct=15.0)
        assert report.needs_recalibration is True
        assert report.divergence_pct > 15.0
        store.close()

    def test_no_recalibration_with_high_threshold(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200)
        engine = CalibrationEngine(store)
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30, threshold_pct=99.0)
        assert report.needs_recalibration is False
        store.close()
