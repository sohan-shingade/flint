"""Tests for extended metrics: turnover_annualized, calmar_ratio."""
from flint.analytics.metrics import compute_metrics
from flint.models import BacktestResult, Position, Fill, Side


def _make_result(pnl=500, trades=10, equity=None, fills=None):
    positions = [
        Position(entry_price=100, size=10, entry_ts=1700000000 + i * 7200,
                 exit_price=100 + (5 if i % 2 == 0 else -3), exit_ts=1700000000 + i * 7200 + 3600,
                 pnl=50 if i % 2 == 0 else -30, closed=True)
        for i in range(trades)
    ]
    return BacktestResult(
        total_pnl=pnl, win_rate=0.5, max_drawdown=0.1,
        sharpe_ratio=1.0, total_trades=trades,
        winning_trades=trades // 2, losing_trades=trades - trades // 2,
        positions=positions,
        equity_curve=equity or [10000 + i * 50 for i in range(100)],
        fills=fills if fills is not None else [
            Fill(market="SOL-PERP", side=Side.LONG, price=100, size=10, fee=1, ts=1700000000 + i * 3600)
            for i in range(trades * 2)
        ],
        total_fees=20.0,
    )


class TestTurnoverAnnualized:
    def test_turnover_exists(self):
        result = _make_result()
        m = compute_metrics(result, 10000)
        assert hasattr(m, 'turnover_annualized')

    def test_turnover_positive(self):
        result = _make_result(trades=20)
        m = compute_metrics(result, 10000)
        assert m.turnover_annualized > 0

    def test_turnover_zero_with_no_fills(self):
        result = _make_result(fills=[])
        m = compute_metrics(result, 10000)
        assert m.turnover_annualized == 0


class TestCalmarRatio:
    def test_calmar_exists(self):
        result = _make_result()
        m = compute_metrics(result, 10000)
        assert hasattr(m, 'calmar_ratio')

    def test_calmar_positive_when_profitable_with_drawdown(self):
        # Equity: up, then dip, then higher — has both return and drawdown
        eq = [10000 + i * 100 for i in range(50)] + [14000 - i * 50 for i in range(20)] + [13500 + i * 100 for i in range(30)]
        result = _make_result(pnl=3500, equity=eq)
        m = compute_metrics(result, 10000)
        assert m.calmar_ratio > 0

    def test_calmar_zero_when_no_drawdown(self):
        # Monotonically increasing equity — no drawdown
        eq = [10000 + i * 10 for i in range(100)]
        result = _make_result(pnl=990, equity=eq)
        m = compute_metrics(result, 10000)
        assert m.calmar_ratio == 0  # max_dd < 0.001 threshold
