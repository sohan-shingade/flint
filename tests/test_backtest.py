"""Tests for the backtest engine."""
import pytest

from flint.backtest.engine import BacktestEngine, _max_drawdown, _sharpe_ratio
from flint.execution.fill_models import ClosePriceFill, SlippageFill
from flint.models import Candle, Signal
from flint.strategy.ma_crossover import MACrossoverStrategy


def test_max_drawdown_flat():
    assert _max_drawdown([100, 100, 100]) == 0.0


def test_max_drawdown_simple():
    equity = [100, 110, 90, 95, 80, 100]
    dd = _max_drawdown(equity)
    # peak 110, trough 80 → dd = 30/110 ≈ 0.2727
    assert dd == pytest.approx(30 / 110, abs=0.001)


def test_max_drawdown_empty():
    assert _max_drawdown([]) == 0.0


def test_sharpe_ratio_flat():
    assert _sharpe_ratio([100, 100, 100]) == 0.0


def test_sharpe_ratio_positive():
    equity = [100, 101, 102, 103, 104]
    s = _sharpe_ratio(equity)
    assert s > 0


def test_backtest_no_candles():
    strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
    engine = BacktestEngine(strategy)
    result = engine.run([])
    assert result.total_pnl == 0.0
    assert result.total_trades == 0


def test_backtest_on_synthetic(sample_candles):
    strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
    engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0,
                            fill_model=SlippageFill(slippage_bps=5))
    result = engine.run(sample_candles)

    assert result.total_trades > 0
    assert len(result.equity_curve) == 60
    assert result.winning_trades + result.losing_trades == result.total_trades
    assert 0 <= result.win_rate <= 1.0
    assert 0 <= result.max_drawdown <= 1.0


def test_backtest_fees_reduce_pnl(sample_candles):
    strategy = MACrossoverStrategy(fast_period=5, slow_period=10)

    engine_no_fee = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0,
                                   fill_model=SlippageFill(slippage_bps=5))
    result_no_fee = engine_no_fee.run(sample_candles)

    strategy2 = MACrossoverStrategy(fast_period=5, slow_period=10)
    engine_fee = BacktestEngine(strategy2, initial_capital=10_000, fee_rate=0.001,
                                fill_model=SlippageFill(slippage_bps=5))
    result_fee = engine_fee.run(sample_candles)

    assert result_no_fee.total_pnl >= result_fee.total_pnl


def test_backtest_closes_open_position(sample_candles):
    """If strategy never sells, engine force-closes at the end."""

    class AlwaysBuy(MACrossoverStrategy):
        def on_candle(self, candle, history):
            if len(history) == 1:
                return Signal.BUY
            return Signal.HOLD

    strategy = AlwaysBuy(fast_period=3, slow_period=5)
    engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0,
                            fill_model=ClosePriceFill())
    result = engine.run(sample_candles)

    assert result.total_trades == 1
    assert result.positions[0].closed
