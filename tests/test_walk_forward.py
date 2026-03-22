"""Tests for walk-forward analysis."""
from __future__ import annotations

from flint.models import Candle
from flint.optimization.walk_forward import run_walk_forward, WalkForwardResult
from flint.strategy import MACrossoverStrategy


def _oscillating_candles(n=200):
    cs = []
    for i in range(n):
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


class TestWalkForward:
    def test_basic_walk_forward(self):
        candles = _oscillating_candles(200)
        result = run_walk_forward(
            MACrossoverStrategy, candles,
            n_windows=3, trials_per_window=5,
        )
        assert isinstance(result, WalkForwardResult)
        assert result.strategy_name == "MACrossoverStrategy"

    def test_returns_metrics(self):
        candles = _oscillating_candles(200)
        result = run_walk_forward(
            MACrossoverStrategy, candles,
            n_windows=2, trials_per_window=5,
        )
        # Should have computed averages
        assert isinstance(result.avg_is_sharpe, float)
        assert isinstance(result.avg_oos_sharpe, float)
        assert isinstance(result.overfitting_ratio, float)

    def test_parameter_stability(self):
        candles = _oscillating_candles(300)
        result = run_walk_forward(
            MACrossoverStrategy, candles,
            n_windows=3, trials_per_window=5,
        )
        # If windows ran, we should have stability scores
        if result.n_windows >= 2:
            assert isinstance(result.parameter_stability, dict)

    def test_too_few_candles(self):
        candles = _oscillating_candles(10)
        result = run_walk_forward(
            MACrossoverStrategy, candles,
            n_windows=3, trials_per_window=3,
        )
        assert result.n_windows == 0  # not enough data
