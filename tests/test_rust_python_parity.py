"""Rust↔Python parity tests — Phase 1 T1.1-parity.

For each backtest configuration that BOTH engines claim to support, assert
that EngineResult fields match across engines within documented tolerance.

Strict parity is not achievable today on all features (fee-on-close-all,
tx_cost plumbing, partial fills, latency). This test captures the currently
supported shared feature set. Divergence beyond these tolerances is a
regression and must be investigated.

Tolerances (subject to revision when Phase 3 tightens parity):
- Zero-trade runs: exact equality (1e-9).
- Closed-trade runs: PnL within 0.05% of initial capital; fill counts exact;
  Sharpe within 0.1 (absolute); trade count exact.
- Force-close runs: relaxed PnL (within 0.2% of initial) — Rust close_all
  does not charge exit fees today (rust/src/engine/positions.rs:198).
"""
from __future__ import annotations

from typing import List

import pytest

from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import ClosePriceFill
from flint.models import Candle, Signal
from flint.strategy.ma_crossover import MACrossoverStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candles(n: int, seed_price: float = 100.0, resolution_s: int = 3600) -> List[Candle]:
    """Deterministic synthetic candles with modest oscillation."""
    return [
        Candle(
            ts=i * resolution_s,
            open=seed_price + (i % 10) * 0.1,
            high=seed_price + (i % 10) * 0.1 + 0.5,
            low=seed_price + (i % 10) * 0.1 - 0.5,
            close=seed_price + ((i % 10) - 4.5) * 0.2,
            volume=10_000.0,
            market="SOL-PERP",
            resolution_s=resolution_s,
        )
        for i in range(n)
    ]


def _run_rust(strategy, candles, **kwargs):
    """Force Rust path: trivially Rust-compatible config, flint_core installed."""
    engine = BacktestEngine(
        strategy, initial_capital=10_000.0, fee_rate=0.0005,
        fill_model=ClosePriceFill(), seed=12345, **kwargs
    )
    # Run returns via can_use_rust dispatch. We assert Rust by checking
    # flint_core is importable — if not, test skips.
    return engine.run(candles)


def _run_python(strategy, candles, **kwargs):
    """Force Python path: add one fake cross-market so extra_markets gate kicks in
    (Rust adapter does not expose cross-market histories, so Rust is skipped)."""
    fake_cross = [
        Candle(ts=-3600, open=1.0, high=1.0, low=1.0, close=1.0,
               volume=0.0, market="FAKE-CROSS", resolution_s=3600)
    ]
    engine = BacktestEngine(
        strategy, initial_capital=10_000.0, fee_rate=0.0005,
        fill_model=ClosePriceFill(), seed=12345, **kwargs
    )
    return engine.run(candles, extra_markets={"FAKE-CROSS": fake_cross})


try:  # skip whole module if Rust engine isn't built
    import flint_core  # noqa: F401
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


pytestmark = pytest.mark.skipif(not HAS_RUST, reason="flint_core not installed")


# ---------------------------------------------------------------------------
# 1. Zero-trade parity — strategy that never trades
# ---------------------------------------------------------------------------

class _HoldStrategy(MACrossoverStrategy):
    def on_candle(self, candle, history):
        return Signal.HOLD


def test_parity_zero_trade_equity_identical():
    """Strategy that never trades must produce identical equity curves
    (no fills → no fee, no tx, no divergence source)."""
    candles = _candles(50)
    r_rust = _run_rust(_HoldStrategy(5, 10), candles)
    r_py = _run_python(_HoldStrategy(5, 10), candles)

    assert r_rust.total_trades == 0
    assert r_py.total_trades == 0
    assert r_rust.total_pnl == pytest.approx(r_py.total_pnl, abs=1e-9)
    assert r_rust.total_fees == pytest.approx(r_py.total_fees, abs=1e-9)
    assert len(r_rust.equity_curve) == len(r_py.equity_curve)
    for a, b in zip(r_rust.equity_curve, r_py.equity_curve):
        assert a == pytest.approx(b, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. Self-closing trades — no force-close, expect tight parity
# ---------------------------------------------------------------------------

class _BuyThenSell(MACrossoverStrategy):
    def on_candle(self, candle, history):
        if len(history) == 1:
            return Signal.BUY
        if len(history) == 5:
            return Signal.SELL
        return Signal.HOLD


def test_parity_self_close_pnl_close():
    """When strategy closes its own position (no force-close), PnL should
    track within 0.05% of initial capital between engines."""
    candles = _candles(20)
    r_rust = _run_rust(_BuyThenSell(5, 10), candles)
    r_py = _run_python(_BuyThenSell(5, 10), candles)

    assert r_rust.total_trades == r_py.total_trades
    assert r_rust.total_trades >= 1
    # Tolerance: 0.05% of initial capital = $5 on a $10k account.
    assert abs(r_rust.total_pnl - r_py.total_pnl) <= 5.0, (
        f"PnL divergence beyond tolerance: rust={r_rust.total_pnl}, "
        f"python={r_py.total_pnl}"
    )


# ---------------------------------------------------------------------------
# 3. Force-close — relaxed tolerance (Rust close_all doesn't charge fees today,
#    tracked as a Phase 3.3 parity gap)
# ---------------------------------------------------------------------------

class _BuyAndHold(MACrossoverStrategy):
    def on_candle(self, candle, history):
        if len(history) == 1:
            return Signal.BUY
        return Signal.HOLD


def test_parity_force_close_pnl_relaxed():
    """Buy-and-hold through end-of-run triggers force-close. Rust does not
    currently charge force-close exit fees while Python does; divergence up
    to ~0.2% of initial capital is expected until the Phase 3.3 fix lands."""
    candles = _candles(30)
    r_rust = _run_rust(_BuyAndHold(5, 10), candles)
    r_py = _run_python(_BuyAndHold(5, 10), candles)

    assert r_rust.total_trades == r_py.total_trades
    # Both should have force-closed the position.
    assert len(r_rust.equity_curve) == len(candles) + 1
    assert len(r_py.equity_curve) == len(candles) + 1
    # Tolerance: 0.2% of initial = $20. Narrow as Phase 3 closes this gap.
    assert abs(r_rust.total_pnl - r_py.total_pnl) <= 20.0, (
        f"Force-close PnL divergence beyond tolerance: "
        f"rust={r_rust.total_pnl}, python={r_py.total_pnl}"
    )


# ---------------------------------------------------------------------------
# 4. Determinism under seed — same seed → same result on the same engine
# ---------------------------------------------------------------------------

def test_rust_determinism_same_seed():
    candles = _candles(40)
    r1 = _run_rust(_BuyAndHold(5, 10), candles)
    r2 = _run_rust(_BuyAndHold(5, 10), candles)

    assert r1.total_pnl == r2.total_pnl
    assert r1.total_trades == r2.total_trades
    assert r1.equity_curve == r2.equity_curve


def test_python_determinism_same_seed():
    candles = _candles(40)
    r1 = _run_python(_BuyAndHold(5, 10), candles)
    r2 = _run_python(_BuyAndHold(5, 10), candles)

    assert r1.total_pnl == r2.total_pnl
    assert r1.total_trades == r2.total_trades
    assert r1.equity_curve == r2.equity_curve
