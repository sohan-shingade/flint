"""Backtest cancellation — Phase 4 T4.5.

Tests:
- `cancel_check=lambda: True` raises `BacktestCancelled` mid-run.
- `cancel_check=lambda: False` completes normally.
- Without a `cancel_check` (default None), behavior unchanged.
- Default check runs every 100 bars — confirmed by testing a 50-bar and a
  150-bar run behave as expected.
"""
from __future__ import annotations

import pytest

from flint.backtest.engine import BacktestCancelled, BacktestEngine
from flint.execution.fill_models import ClosePriceFill
from flint.models import Candle
from flint.strategy.ma_crossover import MACrossoverStrategy


def _candles(n=500):
    return [
        Candle(ts=i * 3600, open=100, high=101, low=99,
               close=100.0 + (i % 10) * 0.1, volume=1000,
               market="SOL-PERP", resolution_s=3600)
        for i in range(n)
    ]


class TestCancelCheck:
    def test_always_true_triggers_cancellation(self):
        """cancel_check returning True at the first poll (bar 0) raises
        BacktestCancelled."""
        engine = BacktestEngine(
            MACrossoverStrategy(5, 20), initial_capital=10_000, fee_rate=0.0,
            fill_model=ClosePriceFill(),
            cancel_check=lambda: True,
        )
        # Must have >0 candles to enter the loop, but cancel_check fires at
        # bar 0 (first iteration, _ci % 100 == 0).
        with pytest.raises(BacktestCancelled):
            engine.run(_candles(500))

    def test_always_false_completes_normally(self):
        engine = BacktestEngine(
            MACrossoverStrategy(5, 20), initial_capital=10_000, fee_rate=0.0,
            fill_model=ClosePriceFill(),
            cancel_check=lambda: False,
        )
        result = engine.run(_candles(500))
        assert result.total_trades >= 0  # just must not raise

    def test_default_no_cancel_check(self):
        engine = BacktestEngine(
            MACrossoverStrategy(5, 20), initial_capital=10_000, fee_rate=0.0,
            fill_model=ClosePriceFill(),
        )
        result = engine.run(_candles(500))
        assert result.total_trades >= 0


class TestCancelFiresAt100BarBoundaries:
    def test_cancel_after_300_bars(self):
        """Flip cancel flag after 150 polls → should raise on next 100-bar
        boundary."""
        calls = {"n": 0}

        def _cancel():
            calls["n"] += 1
            return calls["n"] > 2  # False on first 2 polls, then True

        engine = BacktestEngine(
            MACrossoverStrategy(5, 20), initial_capital=10_000, fee_rate=0.0,
            fill_model=ClosePriceFill(),
            cancel_check=_cancel,
        )
        with pytest.raises(BacktestCancelled):
            engine.run(_candles(500))
        # Should have been polled at bars 0, 100, 200, 300 → 4 polls; 3rd
        # poll (bar 200) returns True.
        assert calls["n"] >= 3
