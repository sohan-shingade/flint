"""Tests for Drift-specific simulation and MEV cost model."""
from __future__ import annotations

from flint.backtest.drift_sim import (
    DRIFT_MARKETS, DriftFeeModelV2, MevCostModel,
)
from flint.models import Fill, Side


class TestDriftMarkets:
    def test_sol_perp_config(self):
        cfg = DRIFT_MARKETS["SOL-PERP"]
        assert cfg.market_index == 0
        assert cfg.maker_fee < 0  # rebate
        assert cfg.taker_fee > 0

    def test_all_markets_defined(self):
        assert "SOL-PERP" in DRIFT_MARKETS
        assert "BTC-PERP" in DRIFT_MARKETS
        assert "ETH-PERP" in DRIFT_MARKETS


class TestDriftFeeModelV2:
    def test_taker_fee_default_tier(self):
        model = DriftFeeModelV2(thirty_day_volume=0)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100,
                    size=10, fee=0, ts=0)
        fee = model.compute_fee(fill)
        assert fee == 1.0  # 10 * 100 * 0.001

    def test_maker_fee_is_rebate(self):
        model = DriftFeeModelV2(thirty_day_volume=0, is_maker=True)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100,
                    size=10, fee=0, ts=0)
        fee = model.compute_fee(fill)
        assert fee < 0  # rebate

    def test_higher_volume_lower_fee(self):
        low_vol = DriftFeeModelV2(thirty_day_volume=0)
        high_vol = DriftFeeModelV2(thirty_day_volume=10_000_000)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=100,
                    size=10, fee=0, ts=0)
        assert low_vol.compute_fee(fill) > high_vol.compute_fee(fill)


class TestMevCostModel:
    def test_basic_costs(self):
        model = MevCostModel(jito_tip_lamports=10_000, sol_price_usd=150)
        assert model.jito_tip_usd > 0
        assert model.base_tx_fee_usd > 0

    def test_estimate_cost(self):
        model = MevCostModel(jito_tip_lamports=10_000, sol_price_usd=150,
                             front_run_bps=5)
        cost = model.estimate_cost(notional_usd=10_000)
        assert cost > 0
        # Should include tip + base fee + front-run cost
        assert cost > model.jito_tip_usd

    def test_zero_front_run(self):
        model = MevCostModel(front_run_bps=0)
        cost = model.estimate_cost(10_000)
        assert cost == model.jito_tip_usd + model.base_tx_fee_usd
