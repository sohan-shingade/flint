"""Tests for MomentumBreakoutStrategy."""
from unittest.mock import MagicMock

from flint.models import Candle, Signal
from flint.strategy.momentum_breakout import MomentumBreakoutStrategy


def _candle(close, high=None, low=None, ts=1000, market="SOL-PERP"):
    high = high if high is not None else close + 1.0
    low = low if low is not None else close - 1.0
    return Candle(
        ts=ts, open=close, high=high, low=low, close=close,
        volume=100.0, market=market, resolution_s=3600,
    )


def _history(n=20, base_close=100.0):
    """Build a flat history of n candles."""
    return [_candle(base_close, ts=i * 3600) for i in range(n)]


class TestName:
    def test_name(self):
        s = MomentumBreakoutStrategy()
        assert s.name == "momentum_breakout"


class TestParameters:
    def test_parameters(self):
        params = MomentumBreakoutStrategy.parameters()
        assert "breakout_lookback" in params
        assert "trailing_stop_pct" in params
        assert "oracle_confirmation" in params
        assert "candle_resolution_s" in params

    def test_parameter_types(self):
        params = MomentumBreakoutStrategy.parameters()
        assert params["breakout_lookback"]["type"] == "int"
        assert params["trailing_stop_pct"]["type"] == "float"
        assert params["oracle_confirmation"]["type"] == "int"
        assert params["candle_resolution_s"]["type"] == "int"

    def test_parameter_defaults(self):
        params = MomentumBreakoutStrategy.parameters()
        assert params["breakout_lookback"]["default"] == 20
        assert params["trailing_stop_pct"]["default"] == 0.02
        assert params["oracle_confirmation"]["default"] == 1
        assert params["candle_resolution_s"]["default"] == 3600


class TestSignals:
    def test_buy_on_breakout(self):
        """Price breaks above N-bar high → BUY."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        # history candles have high=101, low=99, close=100
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        # current candle close=102 > highest_high=101
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)
        signal = s.on_candle(current, history)
        assert signal == Signal.BUY

    def test_sell_on_breakdown(self):
        """Price breaks below N-bar low → SELL."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        # current candle close=98 < lowest_low=99
        current = _candle(98.0, high=99.5, low=97.0, ts=5 * 3600)
        signal = s.on_candle(current, history)
        assert signal == Signal.SELL

    def test_hold_in_range(self):
        """Price within range → HOLD."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        history = [_candle(100.0, high=105.0, low=95.0, ts=i * 3600) for i in range(5)]
        # current close=100 is within [95, 105]
        current = _candle(100.0, high=101.0, low=99.0, ts=5 * 3600)
        signal = s.on_candle(current, history)
        assert signal == Signal.HOLD

    def test_no_signal_insufficient_history(self):
        """Fewer history candles than lookback → HOLD."""
        s = MomentumBreakoutStrategy(breakout_lookback=20, oracle_confirmation=0)
        history = [_candle(100.0, ts=i * 3600) for i in range(10)]
        current = _candle(200.0, ts=10 * 3600)  # extreme breakout
        signal = s.on_candle(current, history)
        assert signal == Signal.HOLD

    def test_oracle_blocks_buy_entry(self):
        """Oracle price below current close on BUY breakout → HOLD."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)

        ctx = MagicMock()
        # Oracle is below current close → disagrees with BUY
        ctx.get_oracle_price.return_value = 99.0

        signal = s.on_candle(current, history, ctx=ctx)
        assert signal == Signal.HOLD
        ctx.get_oracle_price.assert_called_once_with("SOL-PERP")

    def test_oracle_allows_buy_entry(self):
        """Oracle price above current close on BUY breakout → BUY allowed."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)

        ctx = MagicMock()
        # Oracle is above current close → agrees with BUY
        ctx.get_oracle_price.return_value = 103.0

        signal = s.on_candle(current, history, ctx=ctx)
        assert signal == Signal.BUY

    def test_oracle_blocks_sell_entry(self):
        """Oracle price above current close on SELL breakdown → HOLD."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(98.0, high=99.5, low=97.0, ts=5 * 3600)

        ctx = MagicMock()
        # Oracle is above current close → disagrees with SELL
        ctx.get_oracle_price.return_value = 101.0

        signal = s.on_candle(current, history, ctx=ctx)
        assert signal == Signal.HOLD

    def test_oracle_none_does_not_block(self):
        """If oracle returns None, entry is not blocked."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)

        ctx = MagicMock()
        ctx.get_oracle_price.return_value = None

        signal = s.on_candle(current, history, ctx=ctx)
        assert signal == Signal.BUY

    def test_oracle_confirmation_disabled(self):
        """oracle_confirmation=0 never calls ctx.get_oracle_price."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)

        ctx = MagicMock()
        signal = s.on_candle(current, history, ctx=ctx)
        assert signal == Signal.BUY
        ctx.get_oracle_price.assert_not_called()

    def test_no_ctx_oracle_confirmation_still_signals(self):
        """oracle_confirmation=1 but ctx=None → still returns BUY (no oracle check possible)."""
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        history = [_candle(100.0, high=101.0, low=99.0, ts=i * 3600) for i in range(5)]
        current = _candle(102.0, high=103.0, low=101.0, ts=5 * 3600)
        signal = s.on_candle(current, history, ctx=None)
        assert signal == Signal.BUY


class TestReset:
    def test_reset_runs_without_error(self):
        s = MomentumBreakoutStrategy()
        s.reset()  # should not raise
