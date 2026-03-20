"""Tests for data models."""
from flint.models import Candle, Position, Signal, Side, BacktestResult


def test_candle_frozen():
    c = Candle(ts=1, open=1, high=2, low=0.5, close=1.5, volume=10, market="X", resolution_s=60)
    assert c.ts == 1
    assert c.market == "X"
    # frozen — cannot assign
    try:
        c.ts = 2  # type: ignore[misc]
        assert False, "Should raise"
    except AttributeError:
        pass


def test_signal_values():
    assert Signal.BUY.value == "buy"
    assert Signal.SELL.value == "sell"
    assert Signal.HOLD.value == "hold"


def test_position_long():
    p = Position(entry_price=100, size=1.0, entry_ts=1000)
    assert p.side == Side.LONG
    assert not p.closed
    pnl = p.close(110, 2000)
    assert pnl == 10.0
    assert p.closed
    assert p.exit_price == 110
    assert p.exit_ts == 2000


def test_position_short():
    p = Position(entry_price=100, size=-1.0, entry_ts=1000)
    assert p.side == Side.SHORT
    pnl = p.close(90, 2000)
    assert pnl == 10.0  # (90-100)*(-1) = 10


def test_backtest_result_defaults():
    r = BacktestResult(
        total_pnl=100,
        win_rate=0.6,
        max_drawdown=0.05,
        sharpe_ratio=1.2,
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
    )
    assert r.positions == []
    assert r.equity_curve == []
