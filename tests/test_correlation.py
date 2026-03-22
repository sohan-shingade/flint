"""Tests for flint.analytics.correlation module."""
from __future__ import annotations

import math

from flint.analytics.correlation import (
    _pearson,
    _returns,
    compute_correlation_matrix,
    compute_rolling_correlation,
)
from flint.models import Candle


def _make_candle(ts: int, close: float, market: str = "TEST") -> Candle:
    """Helper to build a minimal Candle with the given close price."""
    return Candle(
        ts=ts, open=close, high=close, low=close,
        close=close, volume=1.0, market=market, resolution_s=3600,
    )


# ── _returns ────────────────────────────────────────────────────────

def test_returns_basic():
    candles = [_make_candle(i * 3600, p) for i, p in enumerate([100, 110, 121])]
    rets = _returns(candles)
    assert len(rets) == 2
    assert abs(rets[0] - math.log(110 / 100)) < 1e-12
    assert abs(rets[1] - math.log(121 / 110)) < 1e-12


def test_returns_single_candle():
    candles = [_make_candle(0, 100)]
    assert _returns(candles) == []


# ── _pearson ────────────────────────────────────────────────────────

def test_pearson_perfect_positive():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert abs(_pearson(x, y) - 1.0) < 1e-12


def test_pearson_perfect_negative():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 8.0, 6.0, 4.0, 2.0]
    assert abs(_pearson(x, y) - (-1.0)) < 1e-12


def test_pearson_zero_variance():
    x = [5.0, 5.0, 5.0]
    y = [1.0, 2.0, 3.0]
    assert _pearson(x, y) == 0.0


def test_pearson_short_input():
    assert _pearson([1.0], [2.0]) == 0.0
    assert _pearson([], []) == 0.0


# ── compute_correlation_matrix ──────────────────────────────────────

def test_correlation_matrix_perfectly_correlated():
    """Two markets with identical price movement should have corr = 1.0."""
    prices = [100, 102, 101, 105, 108, 110]
    candles_a = [_make_candle(i * 3600, p, "A") for i, p in enumerate(prices)]
    candles_b = [_make_candle(i * 3600, p, "B") for i, p in enumerate(prices)]

    matrix = compute_correlation_matrix({"A": candles_a, "B": candles_b})

    assert abs(matrix["A"]["A"] - 1.0) < 1e-12
    assert abs(matrix["B"]["B"] - 1.0) < 1e-12
    assert abs(matrix["A"]["B"] - 1.0) < 1e-12
    assert abs(matrix["B"]["A"] - 1.0) < 1e-12


def test_correlation_matrix_inverse_correlated():
    """Market A goes up while B mirrors in log-return space — corr = -1."""
    # Build prices such that B's log returns are the exact negation of A's.
    # If A goes from 100 -> 110 (ratio 1.1), B goes from 100 -> 100/1.1.
    import math as _math

    prices_a = [100.0]
    prices_b = [100.0]
    ratios = [1.10, 0.95, 1.05, 1.08]
    for r in ratios:
        prices_a.append(prices_a[-1] * r)
        prices_b.append(prices_b[-1] / r)  # exact inverse log return

    candles_a = [_make_candle(i * 3600, p, "A") for i, p in enumerate(prices_a)]
    candles_b = [_make_candle(i * 3600, p, "B") for i, p in enumerate(prices_b)]

    matrix = compute_correlation_matrix({"A": candles_a, "B": candles_b})

    assert abs(matrix["A"]["B"] - (-1.0)) < 1e-9, (
        f"Expected corr = -1.0, got {matrix['A']['B']}"
    )
    # Symmetry check
    assert abs(matrix["A"]["B"] - matrix["B"]["A"]) < 1e-12


def test_correlation_matrix_single_market():
    """Single market should produce a 1x1 matrix with corr = 1.0."""
    candles = [_make_candle(i * 3600, 100 + i, "X") for i in range(10)]
    matrix = compute_correlation_matrix({"X": candles})
    assert matrix == {"X": {"X": 1.0}}


# ── compute_rolling_correlation ─────────────────────────────────────

def test_rolling_correlation_basic():
    """Rolling corr of identical series should be 1.0 at every window."""
    n = 30
    prices = [100 + i * 0.5 for i in range(n)]
    candles_a = [_make_candle(i * 3600, p, "A") for i, p in enumerate(prices)]
    candles_b = [_make_candle(i * 3600, p, "B") for i, p in enumerate(prices)]

    result = compute_rolling_correlation(candles_a, candles_b, window=10)

    assert len(result) > 0
    for ts, corr in result:
        assert abs(corr - 1.0) < 1e-9, f"Expected 1.0 at ts={ts}, got {corr}"


def test_rolling_correlation_window_larger_than_data():
    """When the window is larger than available returns, result is empty."""
    candles_a = [_make_candle(i * 3600, 100 + i, "A") for i in range(5)]
    candles_b = [_make_candle(i * 3600, 100 + i, "B") for i in range(5)]

    result = compute_rolling_correlation(candles_a, candles_b, window=100)
    assert result == []


def test_rolling_correlation_has_timestamps():
    """Each entry in the result should carry a valid timestamp."""
    n = 25
    candles_a = [_make_candle(i * 3600, 100 + i, "A") for i in range(n)]
    candles_b = [_make_candle(i * 3600, 200 - i, "B") for i in range(n)]

    result = compute_rolling_correlation(candles_a, candles_b, window=5)
    assert len(result) > 0
    # Timestamps should be ascending
    timestamps = [ts for ts, _ in result]
    assert timestamps == sorted(timestamps)
    # All timestamps should come from candles_a
    valid_ts = {c.ts for c in candles_a}
    for ts in timestamps:
        assert ts in valid_ts
