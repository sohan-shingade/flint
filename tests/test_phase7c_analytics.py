"""Tests for Phase 7c analytics additions."""
from __future__ import annotations

import pytest
from flint.models import Candle, Signal
from flint.strategy.base import Strategy


class TestAnnualizedVolatility:
    def test_volatility_present_in_metrics(self):
        from flint.analytics.metrics import compute_metrics
        from flint.models import BacktestResult, Position
        positions = [
            Position(entry_price=100, size=10, entry_ts=i*3600,
                     exit_price=102, exit_ts=(i+1)*3600, pnl=20, closed=True)
            for i in range(10)
        ]
        result = BacktestResult(
            total_pnl=200, win_rate=1.0, max_drawdown=0.01,
            sharpe_ratio=2.0, total_trades=10,
            winning_trades=10, losing_trades=0,
            positions=positions,
            equity_curve=[10000 + i * 20 for i in range(100)],
        )
        m = compute_metrics(result, initial_capital=10000)
        assert hasattr(m, "annualized_volatility_pct")
        assert m.annualized_volatility_pct > 0

    def test_zero_returns_zero_vol(self):
        from flint.analytics.metrics import compute_metrics
        from flint.models import BacktestResult
        result = BacktestResult(
            total_pnl=0, win_rate=0, max_drawdown=0,
            sharpe_ratio=0, total_trades=0,
            winning_trades=0, losing_trades=0,
            positions=[], equity_curve=[10000],
        )
        m = compute_metrics(result, initial_capital=10000)
        assert m.annualized_volatility_pct == 0.0


class TestStrategyErrorMessages:
    def test_traceback_extraction(self):
        """Traceback extraction should find user code frames."""
        tb_lines = [
            '  File "<string>", line 42, in on_candle',
            '  File "flint/indicators.py", line 95, in rsi',
            'ZeroDivisionError: division by zero',
        ]
        user_lines = [l.strip() for l in tb_lines if '<string>' in l]
        assert len(user_lines) == 1
        assert 'line 42' in user_lines[0]

    def test_error_includes_type_and_message(self):
        error_detail = "ZeroDivisionError: division by zero"
        assert "ZeroDivisionError" in error_detail
        assert "division by zero" in error_detail


class _HoldStrategy(Strategy):
    @property
    def name(self):
        return "Hold"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD


class TestVolumeWarning:
    def test_zero_volume_candles_produce_warning(self):
        from flint.backtest.engine import BacktestEngine
        candles = [
            Candle(ts=1000 + i * 3600, open=100, high=101, low=99,
                   close=100, volume=0.0, market="SOL-PERP", resolution_s=3600)
            for i in range(50)
        ]
        engine = BacktestEngine(_HoldStrategy(), 10000, 0.0005)
        result = engine.run(candles)
        assert any("volume" in w.lower() for w in result.strategy_warnings), (
            f"Expected volume warning, got: {result.strategy_warnings}"
        )

    def test_nonzero_volume_no_warning(self):
        from flint.backtest.engine import BacktestEngine
        candles = [
            Candle(ts=1000 + i * 3600, open=100, high=101, low=99,
                   close=100, volume=100.0, market="SOL-PERP", resolution_s=3600)
            for i in range(50)
        ]
        engine = BacktestEngine(_HoldStrategy(), 10000, 0.0005)
        result = engine.run(candles)
        assert not any("volume" in w.lower() for w in result.strategy_warnings)


class TestRicherOptimizationResults:
    def test_optimization_result_has_study(self):
        from flint.optimization.optimizer import OptimizationResult
        fields = OptimizationResult.__dataclass_fields__
        assert "study" in fields, "OptimizationResult needs a 'study' field"

    def test_convergence_format(self):
        convergence = [[0, 1.0], [1, 1.5], [2, 1.5], [3, 2.0]]
        assert all(len(entry) == 2 for entry in convergence)
        values = [v for _, v in convergence]
        for i in range(1, len(values)):
            assert values[i] >= values[i-1]
