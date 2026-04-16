"""Tests for flint/indicators.py — 23 technical analysis functions."""
from __future__ import annotations

import numpy as np
import pytest

from flint.indicators import (
    adx,
    atr,
    bollinger,
    bollinger_width,
    closes,
    ema,
    highest_high,
    highs,
    lowest_low,
    lows,
    macd,
    roc,
    rsi,
    sma,
    stochastic,
    volatility,
    volume_ratio,
    volumes,
    vwap,
    wma,
    z_score,
)
from flint.models import Candle

# ── Helpers ──────────────────────────────────────────────────────────


def _make_candles(prices: list[float], vols: list[float] | None = None) -> list[Candle]:
    """Build candles from close prices. open=close-0.5, high=close+1, low=close-1."""
    if vols is None:
        vols = [100.0] * len(prices)
    return [
        Candle(
            ts=1_700_000_000 + i * 3600,
            open=p - 0.5,
            high=p + 1.0,
            low=p - 1.0,
            close=p,
            volume=vols[i],
            market="TEST-PERP",
            resolution_s=3600,
        )
        for i, p in enumerate(prices)
    ]


def _flat_candles(n: int, price: float = 100.0) -> list[Candle]:
    """N candles all at the exact same price (for division-by-zero guards)."""
    return _make_candles([price] * n)


def _rising_candles(n: int, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    """Monotonically rising prices."""
    return _make_candles([start + i * step for i in range(n)])


def _falling_candles(n: int, start: float = 200.0, step: float = 1.0) -> list[Candle]:
    """Monotonically falling prices."""
    return _make_candles([start - i * step for i in range(n)])


# ── Helpers: closes / highs / lows / volumes ─────────────────────────


class TestExtractors:
    def test_closes_returns_ndarray(self, sample_candles):
        result = closes(sample_candles)
        assert isinstance(result, np.ndarray)
        assert len(result) == 60

    def test_closes_with_period(self, sample_candles):
        result = closes(sample_candles, period=10)
        assert len(result) == 10
        # Should be the *last* 10 candles
        assert result[-1] == sample_candles[-1].close

    def test_highs_values(self):
        candles = _make_candles([10.0, 20.0, 30.0])
        result = highs(candles)
        np.testing.assert_array_equal(result, [11.0, 21.0, 31.0])

    def test_lows_values(self):
        candles = _make_candles([10.0, 20.0, 30.0])
        result = lows(candles)
        np.testing.assert_array_equal(result, [9.0, 19.0, 29.0])

    def test_volumes_with_period(self):
        candles = _make_candles([1.0, 2.0, 3.0, 4.0], vols=[10, 20, 30, 40])
        result = volumes(candles, period=2)
        np.testing.assert_array_equal(result, [30.0, 40.0])


# ── Moving Averages ──────────────────────────────────────────────────


class TestSMA:
    def test_known_values(self):
        candles = _make_candles([1.0, 2.0, 3.0, 4.0, 5.0])
        assert sma(candles, 3) == pytest.approx(4.0)  # mean([3, 4, 5])

    def test_insufficient_data(self):
        candles = _make_candles([1.0, 2.0])
        assert sma(candles, 5) == 0.0

    def test_sample_candles(self, sample_candles):
        result = sma(sample_candles, 20)
        assert isinstance(result, float)
        assert result > 0

    def test_single_candle_period_1(self):
        candles = _make_candles([42.0])
        assert sma(candles, 1) == pytest.approx(42.0)


class TestEMA:
    def test_insufficient_data(self):
        candles = _make_candles([1.0, 2.0])
        assert ema(candles, 5) == 0.0

    def test_differs_from_sma(self, sample_candles):
        e = ema(sample_candles, 20)
        s = sma(sample_candles, 20)
        assert e != s  # EMA reacts differently than SMA

    def test_ema_weights_recent(self):
        # Use accelerating prices so EMA (which weights recent more) exceeds SMA.
        # A perfectly linear series makes EMA ~= SMA, so use exponential growth.
        prices = [100.0 + i**2 * 0.1 for i in range(50)]
        candles = _make_candles(prices)
        assert ema(candles, 10) > sma(candles, 10)

    def test_returns_float(self, sample_candles):
        assert isinstance(ema(sample_candles, 14), float)


class TestWMA:
    def test_insufficient_data(self):
        assert wma(_make_candles([1.0]), 5) == 0.0

    def test_known_values(self):
        candles = _make_candles([1.0, 2.0, 3.0])
        # weights 1,2,3 -> (1*1 + 2*2 + 3*3) / (1+2+3) = 14/6
        assert wma(candles, 3) == pytest.approx(14.0 / 6.0)

    def test_weights_recent_more(self):
        candles = _rising_candles(20)
        assert wma(candles, 10) > sma(candles, 10)


# ── Oscillators ──────────────────────────────────────────────────────


class TestRSI:
    def test_insufficient_data(self):
        candles = _make_candles([1.0, 2.0, 3.0])
        assert rsi(candles, 14) == 50.0

    def test_all_gains(self):
        candles = _rising_candles(20)
        assert rsi(candles, 14) == 100.0

    def test_all_losses_near_zero(self):
        candles = _falling_candles(20)
        result = rsi(candles, 14)
        assert result < 5.0  # Should be near 0

    def test_range_0_to_100(self, sample_candles):
        result = rsi(sample_candles, 14)
        assert 0.0 <= result <= 100.0

    def test_flat_candles(self):
        # No gains, no losses: avg_loss == 0 -> returns 100.0
        candles = _flat_candles(20)
        assert rsi(candles, 14) == 100.0


class TestStochastic:
    def test_insufficient_data(self):
        candles = _make_candles([1.0, 2.0])
        k, d = stochastic(candles, 14)
        assert k == 50.0
        assert d == 50.0

    def test_returns_tuple(self, sample_candles):
        result = stochastic(sample_candles, 14)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_range_0_to_100(self, sample_candles):
        k, d = stochastic(sample_candles, 14)
        assert 0.0 <= k <= 100.0
        assert 0.0 <= d <= 100.0

    def test_flat_candles_returns_50(self):
        candles = _flat_candles(20)
        k, d = stochastic(candles, 14)
        assert k == 50.0


# ── MACD ─────────────────────────────────────────────────────────────


class TestMACD:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 10)
        assert macd(candles) == (0.0, 0.0, 0.0)

    def test_returns_three_floats(self, sample_candles):
        m, s, h = macd(sample_candles)
        assert isinstance(m, float)
        assert isinstance(s, float)
        assert isinstance(h, float)

    def test_histogram_is_difference(self, sample_candles):
        m, s, h = macd(sample_candles)
        assert h == pytest.approx(m - s)

    def test_trending_up(self):
        candles = _rising_candles(40)
        m, _s, _h = macd(candles)
        assert m > 0  # Fast EMA > slow EMA in uptrend


