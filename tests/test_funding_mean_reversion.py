"""Tests for FundingMeanReversionStrategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from flint.models import AccountState, Candle, Side, Signal
from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy


def _candle(close=150.0, ts=10000, market="SOL-PERP"):
    return Candle(
        ts=ts, open=close, high=close + 1.0, low=close - 1.0,
        close=close, volume=100.0, market=market, resolution_s=3600,
    )


def _make_ctx(funding_rates=None, equity=10000.0):
    ctx = MagicMock()
    ctx.get_funding_rates.return_value = funding_rates or []
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    account = MagicMock()
    account.equity = equity
    type(ctx).account = PropertyMock(return_value=account)
    return ctx


def _flat_rates(n, rate=0.0001, ts_start=0):
    """Build a list of (ts, rate) tuples with flat rate."""
    return [(ts_start + i * 3600, rate) for i in range(n)]


def _rates_with_spike(n, base=0.0001, spike=-0.01, ts_start=0):
    """Build rates with the last value spiked."""
    rates = [(ts_start + i * 3600, base) for i in range(n - 1)]
    rates.append((ts_start + (n - 1) * 3600, spike))
    return rates


class TestName:
    def test_name(self):
        s = FundingMeanReversionStrategy()
        assert s.name == "funding_mean_reversion"


class TestParameters:
    def test_parameters(self):
        params = FundingMeanReversionStrategy.parameters()
        assert "bb_lookback" in params
        assert "bb_std" in params
        assert "max_hold_hours" in params
        assert "position_size_pct" in params
        assert "candle_resolution_s" in params

    def test_parameter_types(self):
        params = FundingMeanReversionStrategy.parameters()
        assert params["bb_lookback"]["type"] == "int"
        assert params["bb_std"]["type"] == "float"
        assert params["max_hold_hours"]["type"] == "int"

    def test_parameter_defaults(self):
        params = FundingMeanReversionStrategy.parameters()
        assert params["bb_lookback"]["default"] == 24
        assert params["bb_std"]["default"] == 2.0
        assert params["max_hold_hours"]["default"] == 12


class TestReset:
    def test_reset(self):
        s = FundingMeanReversionStrategy()
        s._entry_ts = 99999
        s._entry_side = Side.LONG
        s.reset()
        assert s._entry_ts == 0
        assert s._entry_side is None


class TestEntryLogic:
    def test_entry_when_rate_below_band(self):
        """Funding rate drops far below lower band → market_order called (LONG)."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0)
        candle = _candle(close=150.0, ts=9 * 3600)
        # 9 flat values at 0.0001, then a spike to -0.01 (way below lower band)
        rates = _rates_with_spike(10, base=0.0001, spike=-0.01)
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_called_once()
        call_args = ctx.market_order.call_args
        assert call_args[0][1] == Side.LONG  # going long when rate is very negative

    def test_entry_when_rate_above_band(self):
        """Funding rate spikes far above upper band → market_order called (SHORT)."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0)
        candle = _candle(close=150.0, ts=9 * 3600)
        # spike high
        rates = _rates_with_spike(10, base=0.0001, spike=0.05)
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_called_once()
        call_args = ctx.market_order.call_args
        assert call_args[0][1] == Side.SHORT  # going short when rate is very positive

    def test_no_entry_rate_in_bands(self):
        """Flat rate within bands → no order placed."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0)
        candle = _candle(close=150.0, ts=9 * 3600)
        rates = _flat_rates(10, rate=0.0001)
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_no_entry_insufficient_data(self):
        """Fewer rates than lookback → no order, return HOLD."""
        s = FundingMeanReversionStrategy(bb_lookback=24, bb_std=2.0)
        candle = _candle(close=150.0, ts=5 * 3600)
        rates = _flat_rates(5, rate=-0.01)  # only 5 instead of 24
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_no_entry_without_ctx(self):
        """ctx=None → always HOLD."""
        s = FundingMeanReversionStrategy(bb_lookback=10)
        candle = _candle(close=150.0, ts=9 * 3600)
        signal = s.on_candle(candle, [candle], ctx=None)
        assert signal == Signal.HOLD

    def test_entry_sets_state(self):
        """Successful entry sets _entry_ts and _entry_side."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0)
        candle = _candle(close=150.0, ts=9 * 3600)
        rates = _rates_with_spike(10, base=0.0001, spike=-0.01)
        ctx = _make_ctx(funding_rates=rates)

        s.on_candle(candle, [candle], ctx=ctx)
        assert s._entry_ts == 9 * 3600
        assert s._entry_side == Side.LONG


class TestExitLogic:
    def test_exit_on_max_hold(self):
        """Position held >= max_hold_hours → close_position called."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0, max_hold_hours=4)
        s._entry_ts = 1 * 3600  # entered 1 hour into epoch
        s._entry_side = Side.LONG

        # current candle is 5 hours after entry (entry at 3600, candle at 6*3600 = 21600)
        candle = _candle(close=150.0, ts=6 * 3600)
        rates = _flat_rates(10, rate=0.0001)  # rate within bands
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.close_position.assert_called_once_with("SOL-PERP")
        assert s._entry_ts == 0
        assert s._entry_side is None

    def test_exit_when_rate_crosses_middle_long(self):
        """Long position: rate >= middle → exit."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0, max_hold_hours=48)
        s._entry_ts = 1  # non-zero → has_position
        s._entry_side = Side.LONG

        candle = _candle(close=150.0, ts=1 * 3600)  # 1 hour into trade
        # All rates at 0.0001 → middle ≈ 0.0001, current = 0.0001 (at middle → exit)
        rates = _flat_rates(10, rate=0.0001)
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.close_position.assert_called_once()

    def test_exit_when_rate_crosses_middle_short(self):
        """Short position: rate <= middle → exit."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0, max_hold_hours=48)
        s._entry_ts = 1  # non-zero → has_position
        s._entry_side = Side.SHORT

        candle = _candle(close=150.0, ts=1 * 3600)
        rates = _flat_rates(10, rate=0.0001)
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.close_position.assert_called_once()

    def test_no_exit_before_max_hold_with_open_position(self):
        """Position not yet hitting exit conditions → no close_position."""
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0, max_hold_hours=48)
        s._entry_ts = 1 * 3600  # entered 1 hour in
        s._entry_side = Side.LONG

        # Only 2 hours in, rate still well below middle
        candle = _candle(close=150.0, ts=2 * 3600)
        # Rate is still negative (below middle of 0.0001) — shouldn't exit LONG
        rates = _flat_rates(9, rate=0.0001)
        rates.append((9 * 3600, -0.005))  # last rate is well below middle
        ctx = _make_ctx(funding_rates=rates)

        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.close_position.assert_not_called()
