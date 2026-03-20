"""Tests for analytics module — metrics and tearsheet."""
import pytest

from flint.analytics.metrics import compute_metrics, MetricsSummary, _max_drawdown_duration
from flint.analytics.tearsheet import generate_tearsheet, _buy_and_hold, _compute_monthly_returns
from flint.models import BacktestResult, Candle, Position

import numpy as np


# -- Fixtures ------------------------------------------------------------------

def _make_result(
    pnl: float = 500.0,
    positions: list = None,
    equity: list = None,
) -> BacktestResult:
    if positions is None:
        positions = [
            Position(100, 10.0, 1700000000, 110, 1700010000, 100.0, True),
            Position(105, 10.0, 1700020000, 95, 1700030000, -100.0, True),
            Position(90, 10.0, 1700040000, 140, 1700090000, 500.0, True),
        ]
    if equity is None:
        equity = [10000, 10050, 10100, 10000, 9900, 10100, 10300, 10500]
    return BacktestResult(
        total_pnl=pnl,
        win_rate=2 / 3,
        max_drawdown=0.02,
        sharpe_ratio=1.5,
        total_trades=3,
        winning_trades=2,
        losing_trades=1,
        positions=positions,
        equity_curve=equity,
    )


def _make_candles(n: int = 50) -> list:
    base_ts = 1709251200  # 2024-03-01 00:00 UTC
    return [
        Candle(
            ts=base_ts + i * 3600,
            open=100 + i * 0.5 - 0.25,
            high=100 + i * 0.5 + 1,
            low=100 + i * 0.5 - 1,
            close=100 + i * 0.5,
            volume=100.0,
            market="SOL-PERP",
            resolution_s=3600,
        )
        for i in range(n)
    ]


# -- Metrics -------------------------------------------------------------------

def test_compute_metrics_basic():
    result = _make_result()
    m = compute_metrics(result, initial_capital=10000)

    assert isinstance(m, MetricsSummary)
    assert m.total_return_pct == pytest.approx(5.0)  # 10500/10000 = +5%
    assert m.total_trades == 3
    assert m.win_rate == pytest.approx(2 / 3)
    assert m.avg_win > 0
    assert m.avg_loss < 0
    assert m.best_trade == 500.0
    assert m.worst_trade == -100.0


def test_compute_metrics_sharpe_sortino():
    result = _make_result()
    m = compute_metrics(result, initial_capital=10000)

    assert m.sharpe_ratio != 0
    assert m.sortino_ratio != 0
    # Sortino should be >= Sharpe (only penalises downside)
    assert m.sortino_ratio >= m.sharpe_ratio


def test_compute_metrics_profit_factor():
    result = _make_result()
    m = compute_metrics(result, initial_capital=10000)

    # 2 winners (100+500=600), 1 loser (100), pf = 6.0
    assert m.profit_factor == pytest.approx(6.0)


def test_compute_metrics_no_trades():
    result = BacktestResult(0, 0, 0, 0, 0, 0, 0, [], [10000, 10000])
    m = compute_metrics(result, initial_capital=10000)

    assert m.total_trades == 0
    assert m.win_rate == 0
    assert m.profit_factor == 0
    assert m.avg_holding_period_s == 0


def test_compute_metrics_empty_equity():
    result = BacktestResult(0, 0, 0, 0, 0, 0, 0, [], [])
    m = compute_metrics(result, initial_capital=10000)

    assert m.total_return_pct == 0


def test_max_drawdown_duration():
    dd = np.array([0, 0.01, 0.02, 0.03, 0, 0, 0.01, 0, 0.01, 0.02])
    assert _max_drawdown_duration(dd) == 3  # indices 1-3


def test_max_drawdown_duration_empty():
    assert _max_drawdown_duration(np.array([])) == 0


# -- Tearsheet -----------------------------------------------------------------

def test_generate_tearsheet():
    candles = _make_candles(8)
    result = _make_result()

    ts = generate_tearsheet(result, candles, "TestStrategy", 10000)

    assert ts.strategy_name == "TestStrategy"
    assert ts.market == "SOL-PERP"
    assert ts.resolution_s == 3600
    assert ts.initial_capital == 10000
    assert len(ts.equity_curve) == 8
    assert len(ts.drawdown_curve) == 8
    assert len(ts.trades) == 3
    assert ts.trades[0]["side"] in ("long", "short")
    assert ts.buy_hold_return_pct > 0  # prices go up


def test_tearsheet_to_dict():
    candles = _make_candles(8)
    result = _make_result()
    ts = generate_tearsheet(result, candles, "Test", 10000)
    d = ts.to_dict()

    assert isinstance(d, dict)
    assert "metrics" in d
    assert "equity_curve" in d
    assert "trades" in d
    assert "buy_hold_equity" in d


def test_buy_and_hold():
    candles = [
        Candle(1000, 99, 101, 98, 100, 10, "X", 60),
        Candle(1060, 100, 120, 99, 110, 10, "X", 60),
        Candle(1120, 110, 115, 105, 105, 10, "X", 60),
    ]
    bh, ret = _buy_and_hold(candles, 10000)

    assert len(bh) == 3
    assert bh[0] == pytest.approx(10000)  # 10000/100 * 100
    assert bh[1] == pytest.approx(11000)  # 100 units * 110
    assert ret == pytest.approx(5.0)  # 10500/10000


def test_monthly_returns():
    # 2 months of hourly timestamps
    ts_list = list(range(1709251200, 1709251200 + 60 * 24 * 3600, 3600))
    equity = [10000 + i * 0.5 for i in range(len(ts_list))]

    monthly = _compute_monthly_returns(ts_list, equity)
    assert isinstance(monthly, dict)
    assert 2024 in monthly
