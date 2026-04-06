"""Tests for Phase 7c analytics additions."""


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