# ── Bands ────────────────────────────────────────────────────────────


class TestBollinger:
    def test_insufficient_data_returns_price(self):
        candles = _make_candles([50.0])
        upper, middle, lower = bollinger(candles, 20)
        assert upper == middle == lower == 50.0

    def test_ordering(self, sample_candles):
        upper, middle, lower = bollinger(sample_candles, 20)
        assert upper > middle > lower

    def test_flat_candles(self):
        candles = _flat_candles(25)
        upper, middle, lower = bollinger(candles, 20)
        assert middle == pytest.approx(100.0)
        # std is 0 with ddof=1 for identical values, so upper == middle == lower
        assert upper == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)

    def test_returns_tuple_of_three(self, sample_candles):
        result = bollinger(sample_candles, 20)
        assert isinstance(result, tuple)
        assert len(result) == 3


class TestBollingerWidth:
    def test_insufficient_data(self):
        candles = _make_candles([50.0])
        assert bollinger_width(candles, 20) == pytest.approx(0.0)

    def test_positive_width(self, sample_candles):
        result = bollinger_width(sample_candles, 20)
        assert result > 0

    def test_zero_middle(self):
        # Price of 0: middle = 0 -> returns 0.0
        candles = _make_candles([0.0] * 25)
        assert bollinger_width(candles, 20) == 0.0


# ── Volatility ───────────────────────────────────────────────────────


