"""Tests for Task 7: Backtest engine borrow rate application.

Borrow cost is realized on position close, NOT per-bar.
It uses cumulative_rate from BorrowSnapshot to compute the cost
as: (cum_at_close - cum_at_entry) * position_notional_usd.
"""
import pytest

from flint.backtest.engine import BacktestEngine
from flint.execution.backtest_context import BacktestContext
from flint.execution.fill_models import ClosePriceFill
from flint.execution.fee_models import FlatFeeModel, ZeroFeeModel
from flint.models import BorrowSnapshot, Candle, Side
from flint.strategy import Strategy


# --- Helper strategy classes ---

class BuyThenSellStrategy(Strategy):
    """Opens a jupiter long on bar 1, closes on bar 3."""

    @property
    def name(self):
        return "BuyThenSell"

    def reset(self):
        self.bar = 0

    def on_candle(self, candle, history, ctx=None):
        if ctx is None:
            return
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="jupiter")
        elif self.bar == 3:
            ctx.market_order("SOL-PERP", Side.SHORT, 10.0, venue="jupiter")


class DriftBuyStrategy(Strategy):
    """Opens a drift long on bar 1 (never closes — force closed at end)."""

    @property
    def name(self):
        return "DriftBuy"

    def reset(self):
        self.bar = 0

    def on_candle(self, candle, history, ctx=None):
        if ctx is None:
            return
        self.bar += 1
        if self.bar == 1:
            ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="drift")


def _candles():
    return [
        Candle(ts=1000, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600),
        Candle(ts=2000, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600),
        Candle(ts=3000, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600),
        Candle(ts=4000, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0, market="SOL-PERP", resolution_s=3600),
    ]


def _borrow_snapshots():
    return [
        BorrowSnapshot("SOL-PERP", 1000, 0.001, 0.5, 1.000, "rpc"),
        BorrowSnapshot("SOL-PERP", 2000, 0.001, 0.5, 1.001, "rpc"),
        BorrowSnapshot("SOL-PERP", 3000, 0.001, 0.5, 1.002, "rpc"),
        BorrowSnapshot("SOL-PERP", 4000, 0.001, 0.5, 1.003, "rpc"),
    ]


def test_borrow_cost_deducted_on_close():
    """Jupiter position held from ts=1000 (cum=1.000) to ts=3000 (cum=1.002).
    Size USD = 10 * 100 = 1000. Borrow cost = (1.002 - 1.000) * 1000 = 2.0.
    """
    strategy = BuyThenSellStrategy()
    engine = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0006,
        borrow_snapshots=_borrow_snapshots(),
        fill_model=ClosePriceFill(),
    )
    result = engine.run(_candles())

    assert result.jupiter_borrow_paid > 0
    assert abs(result.jupiter_borrow_paid - 2.0) < 0.5


def test_no_borrow_cost_for_drift_positions():
    """Drift positions should NOT accrue borrow costs."""
    strategy = DriftBuyStrategy()
    engine = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0006,
        borrow_snapshots=_borrow_snapshots(),
        fill_model=ClosePriceFill(),
    )
    result = engine.run(_candles())

    assert result.jupiter_borrow_paid == 0.0


def test_borrow_snapshots_fed_to_context():
    """Borrow snapshots should be accessible via ctx after engine feeds them."""
    strategy = BuyThenSellStrategy()
    engine = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0,
        borrow_snapshots=_borrow_snapshots(),
        fill_model=ClosePriceFill(),
        fee_model=ZeroFeeModel(),
    )
    result = engine.run(_candles())
    # If we got here without error, borrow snapshots were fed correctly.
    # The borrow cost should have been computed.
    assert result.jupiter_borrow_paid > 0


def test_borrow_cost_reduces_equity():
    """Equity should be lower when borrow cost is applied."""
    strategy = BuyThenSellStrategy()

    # Run without borrow
    engine_no_borrow = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0,
        fill_model=ClosePriceFill(),
        fee_model=ZeroFeeModel(),
    )
    result_no_borrow = engine_no_borrow.run(_candles())

    # Run with borrow
    strategy.reset()
    engine_borrow = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0,
        borrow_snapshots=_borrow_snapshots(),
        fill_model=ClosePriceFill(),
        fee_model=ZeroFeeModel(),
    )
    result_borrow = engine_borrow.run(_candles())

    # Borrow cost should reduce PnL
    assert result_borrow.total_pnl < result_no_borrow.total_pnl


def test_no_borrow_without_snapshots():
    """Without borrow snapshots, jupiter_borrow_paid should be 0."""
    strategy = BuyThenSellStrategy()
    engine = BacktestEngine(
        strategy,
        initial_capital=10000.0,
        fee_rate=0.0,
        fill_model=ClosePriceFill(),
        fee_model=ZeroFeeModel(),
    )
    result = engine.run(_candles())
    assert result.jupiter_borrow_paid == 0.0
