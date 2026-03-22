"""Tests for precision module — token math, Drift conversions, order validation."""
from flint.precision import (
    amount_to_precision, price_to_precision, round_pnl,
    to_drift_base, from_drift_base, to_drift_price, from_drift_price,
    to_base_units, from_base_units, validate_order_size, get_market_specs,
)
import pytest


class TestAmountPrecision:
    def test_truncate_to_step(self):
        assert amount_to_precision(1.23456, 0.01) == 1.23
        assert amount_to_precision(1.239, 0.01) == 1.23  # truncate, not round

    def test_truncate_small(self):
        assert amount_to_precision(0.0078, 0.001) == 0.007

    def test_exact_step(self):
        assert amount_to_precision(1.5, 0.5) == 1.5

    def test_large_step(self):
        assert amount_to_precision(1234, 100) == 1200

    def test_zero_step(self):
        assert amount_to_precision(1.5, 0) == 1.5


class TestPricePrecision:
    def test_round_to_tick(self):
        assert price_to_precision(142.3456, 0.001) == 142.346

    def test_round_down(self):
        assert price_to_precision(142.3454, 0.001) == 142.345

    def test_exact(self):
        assert price_to_precision(100.0, 0.01) == 100.0


class TestDriftConversions:
    def test_base_roundtrip(self):
        assert from_drift_base(to_drift_base(1.5)) == 1.5
        assert from_drift_base(to_drift_base(0.001)) == 0.001

    def test_price_roundtrip(self):
        assert from_drift_price(to_drift_price(150.25)) == 150.25

    def test_base_precision(self):
        assert to_drift_base(1.0) == 1_000_000_000
        assert to_drift_base(0.1) == 100_000_000

    def test_price_precision(self):
        assert to_drift_price(100.0) == 100_000_000
        assert to_drift_price(0.001) == 1000

    def test_no_float_error(self):
        # 0.1 + 0.2 = 0.30000000000000004 in float
        # Decimal should handle this correctly
        val = 0.1 + 0.2
        result = to_drift_base(val)
        assert result == 300_000_000  # not 300_000_000 + or - 1


class TestTokenConversions:
    def test_sol(self):
        assert to_base_units(1.5, "SOL") == 1_500_000_000
        assert from_base_units(1_500_000_000, "SOL") == 1.5

    def test_usdc(self):
        assert to_base_units(100.0, "USDC") == 100_000_000
        assert from_base_units(100_000_000, "USDC") == 100.0

    def test_unknown_token_uses_6(self):
        # Unknown tokens default to 6 decimals
        assert to_base_units(1.0, "UNKNOWN") == 1_000_000


class TestOrderValidation:
    def test_valid_sol_order(self):
        result = validate_order_size(5.0, "SOL-PERP")
        assert result == 5.0

    def test_truncate_to_step(self):
        result = validate_order_size(5.15, "SOL-PERP")  # step=0.1
        assert result == 5.1

    def test_below_minimum(self):
        with pytest.raises(ValueError, match="below minimum"):
            validate_order_size(0.01, "SOL-PERP")  # min=0.1

    def test_btc_precision(self):
        result = validate_order_size(0.0156, "BTC-PERP")  # step=0.001
        assert result == 0.015

    def test_unknown_market_passthrough(self):
        assert validate_order_size(1.234, "UNKNOWN-PERP") == 1.234

    def test_market_specs(self):
        specs = get_market_specs("SOL-PERP")
        assert specs is not None
        assert specs[0] == 0.001  # tick_size
        assert specs[1] == 0.1    # min_order_size


class TestRoundPnl:
    def test_prevents_drift(self):
        # Simulate float accumulation
        total = 0.0
        for _ in range(1000):
            total += 0.1
        # Float: 99.9999999999986
        assert total != 100.0
        # With rounding:
        assert round_pnl(total) == 100.0
