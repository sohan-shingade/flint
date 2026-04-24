"""Python fee-model parity with Rust variants — Phase 3 T3.3.

Asserts the Python fee models compute the same numbers as the Rust enum
presets (Drift / Hyperliquid / MakerTaker) to 1e-9. Rust-side tests live in
rust/src/engine/fees.rs::tests.
"""
from __future__ import annotations

import pytest

from flint.execution.fee_models import (
    DriftFeeModel,
    FlatFeeModel,
    HyperliquidFeeModel,
    MakerTakerFeeModel,
)
from flint.models import Fill, Side


def _fill(size: float, price: float) -> Fill:
    return Fill(
        market="SOL-PERP",
        side=Side.LONG,
        price=price,
        size=size,
        fee=0.0,
        ts=0,
    )


class TestDriftFeeModel:
    def test_taker_matches_rust_drift_default(self):
        # 100 * 50 * 10/10000 = 5.0 (matches rust fees.rs::drift_taker_is_10_bps)
        fee = DriftFeeModel().compute_fee(_fill(100.0, 50.0), is_maker=False)
        assert fee == pytest.approx(5.0, abs=1e-9)

    def test_maker_matches_rust_drift_rebate(self):
        # 100 * 50 * -2/10000 = -1.0
        fee = DriftFeeModel().compute_fee(_fill(100.0, 50.0), is_maker=True)
        assert fee == pytest.approx(-1.0, abs=1e-9)


class TestHyperliquidFeeModel:
    def test_taker_matches_rust(self):
        # 100 * 50 * 3.5/10000 = 1.75
        fee = HyperliquidFeeModel().compute_fee(_fill(100.0, 50.0), is_maker=False)
        assert fee == pytest.approx(1.75, abs=1e-9)

    def test_maker_matches_rust(self):
        # 100 * 50 * 1/10000 = 0.5
        fee = HyperliquidFeeModel().compute_fee(_fill(100.0, 50.0), is_maker=True)
        assert fee == pytest.approx(0.5, abs=1e-9)


class TestGenericMakerTaker:
    def test_custom_rates_match_rust(self):
        # Mirrors rust fees.rs::maker_taker_custom
        model = MakerTakerFeeModel(maker_bps=2.0, taker_bps=7.0)
        assert model.compute_fee(_fill(10.0, 100.0), is_maker=True) \
            == pytest.approx(0.2, abs=1e-9)
        assert model.compute_fee(_fill(10.0, 100.0), is_maker=False) \
            == pytest.approx(0.7, abs=1e-9)

    def test_maker_rebate_negative_bps(self):
        model = MakerTakerFeeModel(maker_bps=-2.0, taker_bps=10.0)
        rebate = model.compute_fee(_fill(100.0, 50.0), is_maker=True)
        assert rebate == pytest.approx(-1.0, abs=1e-9)


class TestFlatFeeRemainsFlat:
    """Regression — FlatFeeModel behavior unchanged by T3.3 refactor."""

    def test_flat_fee_unchanged(self):
        assert FlatFeeModel(fee_bps=5.0).compute_fee(_fill(100.0, 50.0)) \
            == pytest.approx(2.5, abs=1e-9)
