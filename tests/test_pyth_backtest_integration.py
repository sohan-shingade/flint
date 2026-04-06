"""Pyth pipeline integration tests: backtest engine verification and full pipeline."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

from flint.backtest.engine import BacktestEngine
from flint.models import Candle, Side, Signal
from flint.strategy.base import Strategy
from flint.strategy.loader import load_user_strategy


# ---------------------------------------------------------------------------
# Task 7: Backtest engine verification with venue='pyth' candles
# ---------------------------------------------------------------------------

class _BuyOnFirstBar(Strategy):
    """Minimal v2 strategy: buy on bar 1 and hold until force-closed at end."""

    @property
    def name(self) -> str:
        return "BuyOnFirstBar"

    def __init__(self):
        self.bar = 0

    def on_candle(self, candle, history, ctx=None):
        self.bar += 1
        if self.bar == 1 and ctx is not None:
            ctx.market_order(candle.market, Side.LONG, 1.0)
        return Signal.HOLD

    def reset(self):
        self.bar = 0


def test_backtest_works_with_pyth_venue_candles():
    """Engine must handle candles with venue='pyth' without errors."""
    candles = [
        Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
        Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "pyth"),
        Candle(1700007200, 101.0, 103.0, 100.5, 102.0, 1200.0, "SOL-PERP", 3600, "pyth"),
    ]
    strategy = _BuyOnFirstBar()
    engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
    result = engine.run(candles)

    assert result is not None
    assert result.total_trades >= 1


def test_backtest_pyth_candles_venue_preserved():
    """Venue metadata on candles should not break any engine internals."""
    candles = [
        Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
               1000.0, "SOL-PERP", 3600, "pyth")
        for i in range(20)
    ]
    strategy = _BuyOnFirstBar()
    engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
    result = engine.run(candles)

    assert result is not None
    assert len(result.equity_curve) == 20


def test_backtest_pyth_candles_via_loader():
    """load_user_strategy + BacktestEngine must work end-to-end with pyth candles."""
    code = '''\
from flint.strategy.base import Strategy
from flint.models import Signal, Side

class MyStrategy(Strategy):
    @property
    def name(self):
        return "MyStrategy"

    def __init__(self):
        self.bar = 0

    def on_candle(self, candle, history, ctx=None):
        self.bar += 1
        if self.bar == 1 and ctx is not None:
            ctx.market_order(candle.market, Side.LONG, 1.0)
        return Signal.HOLD

    def reset(self):
        self.bar = 0
'''
    strategy = load_user_strategy(code)
    candles = [
        Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
               1000.0, "SOL-PERP", 3600, "pyth")
        for i in range(5)
    ]
    engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
    result = engine.run(candles)

    assert result is not None
    assert result.total_trades >= 1


def test_backtest_mixed_venues_treated_independently():
    """Dict input with drift and pyth keys for the same market must run without errors.

    When the same market appears under two venue keys the engine picks whichever
    stream is longest (equal here), so we just check it completes successfully.
    """
    drift_candles = [
        Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
               1000.0, "SOL-PERP", 3600, "drift")
        for i in range(10)
    ]
    pyth_candles = [
        Candle(1700000000 + i * 3600, 100.1 + i, 101.1 + i, 99.1 + i, 100.6 + i,
               1000.0, "SOL-PERP", 3600, "pyth")
        for i in range(10)
    ]
    strategy = _BuyOnFirstBar()
    engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
    result = engine.run({"drift:SOL-PERP": drift_candles, "pyth:SOL-PERP": pyth_candles})

    assert result is not None
    assert len(result.equity_curve) == 10


# ---------------------------------------------------------------------------
# Task 8: Full pipeline integration tests
# ---------------------------------------------------------------------------

def _make_store():
    from flint.store import FlintStore
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_full_pipeline_pyth_store_and_backtest():
    """Insert Pyth candles → query → run backtest. Asserts PnL > 0 on rising prices."""
    store, path = _make_store()
    try:
        candles = [
            Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
                   1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(10)
        ]
        count = store.upsert_candles(candles)
        assert count == 10

        queried = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(queried) == 10

        class BuySellStrategy(Strategy):
            @property
            def name(self):
                return "BuySell"

            def __init__(self):
                self.bar = 0

            def on_candle(self, candle, history, ctx=None):
                self.bar += 1
                if ctx is None:
                    return Signal.HOLD
                if self.bar == 1:
                    ctx.market_order(candle.market, Side.LONG, 1.0)
                elif self.bar == 8:
                    ctx.market_order(candle.market, Side.SHORT, 1.0)
                return Signal.HOLD

            def reset(self):
                self.bar = 0

        strategy = BuySellStrategy()
        engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
        result = engine.run(queried)

        assert result is not None
        # One round-trip = one closed position (long opened bar 1, closed bar 8)
        assert result.total_trades == 1
        assert result.total_pnl > 0  # price went up from bar 1 to bar 8
    finally:
        store.close()
        os.unlink(path)


def test_migration_then_backtest():
    """Drift candles in store → Pyth migration → query_candles_with_fallback returns pyth."""
    from flint.migration import run_pyth_migration

    store, path = _make_store()
    try:
        # Seed with Drift candles
        drift_candles = [
            Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
                   1000.0, "SOL-PERP", 3600, "drift")
            for i in range(5)
        ]
        store.upsert_candles(drift_candles)

        # Pyth migration returns candles via mocked provider
        pyth_candles = [
            Candle(1700000000 + i * 3600, 100.1 + i, 101.1 + i, 99.1 + i, 100.6 + i,
                   1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(5)
        ]
        with patch("flint.migration.PythCandleProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.fetch_candles.return_value = pyth_candles
            instance.close = MagicMock()
            migration_result = run_pyth_migration(store)

        assert "SOL-PERP" in migration_result["markets_migrated"]

        # Fallback query should now prefer Pyth candles.
        # end_ts uses inclusive ts of last candle (4 intervals = ts + 4*3600)
        candles = store.query_candles_with_fallback(
            "SOL-PERP", 3600, 1700000000, 1700000000 + 4 * 3600
        )
        assert len(candles) == 5
        assert all(c.venue == "pyth" for c in candles)

        # Run a backtest on the migrated data
        strategy = _BuyOnFirstBar()
        engine = BacktestEngine(strategy, initial_capital=10_000.0, fee_rate=0.0006)
        result = engine.run(candles)

        assert result is not None
        assert result.total_trades >= 1
    finally:
        store.close()
        os.unlink(path)


def test_store_upsert_idempotent_pyth_candles():
    """Upserting the same Pyth candles twice should not double-count rows."""
    store, path = _make_store()
    try:
        candles = [
            Candle(1700000000 + i * 3600, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
                   1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(5)
        ]
        store.upsert_candles(candles)
        store.upsert_candles(candles)  # second upsert — must be idempotent

        queried = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(queried) == 5
    finally:
        store.close()
        os.unlink(path)


def test_query_candles_venue_filter_isolates_pyth():
    """venue='pyth' filter must not return drift candles at the same timestamps."""
    store, path = _make_store()
    try:
        drift_candles = [
            Candle(1700000000 + i * 3600, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift")
            for i in range(3)
        ]
        pyth_candles = [
            Candle(1700000000 + i * 3600, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth")
            for i in range(3)
        ]
        store.upsert_candles(drift_candles)
        store.upsert_candles(pyth_candles)

        pyth_result = store.query_candles("SOL-PERP", 3600, venue="pyth")
        drift_result = store.query_candles("SOL-PERP", 3600, venue="drift")

        assert len(pyth_result) == 3
        assert all(c.venue == "pyth" for c in pyth_result)
        assert len(drift_result) == 3
        assert all(c.venue == "drift" for c in drift_result)
    finally:
        store.close()
        os.unlink(path)
