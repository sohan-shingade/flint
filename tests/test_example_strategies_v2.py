"""Tests for v0.2 example strategies."""
from __future__ import annotations

from flint.backtest.engine import BacktestEngine
from flint.models import Candle, Signal
from flint.strategy import (
    FundingHarvestStrategy,
    MeanReversionStrategy,
    BreakoutMomentumStrategy,
    GridTraderStrategy,
    DualTimeframeStrategy,
)


def _oscillating(n=80):
    cs = []
    for i in range(n):
        cycle = i % 40
        if cycle < 10:
            price = 100 - cycle * 1.0
        elif cycle < 20:
            price = 90 + (cycle - 10) * 2.0
        elif cycle < 30:
            price = 110 - (cycle - 20) * 1.0
        else:
            price = 100 + (cycle - 30) * 0.5
        cs.append(Candle(ts=i * 3600, open=price, high=price + 2,
                         low=price - 2, close=price, volume=100 + i * 5,
                         market="SOL-PERP", resolution_s=3600))
    return cs


class TestFundingHarvest:
    def test_runs_backtest(self):
        strategy = FundingHarvestStrategy()
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_oscillating(80))
        assert len(result.equity_curve) == 80

    def test_has_parameters(self):
        params = FundingHarvestStrategy.parameters()
        assert "entry_threshold" in params
        assert "stop_loss_pct" in params

    def test_name(self):
        s = FundingHarvestStrategy(entry_threshold=0.002)
        assert "0.002" in s.name


class TestMeanReversion:
    def test_runs_backtest(self):
        strategy = MeanReversionStrategy(period=10, entry_z=1.5)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_oscillating(80))
        assert len(result.equity_curve) == 80

    def test_generates_signals(self):
        strategy = MeanReversionStrategy(period=10, entry_z=1.5)
        candles = _oscillating(30)
        history = []
        signals = []
        for c in candles:
            history.append(c)
            s = strategy.on_candle(c, history)
            signals.append(s)
        assert Signal.BUY in signals or Signal.SELL in signals

    def test_has_parameters(self):
        assert len(MeanReversionStrategy.parameters()) >= 3


class TestBreakoutMomentum:
    def test_runs_backtest(self):
        strategy = BreakoutMomentumStrategy(lookback=10)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_oscillating(80))
        assert len(result.equity_curve) == 80

    def test_has_parameters(self):
        assert "lookback" in BreakoutMomentumStrategy.parameters()


class TestGridTrader:
    def test_runs_backtest_with_ctx(self):
        strategy = GridTraderStrategy(grid_spacing_pct=3.0, grid_levels=2)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_oscillating(40))
        assert len(result.equity_curve) == 40

    def test_has_parameters(self):
        params = GridTraderStrategy.parameters()
        assert "grid_spacing_pct" in params
        assert "grid_levels" in params


class TestDualTimeframe:
    def test_runs_backtest(self):
        strategy = DualTimeframeStrategy(trend_period=30, entry_period=5)
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0)
        result = engine.run(_oscillating(80))
        assert len(result.equity_curve) == 80

    def test_has_parameters(self):
        params = DualTimeframeStrategy.parameters()
        assert "trend_period" in params
        assert "entry_period" in params
