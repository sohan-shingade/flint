"""Integration test: Jupiter Perps borrow accrual in backtests."""
from __future__ import annotations

from typing import List, Optional

import pytest

from flint.backtest.engine import BacktestEngine
from flint.models import BorrowSnapshot, Candle, Side, Signal
from flint.strategy.base import Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(ts: int, price: float, market: str = "SOL-PERP", venue: str = "default") -> Candle:
    return Candle(
        ts=ts,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1000.0,
        market=market,
        resolution_s=3600,
        venue=venue,
    )


def _borrow_snapshot(ts: int, cumulative_rate: float, market: str = "SOL-PERP") -> BorrowSnapshot:
    return BorrowSnapshot(
        market=market,
        ts=ts,
        rate_hourly=0.0001,
        utilization=0.7,
        cumulative_rate=cumulative_rate,
        source="rpc",
    )


# ---------------------------------------------------------------------------
# Minimal strategies for testing
# ---------------------------------------------------------------------------

class BuyOnBar1SellOnBar3(Strategy):
    """v2 strategy: buys on bar index 1, sells on bar index 3 using context."""

    def __init__(self, venue: str = "default"):
        self._venue = venue
        self._bar = 0

    @property
    def name(self) -> str:
        return "BuyOnBar1SellOnBar3"

    def reset(self) -> None:
        self._bar = 0

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        self._bar += 1
        if ctx is None:
            return Signal.HOLD

        if self._bar == 1:
            # Buy with full position on first bar — explicitly set venue
            ctx.market_order(candle.market, Side.LONG, size=1.0, venue=self._venue)
        elif self._bar == 3:
            ctx.close_position(candle.market, venue=self._venue)

        return Signal.HOLD


class BorrowRateCapture(Strategy):
    """v2 strategy: captures get_borrow_rate() on bar 2 and stores it."""

    def __init__(self):
        self.captured_rate: Optional[float] = None
        self._bar = 0

    @property
    def name(self) -> str:
        return "BorrowRateCapture"

    def reset(self) -> None:
        self._bar = 0
        self.captured_rate = None

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        self._bar += 1
        if ctx is None:
            return Signal.HOLD

        if self._bar == 2:
            # By bar 2 some borrow snapshots have been fed to the context
            self.captured_rate = ctx.get_borrow_rate(candle.market)

        return Signal.HOLD


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

BASE_TS = 1_700_000_000  # arbitrary unix timestamp
HOUR = 3600


def _sol_candles(n: int = 5, price: float = 100.0) -> List[Candle]:
    return [_candle(BASE_TS + i * HOUR, price, market="SOL-PERP") for i in range(n)]


def _borrow_snapshots_increasing() -> List[BorrowSnapshot]:
    """Cumulative rate increases over time — simulates accruing borrow cost."""
    return [
        _borrow_snapshot(BASE_TS + 0 * HOUR, cumulative_rate=0.010),
        _borrow_snapshot(BASE_TS + 1 * HOUR, cumulative_rate=0.011),
        _borrow_snapshot(BASE_TS + 2 * HOUR, cumulative_rate=0.012),
        _borrow_snapshot(BASE_TS + 3 * HOUR, cumulative_rate=0.013),
        _borrow_snapshot(BASE_TS + 4 * HOUR, cumulative_rate=0.014),
    ]


# ---------------------------------------------------------------------------
# Test 1: Jupiter position accrues borrow cost
# ---------------------------------------------------------------------------

def test_jupiter_position_accrues_borrow_cost():
    """A position opened on venue='jupiter' should accrue borrow costs.

    Engine timing (bars are 0-indexed in on_candle call order):
    - Bar 0 ts=BASE_TS:       borrow snapshot cumulative_rate=0.010 fed first
    - Bar 0 (self._bar==1):   strategy places market_order(venue='jupiter')
                               entry cumulative snapped = 0.010
    - Bar 2 ts=BASE_TS+2h:    borrow snapshot cumulative_rate=0.012 fed first
    - Bar 2 (self._bar==3):   strategy closes position
                               exit cumulative = 0.012
    - borrow_cost = (0.012 - 0.010) * (1 SOL * $100) = 0.002 * 100 = $0.20
    """
    candles = _sol_candles()
    borrow_snaps = _borrow_snapshots_increasing()

    strategy = BuyOnBar1SellOnBar3(venue="jupiter")
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=10_000.0,
        fee_rate=0.0,
        borrow_snapshots=borrow_snaps,
    )

    # Use dict input so the engine assigns venue tags from the key
    result = engine.run({"jupiter:SOL-PERP": candles})

    assert result.jupiter_borrow_paid > 0, (
        f"Expected jupiter_borrow_paid > 0, got {result.jupiter_borrow_paid}"
    )
    # borrow_cost = (cum_exit - cum_entry) * notional_usd
    # cum_entry=0.010, cum_exit=0.012, notional = 1 SOL * $100 = $100
    expected_approx = (0.012 - 0.010) * 100.0  # ≈ 0.20
    assert result.jupiter_borrow_paid == pytest.approx(expected_approx, rel=0.05), (
        f"Expected borrow paid ~{expected_approx}, got {result.jupiter_borrow_paid}"
    )
    # Confirm there is at least one borrow payment recorded
    assert len(result.borrow_payments) >= 1


