"""Tests for the backtest engine."""
import pytest

from flint.backtest.engine import BacktestEngine, _max_drawdown, _sharpe_ratio
from flint.execution.fill_models import ClosePriceFill, SlippageFill
from flint.models import Signal
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


def test_backtest_force_close_appends_terminal_equity(sample_candles):
    """Force-closing an open position must append a terminal equity point,
    not overwrite the last bar's mark-to-market equity. Regression for
    Phase 1 T1.1.f (audit 2026-04-23)."""

    class AlwaysBuy(MACrossoverStrategy):
        def on_candle(self, candle, history):
            if len(history) == 1:
                return Signal.BUY
            return Signal.HOLD

    strategy = AlwaysBuy(fast_period=3, slow_period=5)
    # Non-zero fee so force-close exit fee produces a visible delta
    engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.001,
                            fill_model=ClosePriceFill())
    result = engine.run(sample_candles)

    # Position remained open through last bar and was force-closed.
    assert result.total_trades == 1
    assert result.positions[0].closed

    # Curve length = len(candles) + 1 (one terminal point for force-close).
    # Core invariant of T1.1.f — mark-to-market last-bar equity preserved,
    # terminal equity recorded as distinct point.
    assert len(result.equity_curve) == len(sample_candles) + 1

    # Terminal equity must not exceed last-bar mark-to-market (force-close
    # converts unrealized P&L to realized, minus exit fees if charged).
    # NOTE: Rust path currently doesn't charge force-close exit fees
    # (rust/src/engine/positions.rs:198 — separate parity bug tracked
    # under T1.1.b follow-ups). On Rust: terminal == last_bar.
    # On Python with fees: terminal < last_bar.
    last_bar_equity = result.equity_curve[-2]
    terminal_equity = result.equity_curve[-1]
    assert terminal_equity <= last_bar_equity + 1e-6


def test_backtest_no_force_close_no_extra_point(sample_candles):
    """Strategy that closes its own position should produce equity_curve of
    length == len(candles). No phantom terminal point appended."""

    class BuyThenSell(MACrossoverStrategy):
        def on_candle(self, candle, history):
            if len(history) == 1:
                return Signal.BUY
            if len(history) == 3:
                return Signal.SELL
            return Signal.HOLD

    strategy = BuyThenSell(fast_period=3, slow_period=5)
    engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0,
                            fill_model=ClosePriceFill())
    result = engine.run(sample_candles)

    assert result.total_trades == 1
    assert len(result.equity_curve) == len(sample_candles)


def test_seeded_backtest_is_deterministic(sample_candles):
    """Phase 1 T1.1.c — explicit seed must produce byte-identical output
    across runs on the same candles/strategy. Guards the Rust VenueFiller
    RNG path plus the Python LatencyStage default."""

    def _run():
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0005,
                                fill_model=ClosePriceFill(), seed=12345)
        return engine.run(sample_candles)

    r_a = _run()
    r_b = _run()

    assert r_a.total_pnl == r_b.total_pnl
    assert r_a.sharpe_ratio == r_b.sharpe_ratio
    assert r_a.max_drawdown == r_b.max_drawdown
    assert r_a.total_trades == r_b.total_trades
    assert r_a.equity_curve == r_b.equity_curve


def test_default_seed_is_deterministic(sample_candles):
    """Phase 1 T1.1.c — even without an explicit seed, repeated runs with
    the same strategy + candles must produce identical results (seed is
    derived deterministically from strategy name and first candle ts)."""

    def _run():
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0005,
                                fill_model=ClosePriceFill())
        return engine.run(sample_candles)

    r_a = _run()
    r_b = _run()

    assert r_a.total_pnl == r_b.total_pnl
    assert r_a.equity_curve == r_b.equity_curve
