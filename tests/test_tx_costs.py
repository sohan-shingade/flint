"""Tests for CostEstimate, TxCostModel implementations."""
import pytest
from flint.execution.tx_costs import (
    CostEstimate, SolanaTxCostModel, HyperliquidTxCostModel,
    CexTxCostModel, get_tx_cost_model,
)

class TestCostEstimate:
    def test_total_sums_all(self):
        est = CostEstimate(exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.50)
        assert abs(est.total - 1.261) < 0.001

    def test_zero(self):
        est = CostEstimate(exchange_fee=0.0, network_fee=0.0, bundle_tip=0.0, impact_est=0.0)
        assert est.total == 0.0

    def test_to_dict(self):
        est = CostEstimate(exchange_fee=0.75, network_fee=0.001, bundle_tip=0.01, impact_est=0.50)
        d = est.to_dict()
        assert "total" in d
        assert d["total"] == est.total

class TestSolanaTxCostModel:
    def test_default_costs(self):
        model = SolanaTxCostModel(sol_price_usd=150.0)
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert est.exchange_fee > 0
        assert est.network_fee > 0
        assert est.bundle_tip > 0

    def test_lamport_conversion(self):
        model = SolanaTxCostModel(priority_fee_lamports=1_000_000_000, jito_tip_lamports=0,
                                   sol_price_usd=100.0, exchange_fee_bps=0)
        est = model.estimate("SOL-PERP", 10.0, 150.0)
        assert abs(est.network_fee - 100.0) < 0.01

    def test_urgent_higher(self):
        model = SolanaTxCostModel(sol_price_usd=150.0, historical_fees={"p50": 5000, "p90": 50000})
        normal = model.estimate("SOL-PERP", 10.0, 150.0, urgency="normal")
        urgent = model.estimate("SOL-PERP", 10.0, 150.0, urgency="urgent")
        assert urgent.network_fee > normal.network_fee

    def test_venue(self):
        assert SolanaTxCostModel().venue == "drift"

class TestHyperliquidTxCostModel:
    def test_negligible(self):
        est = HyperliquidTxCostModel().estimate("SOL-PERP", 10.0, 150.0)
        assert est.network_fee < 0.01
        assert est.bundle_tip == 0.0

    def test_venue(self):
        assert HyperliquidTxCostModel().venue == "hyperliquid"

class TestCexTxCostModel:
    def test_no_network(self):
        est = CexTxCostModel().estimate("SOL-PERP", 10.0, 150.0)
        assert est.network_fee == 0.0
        assert est.bundle_tip == 0.0

    def test_venue(self):
        assert CexTxCostModel(venue_name="binance").venue == "binance"

class TestFactory:
    def test_drift(self):
        assert isinstance(get_tx_cost_model("drift"), SolanaTxCostModel)
    def test_hyperliquid(self):
        assert isinstance(get_tx_cost_model("hyperliquid"), HyperliquidTxCostModel)
    def test_binance(self):
        assert isinstance(get_tx_cost_model("binance"), CexTxCostModel)
    def test_unknown(self):
        assert isinstance(get_tx_cost_model("unknown"), CexTxCostModel)