class TestATR:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert atr(candles, 14) == 0.0

    def test_positive(self, sample_candles):
        result = atr(sample_candles, 14)
        assert result > 0

    def test_flat_candles_nonzero(self):
        # Even flat close prices have high-low spread of 2.0
        candles = _flat_candles(20)
        result = atr(candles, 14)
        assert result > 0  # TR includes high-low range


class TestVolatility:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert volatility(candles, 20) == 0.0

    def test_positive(self, sample_candles):
        result = volatility(sample_candles, 20)
        assert result > 0

    def test_flat_candles_zero(self):
        candles = _flat_candles(25)
        assert volatility(candles, 20) == 0.0

    def test_returns_float(self, sample_candles):
        assert isinstance(volatility(sample_candles, 20), float)


# ── Volume ───────────────────────────────────────────────────────────


class TestVWAP:
    def test_insufficient_falls_back_to_close(self):
        candles = _make_candles([42.0])
        assert vwap(candles, 20) == 42.0

    def test_empty_list(self):
        assert vwap([], 20) == 0.0

    def test_zero_volume_falls_back_to_sma(self):
        candles = _make_candles([10.0, 20.0, 30.0], vols=[0, 0, 0])
        result = vwap(candles, 3)
        assert result == pytest.approx(sma(candles, 3))

    def test_positive(self, sample_candles):
        result = vwap(sample_candles, 20)
        assert result > 0


class TestVolumeRatio:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert volume_ratio(candles, 20) == 1.0

    def test_above_average(self):
        # Last candle has 500 volume, rest have 100
        vols = [100.0] * 19 + [500.0]
        candles = _make_candles([10.0] * 20, vols=vols)
        assert volume_ratio(candles, 20) > 1.0

    def test_zero_volume(self):
        candles = _make_candles([10.0] * 25, vols=[0.0] * 25)
        assert volume_ratio(candles, 20) == 1.0


# ── Trend ────────────────────────────────────────────────────────────


class TestROC:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert roc(candles, 12) == 0.0

    def test_positive_roc(self):
        candles = _make_candles([100.0] * 12 + [150.0])
        result = roc(candles, 12)
        assert result == pytest.approx(50.0)  # (150-100)/100 * 100

    def test_negative_roc(self):
        candles = _make_candles([200.0] * 12 + [100.0])
        result = roc(candles, 12)
        assert result == pytest.approx(-50.0)

    def test_zero_old_price(self):
        candles = _make_candles([0.0] * 12 + [50.0])
        assert roc(candles, 12) == 0.0


class TestADX:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert adx(candles, 14) == 0.0

    def test_positive(self, sample_candles):
        result = adx(sample_candles, 14)
        assert result >= 0

    def test_flat_candles_zero(self):
        # All same price: no directional movement, di_sum == 0 -> 0.0
        candles = _flat_candles(20)
        assert adx(candles, 14) == 0.0

    def test_returns_float(self, sample_candles):
        assert isinstance(adx(sample_candles, 14), float)


# ── Convenience ──────────────────────────────────────────────────────


class TestZScore:
    def test_insufficient_data(self):
        candles = _make_candles([1.0] * 5)
        assert z_score(candles, 20) == 0.0

    def test_flat_candles_zero(self):
        candles = _flat_candles(25)
        assert z_score(candles, 20) == 0.0

    def test_above_mean_positive(self):
        # Last price well above mean
        prices = [100.0] * 19 + [200.0]
        candles = _make_candles(prices)
        assert z_score(candles, 20) > 0

    def test_below_mean_negative(self):
        prices = [200.0] * 19 + [100.0]
        candles = _make_candles(prices)
        assert z_score(candles, 20) < 0


class TestHighestHigh:
    def test_known_values(self):
        candles = _make_candles([10.0, 30.0, 20.0])
        # highs are [11, 31, 21]; period 2 -> last 2 candles [30, 20] -> highs [31, 21] -> max = 31
        assert highest_high(candles, 2) == pytest.approx(31.0)
        assert highest_high(candles, 3) == pytest.approx(31.0)
        assert highest_high(candles, 1) == pytest.approx(21.0)  # just the last candle

    def test_insufficient_falls_back(self):
        candles = _make_candles([10.0, 20.0])
        # period > len -> falls back to max of all highs
        result = highest_high(candles, 10)
        assert result == pytest.approx(21.0)  # max(11, 21)

    def test_sample_candles(self, sample_candles):
        result = highest_high(sample_candles, 20)
        assert isinstance(result, float)
        assert result > 0