# ---------------------------------------------------------------------------
# Test 2: Drift position does NOT accrue borrow cost
# ---------------------------------------------------------------------------

def test_drift_position_no_borrow_cost():
    """A position on venue='drift' should have jupiter_borrow_paid == 0.

    Even if borrow snapshots are present, drift positions skip borrow accrual.
    """
    candles = _sol_candles()
    borrow_snaps = _borrow_snapshots_increasing()

    strategy = BuyOnBar1SellOnBar3(venue="drift")
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=10_000.0,
        fee_rate=0.0,
        borrow_snapshots=borrow_snaps,
    )

    result = engine.run({"drift:SOL-PERP": candles})

    assert result.jupiter_borrow_paid == 0.0, (
        f"Expected jupiter_borrow_paid == 0 for drift venue, got {result.jupiter_borrow_paid}"
    )
    assert result.borrow_payments == [], (
        f"Expected no borrow payments for drift venue, got {result.borrow_payments}"
    )


# ---------------------------------------------------------------------------
# Test 3: get_borrow_rate() is accessible inside a strategy
# ---------------------------------------------------------------------------

def test_get_borrow_rate_accessible_in_strategy():
    """ctx.get_borrow_rate() should return a non-None positive value inside a strategy
    once borrow snapshots have been fed for the current market.
    """
    candles = _sol_candles(n=5)
    borrow_snaps = _borrow_snapshots_increasing()

    strategy = BorrowRateCapture()
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=10_000.0,
        fee_rate=0.0,
        borrow_snapshots=borrow_snaps,
    )

    engine.run(candles)

    assert strategy.captured_rate is not None, (
        "ctx.get_borrow_rate() returned None — borrow snapshots were not fed to context"
    )
    assert strategy.captured_rate > 0, (
        f"Expected a positive borrow rate, got {strategy.captured_rate}"
    )
    # Should match the rate_hourly we set in the snapshots (0.0001)
    assert strategy.captured_rate == pytest.approx(0.0001, rel=1e-6)


# ---------------------------------------------------------------------------
# Test 4: No borrow snapshots → jupiter_borrow_paid stays zero
# ---------------------------------------------------------------------------

def test_no_borrow_snapshots_zero_borrow_paid():
    """Without borrow_snapshots provided, borrow cost should be zero even for jupiter."""
    candles = _sol_candles()

    strategy = BuyOnBar1SellOnBar3(venue="jupiter")
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=10_000.0,
        fee_rate=0.0,
        borrow_snapshots=[],  # empty — no borrow data
    )

    result = engine.run({"jupiter:SOL-PERP": candles})

    assert result.jupiter_borrow_paid == 0.0, (
        f"Expected 0 borrow paid with no snapshots, got {result.jupiter_borrow_paid}"
    )


# ---------------------------------------------------------------------------
# Test 5: Borrow paid reduces account equity
# ---------------------------------------------------------------------------

def test_borrow_cost_reduces_equity():
    """Borrow cost should be debited from cash, reducing final equity.

    Forces both engines to `ClosePriceFill` so the slippage profile is
    identical between the borrow / no-borrow runs — otherwise the
    `borrow_snapshots` kwarg routes the Python fallback (SlippageFill
    by default) while the no-borrow run uses the Rust engine, and the
    fill-price delta swamps the borrow cost we're trying to measure.
    """
    from flint.execution.fill_models import ClosePriceFill

    candles = _sol_candles()
    borrow_snaps = _borrow_snapshots_increasing()

    initial_capital = 10_000.0

    # Run with Jupiter borrow
    strategy_jup = BuyOnBar1SellOnBar3(venue="jupiter")
    engine_jup = BacktestEngine(
        strategy=strategy_jup,
        initial_capital=initial_capital,
        fee_rate=0.0,
        fill_model=ClosePriceFill(),
        borrow_snapshots=borrow_snaps,
    )
    result_jup = engine_jup.run({"jupiter:SOL-PERP": candles})

    # Run without borrow (no snapshots)
    strategy_no_borrow = BuyOnBar1SellOnBar3(venue="jupiter")
    engine_no_borrow = BacktestEngine(
        strategy=strategy_no_borrow,
        initial_capital=initial_capital,
        fee_rate=0.0,
        fill_model=ClosePriceFill(),
        borrow_snapshots=[],
    )
    result_no_borrow = engine_no_borrow.run({"jupiter:SOL-PERP": candles})

    # With borrow cost, final equity should be lower
    assert result_jup.total_pnl < result_no_borrow.total_pnl, (
        "Expected total_pnl to be reduced by borrow cost; "
        f"jup={result_jup.total_pnl}, no_borrow={result_no_borrow.total_pnl}"
    )
    # The difference should equal the borrow paid (slippage cancels
    # because both runs share `ClosePriceFill`).
    pnl_diff = result_no_borrow.total_pnl - result_jup.total_pnl
    assert pnl_diff == pytest.approx(result_jup.jupiter_borrow_paid, rel=0.01)
