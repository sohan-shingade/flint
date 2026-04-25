"""Tests for Task 6: BacktestContext borrow rate tracking."""

from flint.execution.backtest_context import BacktestContext
from flint.models import BorrowSnapshot


def _make_ctx(**kwargs):
    defaults = dict(initial_capital=10000.0)
    defaults.update(kwargs)
    return BacktestContext(**defaults)


def test_add_and_get_borrow_rate():
    ctx = _make_ctx()
    bs = BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc")
    ctx.add_borrow_rate(bs)
    rate = ctx.get_borrow_rate("SOL-PERP")
    assert rate == 0.00008


def test_get_borrow_rate_returns_none_when_empty():
    ctx = _make_ctx()
    assert ctx.get_borrow_rate("SOL-PERP") is None


def test_get_borrow_rates_with_lookback():
    ctx = _make_ctx()
    for i in range(10):
        ts = 1000 + i * 3600
        bs = BorrowSnapshot("SOL-PERP", ts, 0.00008 + i * 0.00001, 0.65, 1.001 + i * 0.001, "rpc")
        ctx.add_borrow_rate(bs)
    rates = ctx.get_borrow_rates("SOL-PERP", lookback=5)
    assert len(rates) == 5
    assert rates[-1][1] == 0.00008 + 9 * 0.00001


def test_get_borrow_cumulative_at():
    ctx = _make_ctx()
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc"))
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 2000, 0.00009, 0.70, 1.002, "rpc"))
    ctx.add_borrow_rate(BorrowSnapshot("SOL-PERP", 3000, 0.00010, 0.75, 1.003, "rpc"))
    assert ctx.get_borrow_cumulative_at("SOL-PERP", 2000) == 1.002
    assert ctx.get_borrow_cumulative_at("SOL-PERP", 2500) == 1.002
    assert ctx.get_borrow_cumulative_at("SOL-PERP", 500) is None


def test_get_borrow_rate_uses_current_candle_market():
    """When no market is passed, fall back to current candle's market."""
    from flint.models import Candle
    ctx = _make_ctx()
    bs = BorrowSnapshot("SOL-PERP", 1000, 0.00008, 0.65, 1.001, "rpc")
    ctx.add_borrow_rate(bs)
    candle = Candle(ts=1000, open=100, high=101, low=99, close=100, volume=1000, market="SOL-PERP", resolution_s=3600)
    ctx.set_candle(candle)
    rate = ctx.get_borrow_rate()  # no market arg
    assert rate == 0.00008


def test_get_borrow_rates_empty_market():
    ctx = _make_ctx()
    assert ctx.get_borrow_rates("BTC-PERP") == []


def test_get_borrow_cumulative_at_empty_market():
    ctx = _make_ctx()
    assert ctx.get_borrow_cumulative_at("BTC-PERP", 1000) is None
