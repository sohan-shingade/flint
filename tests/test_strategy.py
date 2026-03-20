"""Tests for MA crossover strategy."""
import pytest

from flint.models import Candle, Signal
from flint.strategy.ma_crossover import MACrossoverStrategy


def _make_candle(price: float, ts: int = 0) -> Candle:
    return Candle(
        ts=ts, open=price, high=price, low=price,
        close=price, volume=1.0, market="TEST", resolution_s=60,
    )


def test_invalid_periods():
    with pytest.raises(ValueError):
        MACrossoverStrategy(fast_period=30, slow_period=10)
    with pytest.raises(ValueError):
        MACrossoverStrategy(fast_period=10, slow_period=10)


def test_hold_until_enough_history():
    s = MACrossoverStrategy(fast_period=3, slow_period=5)
    history: list[Candle] = []
    for i in range(4):
        c = _make_candle(100.0, ts=i)
        history.append(c)
        assert s.on_candle(c, history) == Signal.HOLD


def test_buy_signal_on_crossover():
    s = MACrossoverStrategy(fast_period=3, slow_period=5)
    history: list[Candle] = []

    # first 5 candles: declining (fast < slow)
    prices_down = [110, 108, 106, 104, 102]
    for i, p in enumerate(prices_down):
        c = _make_candle(p, ts=i)
        history.append(c)
        s.on_candle(c, history)

    # now add rising prices so fast crosses above slow
    prices_up = [104, 108, 112, 116, 120]
    signals = []
    for i, p in enumerate(prices_up):
        c = _make_candle(p, ts=100 + i)
        history.append(c)
        signals.append(s.on_candle(c, history))

    assert Signal.BUY in signals


def test_sell_signal_on_crossover():
    s = MACrossoverStrategy(fast_period=3, slow_period=5)
    history: list[Candle] = []

    # rising prices first
    prices_up = [90, 95, 100, 105, 110]
    for i, p in enumerate(prices_up):
        c = _make_candle(p, ts=i)
        history.append(c)
        s.on_candle(c, history)

    # then sharp decline
    prices_down = [105, 95, 85, 75, 65]
    signals = []
    for i, p in enumerate(prices_down):
        c = _make_candle(p, ts=100 + i)
        history.append(c)
        signals.append(s.on_candle(c, history))

    assert Signal.SELL in signals


def test_reset_clears_state():
    s = MACrossoverStrategy(fast_period=3, slow_period=5)
    history: list[Candle] = []
    for i in range(10):
        c = _make_candle(100.0 + i, ts=i)
        history.append(c)
        s.on_candle(c, history)

    s.reset()
    assert s._prev_fast == 0.0
    assert s._prev_slow == 0.0


def test_name():
    s = MACrossoverStrategy(fast_period=10, slow_period=30)
    assert s.name == "MA-Crossover(10/30)"
