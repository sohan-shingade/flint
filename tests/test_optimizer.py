"""Tests for hyperparameter optimization."""
from __future__ import annotations

from flint.models import Candle
from flint.optimization.optimizer import StrategyOptimizer, OptimizationResult
from flint.strategy import MACrossoverStrategy


def _candles(n=80):
    cs = []
    for i in range(n):
        # Oscillating pattern to generate trades
        cycle = i % 60
        if cycle < 20:
            price = 100 - cycle * 0.5
        elif cycle < 40:
            price = 90 + (cycle - 20) * 1.0
        else:
            price = 110 - (cycle - 40) * 0.5
        cs.append(Candle(ts=i * 3600, open=price, high=price + 1,
                         low=price - 1, close=price, volume=100,
                         market="SOL-PERP", resolution_s=3600))
    return cs


class TestOptimizer:
    def test_basic_optimization(self):
        candles = _candles(80)
        opt = StrategyOptimizer(
            MACrossoverStrategy, candles,
            metric="sharpe_ratio", n_trials=10,
        )
        result = opt.optimize()
        assert isinstance(result, OptimizationResult)
        assert "fast_period" in result.best_params
        assert "slow_period" in result.best_params
        assert result.n_trials == 10
        # Some trials may produce no trades (fast >= slow rejected),
        # so trials list may be empty — that's ok

    def test_optimize_total_pnl(self):
        candles = _candles(80)
        opt = StrategyOptimizer(
            MACrossoverStrategy, candles,
            metric="total_pnl", n_trials=5,
        )
        result = opt.optimize()
        assert result.metric == "total_pnl"

    def test_trials_sorted_by_metric(self):
        candles = _candles(80)
        opt = StrategyOptimizer(
            MACrossoverStrategy, candles,
            metric="sharpe_ratio", n_trials=8,
        )
        result = opt.optimize()
        if len(result.trials) >= 2:
            assert result.trials[0].metric_value >= result.trials[1].metric_value

    def test_walk_forward(self):
        candles = _candles(200)
        opt = StrategyOptimizer(
            MACrossoverStrategy, candles,
            metric="total_pnl", n_trials=5,
        )
        wf = opt.walk_forward(n_splits=2, trials_per_split=5)
        assert "windows" in wf
        assert "avg_oos_sharpe" in wf

    def test_no_parameters_raises(self):
        from flint.strategy.base import Strategy
        from flint.models import Signal

        class NoParams(Strategy):
            @property
            def name(self): return "NoParams"
            def on_candle(self, c, h, ctx=None): return Signal.HOLD
            def reset(self): pass

        import pytest
        with pytest.raises(ValueError, match="no parameters"):
            StrategyOptimizer(NoParams, _candles())
