"""Tests for multi-strategy portfolio engine."""
from __future__ import annotations

from flint.models import BacktestResult, Candle
from flint.portfolio.allocator import EqualWeightAllocator, InverseVolAllocator
from flint.portfolio.engine import PortfolioEngine, PortfolioResult
from flint.strategy import MACrossoverStrategy, RSIStrategy, MomentumStrategy


def _candles(n=60):
    cs = []
    for i in range(n):
        if i < 20:
            price = 100 - i * 0.5
        elif i < 40:
            price = 90 + (i - 20) * 1.0
        else:
            price = 110 - (i - 40) * 0.5
        cs.append(Candle(ts=i * 3600, open=price, high=price + 1,
                         low=price - 1, close=price, volume=100,
                         market="SOL-PERP", resolution_s=3600))
    return cs


class TestAllocators:
    def test_equal_weight(self):
        alloc = EqualWeightAllocator()
        weights = alloc.allocate(["A", "B", "C"], {})
        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 0.001
        assert abs(weights["A"] - 1/3) < 0.001

    def test_inverse_vol(self):
        alloc = InverseVolAllocator()
        # Create fake results with different volatilities
        r1 = BacktestResult(0, 0, 0, 0, 0, 0, 0,
                            equity_curve=[10000 + i * 10 for i in range(50)])  # low vol
        r2 = BacktestResult(0, 0, 0, 0, 0, 0, 0,
                            equity_curve=[10000 + i * 100 * ((-1)**i) for i in range(50)])  # high vol
        weights = alloc.allocate(["low_vol", "high_vol"], {"low_vol": r1, "high_vol": r2})
        assert weights["low_vol"] > weights["high_vol"]  # low vol gets more capital


class TestPortfolioEngine:
    def test_two_strategies(self):
        strategies = [
            ("MA", MACrossoverStrategy(fast_period=5, slow_period=10)),
            ("RSI", RSIStrategy(period=14)),
        ]
        engine = PortfolioEngine(strategies, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_candles(60))
        assert isinstance(result, PortfolioResult)
        # D-1.1.b force-close fix may add a terminal equity point past
        # the last candle when a strategy had to be closed at engine
        # exit, so the combined curve length is in {N, N+1}.
        assert len(result.combined_equity) in (60, 61)
        assert len(result.allocations) == 2
        assert len(result.per_strategy) == 2

    def test_three_strategies(self):
        strategies = [
            ("MA", MACrossoverStrategy(fast_period=5, slow_period=10)),
            ("RSI", RSIStrategy()),
            ("Mom", MomentumStrategy(lookback=10, threshold_pct=3)),
        ]
        engine = PortfolioEngine(strategies, initial_capital=10_000)
        result = engine.run(_candles(60))
        assert len(result.per_strategy) == 3

    def test_correlation_matrix(self):
        strategies = [
            ("MA", MACrossoverStrategy(fast_period=5, slow_period=10)),
            ("RSI", RSIStrategy()),
        ]
        engine = PortfolioEngine(strategies, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_candles(60))
        if result.correlation_matrix:
            assert "MA" in result.correlation_matrix
            assert result.correlation_matrix["MA"]["MA"] == 1.0  # self-correlation

    def test_inverse_vol_allocator(self):
        strategies = [
            ("MA", MACrossoverStrategy(fast_period=5, slow_period=10)),
            ("RSI", RSIStrategy()),
        ]
        engine = PortfolioEngine(
            strategies, initial_capital=10_000,
            allocator=InverseVolAllocator(),
        )
        result = engine.run(_candles(60))
        assert abs(sum(result.allocations.values()) - 1.0) < 0.01

    def test_empty_strategies(self):
        engine = PortfolioEngine([], initial_capital=10_000)
        result = engine.run(_candles(60))
        assert result.total_pnl == 0

    def test_empty_candles(self):
        strategies = [("MA", MACrossoverStrategy())]
        engine = PortfolioEngine(strategies, initial_capital=10_000)
        result = engine.run([])
        assert result.total_pnl == 0
