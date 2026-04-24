"""Multi-strategy portfolio backtest — Phase 6 T6.1 integration test.

Exercises the existing flint.portfolio.PortfolioEngine end-to-end with two
strategies on shared candle data. Verifies per-strategy P&L attribution,
combined equity curve, allocation sum, and correlation-matrix output.

The deeper unification work (true shared capital pool where one strategy's
loss drains another's budget, pre-trade PortfolioRiskEngine gate on every
order) lives in D-6.1-unified sibling PR — tracked in DEFERRED.md. Today
each strategy runs in an isolated capital bucket and their equity curves
are summed at the end.
"""
from __future__ import annotations

import pytest

from flint.models import Candle, Signal
from flint.portfolio.engine import PortfolioEngine
from flint.strategy.ma_crossover import MACrossoverStrategy
from flint.strategy.rsi import RSIStrategy


def _candles(n=200):
    return [
        Candle(
            ts=i * 3600,
            open=100 + (i % 20) * 0.1,
            high=100 + (i % 20) * 0.1 + 0.5,
            low=100 + (i % 20) * 0.1 - 0.5,
            close=100 + ((i % 20) - 9.5) * 0.2,
            volume=1000,
            market="SOL-PERP",
            resolution_s=3600,
        )
        for i in range(n)
    ]


class TestTwoStrategyBook:
    def test_runs_two_strategies_and_attributes_pnl(self):
        engine = PortfolioEngine(
            strategies=[
                ("ma_5_20", MACrossoverStrategy(fast_period=5, slow_period=20)),
                ("rsi_14", RSIStrategy(period=14)),
            ],
            initial_capital=10_000.0,
            fee_rate=0.0005,
        )
        result = engine.run(_candles(200))

        # Per-strategy results present
        assert "ma_5_20" in result.per_strategy
        assert "rsi_14" in result.per_strategy

        # Combined equity matches candle count (or +1 force-close)
        assert len(result.combined_equity) in (200, 201)

        # Allocations sum to ~1.0
        total_alloc = sum(result.allocations.values())
        assert total_alloc == pytest.approx(1.0, abs=1e-6)

    def test_correlation_matrix_shape(self):
        engine = PortfolioEngine(
            strategies=[
                ("a", MACrossoverStrategy(fast_period=5, slow_period=20)),
                ("b", MACrossoverStrategy(fast_period=8, slow_period=30)),
            ],
            initial_capital=10_000.0,
        )
        result = engine.run(_candles(200))
        # 2x2 correlation matrix, diagonals 1.0.
        assert set(result.correlation_matrix.keys()) == {"a", "b"}
        assert result.correlation_matrix["a"]["a"] == pytest.approx(1.0)
        assert result.correlation_matrix["b"]["b"] == pytest.approx(1.0)
        # Off-diagonal symmetry
        assert result.correlation_matrix["a"]["b"] == \
            result.correlation_matrix["b"]["a"]

    def test_per_strategy_pnl_sums_align(self):
        """Sum of per-strategy final equity ≈ combined final equity (both
        sides net out to the same book)."""
        engine = PortfolioEngine(
            strategies=[
                ("ma", MACrossoverStrategy(fast_period=5, slow_period=20)),
                ("rsi", RSIStrategy(period=14)),
            ],
            initial_capital=10_000.0,
        )
        result = engine.run(_candles(200))
        sum_final = sum(
            r.equity_curve[-1] if r.equity_curve else 0.0
            for r in result.per_strategy.values()
        )
        assert result.combined_equity[-1] == pytest.approx(sum_final,
                                                            rel=1e-6)


class TestEmptyPortfolio:
    def test_no_candles_returns_zero(self):
        engine = PortfolioEngine(
            strategies=[("ma", MACrossoverStrategy(5, 20))],
            initial_capital=10_000.0,
        )
        result = engine.run([])
        assert result.total_pnl == 0
        assert result.sharpe_ratio == 0

    def test_no_strategies_returns_zero(self):
        engine = PortfolioEngine(strategies=[], initial_capital=10_000.0)
        result = engine.run(_candles(10))
        assert result.total_pnl == 0
