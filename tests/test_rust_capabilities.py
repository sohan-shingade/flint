"""Tests for rust_capabilities + rust_required gate — Phase 3 T3.2."""
from __future__ import annotations

import pytest

from flint.backtest.engine import BacktestEngine, RustRequiredError
from flint.backtest.rust_capabilities import (
    python_capabilities,
    rust_capabilities,
    rust_supports,
)
from flint.execution.fill_models import ClosePriceFill
from flint.execution.margin import MarginEngine
from flint.models import Candle
from flint.strategy.ma_crossover import MACrossoverStrategy


def _candles(n=30):
    return [
        Candle(ts=i * 3600, open=100, high=101, low=99,
               close=100.5, volume=1000, market="SOL-PERP",
               resolution_s=3600)
        for i in range(n)
    ]


class TestCapabilityDiscovery:
    def test_rust_capabilities_returns_dict(self):
        caps = rust_capabilities()
        assert isinstance(caps, dict)
        assert "engine" in caps
        assert "fill_models" in caps
        assert "supports_partial_fills" in caps

    def test_rust_supports_defaults_false_on_unknown(self):
        assert rust_supports("not_a_real_feature") is False

    def test_python_capabilities_superset(self):
        py_caps = python_capabilities()
        assert py_caps["supports_partial_fills"] is True
        assert py_caps["supports_orderbook_walk"] is True

    def test_rust_engine_reports_itself(self):
        caps = rust_capabilities()
        # If flint_core built, engine=rust; else engine=python (no wheel).
        assert caps["engine"] in {"rust", "python"}


class TestEngineUsedTag:
    def test_python_path_tagged(self):
        """When config precludes Rust, result.engine_used == 'python' and
        fallback_reason names the specific gate that fired."""
        # Use a margin engine to force Python path
        margin = MarginEngine()
        engine = BacktestEngine(
            MACrossoverStrategy(3, 5),
            initial_capital=10_000, fee_rate=0.0005,
            fill_model=ClosePriceFill(),
            margin_engine=margin,
        )
        result = engine.run(_candles())
        assert result.engine_used == "python"
        assert result.fallback_reason is not None
        assert "margin_engine" in result.fallback_reason.lower()

    def test_rust_path_tagged_when_available(self):
        """When flint_core is installed + config is compatible, result
        must be tagged engine_used='rust'."""
        try:
            import flint_core  # noqa: F401
        except ImportError:
            pytest.skip("flint_core not installed")

        engine = BacktestEngine(
            MACrossoverStrategy(3, 5),
            initial_capital=10_000, fee_rate=0.0005,
            fill_model=ClosePriceFill(),
        )
        result = engine.run(_candles())
        assert result.engine_used == "rust"
        assert result.fallback_reason is None


class TestRustRequiredGate:
    def test_rust_required_errors_on_incompatible_config(self):
        """rust_required=True + config that gates Rust off = hard error."""
        margin = MarginEngine()
        engine = BacktestEngine(
            MACrossoverStrategy(3, 5),
            initial_capital=10_000, fee_rate=0.0005,
            fill_model=ClosePriceFill(),
            margin_engine=margin,
            rust_required=True,
        )
        with pytest.raises(RustRequiredError, match="margin_engine"):
            engine.run(_candles())

    def test_rust_required_runs_when_compatible(self):
        """rust_required=True + compatible config = runs on Rust (or errors
        if flint_core missing)."""
        try:
            import flint_core  # noqa: F401
        except ImportError:
            pytest.skip("flint_core not installed")

        engine = BacktestEngine(
            MACrossoverStrategy(3, 5),
            initial_capital=10_000, fee_rate=0.0005,
            fill_model=ClosePriceFill(),
            rust_required=True,
        )
        result = engine.run(_candles())
        assert result.engine_used == "rust"
