"""Tests for VammCurve constant-product math."""
import pytest
from flint.execution.vamm import VammCurve, VammAccuracyReport, DEFAULT_SQRT_K

class TestVammCurveConstruction:
    def test_from_oracle_price(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert abs(curve.reserve_price - 150.0) < 0.01

    def test_direct_construction(self):
        curve = VammCurve(sqrt_k=1_000_000, peg_multiplier=150.0)
        assert curve._sqrt_k == 1_000_000
        assert curve.reserve_price == 150.0

class TestFillPrice:
    def test_long_costs_more(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert curve.fill_price(100.0, "long") > 150.0

    def test_short_gets_less(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert curve.fill_price(100.0, "short") < 150.0

    def test_larger_order_worse_price(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        small = curve.fill_price(10.0, "long")
        large = curve.fill_price(1000.0, "long")
        assert large > small

    def test_very_small_near_oracle(self):
        curve = VammCurve.from_oracle_price(sqrt_k=10_000_000, oracle_price=150.0)
        assert abs(curve.fill_price(0.1, "long") - 150.0) < 0.01

    def test_higher_k_less_impact(self):
        small_k = VammCurve.from_oracle_price(sqrt_k=100_000, oracle_price=150.0)
        big_k = VammCurve.from_oracle_price(sqrt_k=10_000_000, oracle_price=150.0)
        assert small_k.fill_price(100.0, "long") > big_k.fill_price(100.0, "long")

class TestImpactBps:
    def test_positive(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert curve.impact_bps(100.0, "long", 150.0) > 0

    def test_larger_more_impact(self):
        curve = VammCurve.from_oracle_price(sqrt_k=1_000_000, oracle_price=150.0)
        assert curve.impact_bps(1000.0, "long", 150.0) > curve.impact_bps(10.0, "long", 150.0)

class TestDefaultSqrtK:
    def test_sol_perp_exists(self):
        assert "SOL-PERP" in DEFAULT_SQRT_K
    def test_btc_deeper(self):
        assert DEFAULT_SQRT_K["BTC-PERP"] > DEFAULT_SQRT_K["SOL-PERP"]

class TestVammAccuracyReport:
    def test_create_and_summary(self):
        report = VammAccuracyReport(market="SOL-PERP", num_fills=100, vamm_mae_bps=2.0,
            orderbook_mae_bps=3.0, sqrt_mae_bps=5.0, close_mae_bps=10.0, recommended_model="vamm")
        assert "SOL-PERP" in report.summary()
        assert report.to_dict()["recommended_model"] == "vamm"