class TestLowestLow:
    def test_known_values(self):
        candles = _make_candles([10.0, 30.0, 20.0])
        # lows are [9, 29, 19]; period 2 -> last 2 -> min(29, 19) = 19
        assert lowest_low(candles, 2) == pytest.approx(19.0)
        assert lowest_low(candles, 3) == pytest.approx(9.0)

    def test_insufficient_falls_back(self):
        candles = _make_candles([10.0, 20.0])
        result = lowest_low(candles, 10)
        assert result == pytest.approx(9.0)  # min(9, 19)

    def test_sample_candles(self, sample_candles):
        result = lowest_low(sample_candles, 20)
        assert isinstance(result, float)
        assert result > 0


# ── Integration: all functions survive sample_candles ────────────────


class TestAllFunctionsWithSampleCandles:
    """Smoke test: every indicator runs without error on the shared fixture."""

    def test_helpers(self, sample_candles):
        assert len(closes(sample_candles)) == 60
        assert len(highs(sample_candles)) == 60
        assert len(lows(sample_candles)) == 60
        assert len(volumes(sample_candles)) == 60

    def test_moving_averages(self, sample_candles):
        assert sma(sample_candles, 20) > 0
        assert ema(sample_candles, 20) > 0
        assert wma(sample_candles, 20) > 0

    def test_oscillators(self, sample_candles):
        assert 0 <= rsi(sample_candles) <= 100
        k, d = stochastic(sample_candles)
        assert 0 <= k <= 100
        assert 0 <= d <= 100

    def test_macd_runs(self, sample_candles):
        m, s, h = macd(sample_candles)
        assert h == pytest.approx(m - s)

    def test_bands(self, sample_candles):
        u, m, l = bollinger(sample_candles)
        assert u >= m >= l
        assert bollinger_width(sample_candles) >= 0

    def test_volatility_indicators(self, sample_candles):
        assert atr(sample_candles) > 0
        assert volatility(sample_candles) > 0

    def test_volume_indicators(self, sample_candles):
        assert vwap(sample_candles) > 0
        assert volume_ratio(sample_candles) > 0

    def test_trend_indicators(self, sample_candles):
        roc(sample_candles)  # just verify no crash
        adx(sample_candles)

    def test_convenience(self, sample_candles):
        z_score(sample_candles)
        assert highest_high(sample_candles, 20) > lowest_low(sample_candles, 20)


# ── Edge case: single candle ────────────────────────────────────────


class TestSingleCandle:
    """Every function should handle a 1-element list gracefully."""

    @pytest.fixture
    def one(self):
        return _make_candles([100.0])

    def test_sma(self, one):
        assert sma(one, 1) == pytest.approx(100.0)
        assert sma(one, 5) == 0.0

    def test_ema(self, one):
        assert ema(one, 1) == pytest.approx(100.0)

    def test_rsi(self, one):
        assert rsi(one) == 50.0

    def test_stochastic(self, one):
        assert stochastic(one, 1) != (None, None)

    def test_macd(self, one):
        assert macd(one) == (0.0, 0.0, 0.0)

    def test_bollinger(self, one):
        u, m, l = bollinger(one)
        assert u == m == l == 100.0

    def test_atr(self, one):
        assert atr(one) == 0.0

    def test_volatility(self, one):
        assert volatility(one) == 0.0

    def test_vwap(self, one):
        assert vwap(one) == 100.0

    def test_volume_ratio(self, one):
        assert volume_ratio(one) == 1.0

    def test_roc(self, one):
        assert roc(one) == 0.0

    def test_adx(self, one):
        assert adx(one) == 0.0

    def test_z_score(self, one):
        assert z_score(one) == 0.0

    def test_highest_high(self, one):
        assert highest_high(one, 5) == pytest.approx(101.0)

    def test_lowest_low(self, one):
        assert lowest_low(one, 5) == pytest.approx(99.0)
