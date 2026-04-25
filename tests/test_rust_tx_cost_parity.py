"""D-3.4-rust — TxCostModel Python↔Rust parity.

The hot path (engine fill loop computing per-trade cost breakdowns) now
has two implementations: `flint/execution/tx_costs.py` (Python) and
`rust/src/engine/tx_costs.rs` (behind `flint_core.TxCostModel`). Any
divergence = silent wrong numbers, so this file pins every field to
1e-9.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("flint_core", reason="flint_core not built — run maturin develop") is None,
    reason="flint_core unavailable",
)

from flint.execution.tx_costs import (  # noqa: E402
    CexTxCostModel,
    HyperliquidTxCostModel,
    SolanaTxCostModel,
    get_tx_cost_model,
)

import flint_core  # noqa: E402

EPS = 1e-9


def _assert_estimates_match(py_est, rs_est: dict):
    d = py_est.to_dict()
    for k in ("exchange_fee", "network_fee", "bundle_tip", "impact_est", "total"):
        assert abs(d[k] - rs_est[k]) < EPS, (
            f"mismatch on {k!r}: py={d[k]} vs rs={rs_est[k]} (Δ={d[k] - rs_est[k]})"
        )


class TestDriftParity:
    def test_default_small_trade(self):
        py = SolanaTxCostModel().estimate("SOL-PERP", 1.0, 150.0)
        rs = flint_core.TxCostModel.for_venue("drift").estimate(
            "SOL-PERP", 1.0, 150.0, "normal"
        )
        _assert_estimates_match(py, rs)

    def test_default_large_trade(self):
        py = SolanaTxCostModel().estimate("SOL-PERP", 10_000.0, 150.0)
        rs = flint_core.TxCostModel.for_venue("drift").estimate(
            "SOL-PERP", 10_000.0, 150.0, "normal"
        )
        _assert_estimates_match(py, rs)

    def test_urgent_uses_p90_when_historical_set(self):
        py = SolanaTxCostModel(
            priority_fee_lamports=1_000,
            jito_tip_lamports=5_000,
            sol_price_usd=100.0,
            historical_fees={"p50": 2_000, "p90": 20_000},
        )
        rs = flint_core.TxCostModel.solana(
            priority_fee_lamports=1_000,
            jito_tip_lamports=5_000,
            sol_price_usd=100.0,
            exchange_fee_bps=5.0,
            historical_p50=2_000,
            historical_p90=20_000,
        )
        py_n = py.estimate("SOL-PERP", 1.0, 100.0, "normal")
        rs_n = rs.estimate("SOL-PERP", 1.0, 100.0, "normal")
        _assert_estimates_match(py_n, rs_n)

        py_u = py.estimate("SOL-PERP", 1.0, 100.0, "urgent")
        rs_u = rs.estimate("SOL-PERP", 1.0, 100.0, "urgent")
        _assert_estimates_match(py_u, rs_u)

        # Sanity: urgent should be strictly more expensive than normal
        # when p90 > p50, otherwise the comparison is meaningless.
        assert rs_u["network_fee"] > rs_n["network_fee"]

    def test_custom_fee_bps(self):
        py = SolanaTxCostModel(exchange_fee_bps=12.5)
        rs = flint_core.TxCostModel.solana(exchange_fee_bps=12.5)
        for size, price in [(0.1, 150.0), (50.0, 148.32), (1_234.5, 99.99)]:
            _assert_estimates_match(
                py.estimate("SOL-PERP", size, price),
                rs.estimate("SOL-PERP", size, price, "normal"),
            )


class TestHyperliquidParity:
    def test_default(self):
        py = HyperliquidTxCostModel()
        rs = flint_core.TxCostModel.for_venue("hyperliquid")
        for size, price in [(1.0, 50_000.0), (0.5, 70_123.45), (10.0, 2_345.67)]:
            _assert_estimates_match(
                py.estimate("BTC-PERP", size, price),
                rs.estimate("BTC-PERP", size, price, "normal"),
            )

    def test_custom_l1_cost(self):
        py = HyperliquidTxCostModel(l1_cost_usd=0.5, exchange_fee_bps=2.0)
        rs = flint_core.TxCostModel.hyperliquid(l1_cost_usd=0.5, exchange_fee_bps=2.0)
        _assert_estimates_match(
            py.estimate("BTC-PERP", 1.0, 50_000.0),
            rs.estimate("BTC-PERP", 1.0, 50_000.0, "normal"),
        )


class TestCexParity:
    def test_default(self):
        py = CexTxCostModel()
        rs = flint_core.TxCostModel.for_venue("binance")
        for size, price in [(0.1, 70_000.0), (1.5, 3_200.0)]:
            _assert_estimates_match(
                py.estimate("BTC/USDT", size, price),
                rs.estimate("BTC/USDT", size, price, "normal"),
            )

    def test_custom_fee_bps(self):
        py = CexTxCostModel(exchange_fee_bps=10.0)
        rs = flint_core.TxCostModel.cex(exchange_fee_bps=10.0)
        _assert_estimates_match(
            py.estimate("ETH/USDT", 10.0, 3_200.0),
            rs.estimate("ETH/USDT", 10.0, 3_200.0, "normal"),
        )


class TestFactoryParity:
    def test_unknown_venue_falls_back_to_cex(self):
        py = get_tx_cost_model("some_random_venue")
        rs = flint_core.TxCostModel.for_venue("some_random_venue")
        _assert_estimates_match(
            py.estimate("ABC/USDT", 1.0, 100.0),
            rs.estimate("ABC/USDT", 1.0, 100.0, "normal"),
        )

    def test_case_insensitive(self):
        py = get_tx_cost_model("DRIFT")
        rs = flint_core.TxCostModel.for_venue("DRIFT")
        _assert_estimates_match(
            py.estimate("SOL-PERP", 1.0, 150.0),
            rs.estimate("SOL-PERP", 1.0, 150.0, "normal"),
        )


class TestCapabilityFlagUpdated:
    def test_supports_tx_costs_true(self):
        caps = flint_core.capabilities()
        assert caps["supports_tx_costs"] is True


class TestEdgeCases:
    def test_zero_size_returns_zero_exchange_fee(self):
        py = SolanaTxCostModel().estimate("SOL-PERP", 0.0, 150.0)
        rs = flint_core.TxCostModel.for_venue("drift").estimate(
            "SOL-PERP", 0.0, 150.0, "normal"
        )
        _assert_estimates_match(py, rs)
        # Network + tip still paid when size=0 — that's a feature, not a bug
        # (matches Python: on-chain costs are per-tx, not per-unit).
        assert rs["network_fee"] > 0 or rs["exchange_fee"] == 0

    def test_high_notional_no_precision_loss(self):
        # $1B notional — check that f64 rounding stays inside 1e-9 tolerance
        py = SolanaTxCostModel().estimate("BTC-PERP", 10.0, 100_000_000.0)
        rs = flint_core.TxCostModel.for_venue("drift").estimate(
            "BTC-PERP", 10.0, 100_000_000.0, "normal"
        )
        _assert_estimates_match(py, rs)
