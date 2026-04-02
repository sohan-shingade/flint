"""Tests for MevArbMonitor strategy."""
import pytest
from unittest.mock import MagicMock, call

from flint.models import Candle, Signal
from flint.strategy.mev_arb_monitor import MevArbMonitor


def _candle(ts=1000, market="SOL-PERP"):
    return Candle(
        ts=ts, open=100.0, high=101.0, low=99.0, close=100.0,
        volume=500.0, market=market, resolution_s=60,
    )


def _make_ctx(routes=None):
    ctx = MagicMock()
    ctx.get_arb_routes.return_value = routes or []
    return ctx


class TestName:
    def test_name(self):
        s = MevArbMonitor()
        assert s.name == "mev_arb_monitor"


class TestParameters:
    def test_parameters(self):
        params = MevArbMonitor.parameters()
        assert "min_profit_bps" in params
        assert "max_hops" in params
        assert "alert_enabled" in params
        assert "candle_resolution_s" in params

    def test_parameter_types(self):
        params = MevArbMonitor.parameters()
        assert params["min_profit_bps"]["type"] == "float"
        assert params["max_hops"]["type"] == "int"
        assert params["alert_enabled"]["type"] == "int"

    def test_parameter_defaults(self):
        params = MevArbMonitor.parameters()
        assert params["min_profit_bps"]["default"] == 10.0
        assert params["max_hops"]["default"] == 3
        assert params["alert_enabled"]["default"] == 0


class TestAlwaysReturnsHold:
    def test_always_returns_hold_no_ctx(self):
        s = MevArbMonitor()
        candle = _candle()
        assert s.on_candle(candle, [candle], ctx=None) == Signal.HOLD

    def test_always_returns_hold_with_ctx(self):
        s = MevArbMonitor()
        candle = _candle()
        ctx = _make_ctx()
        assert s.on_candle(candle, [candle], ctx=ctx) == Signal.HOLD

    def test_always_returns_hold_with_opportunities(self):
        """Even when profitable routes are found, still returns HOLD."""
        s = MevArbMonitor(min_profit_bps=5.0)
        candle = _candle()
        ctx = _make_ctx(routes=[{"profit_bps": 50.0, "hops": 2}])
        result = s.on_candle(candle, [candle], ctx=ctx)
        assert result == Signal.HOLD

    def test_always_returns_hold_multiple_calls(self):
        s = MevArbMonitor()
        candle = _candle()
        ctx = _make_ctx()
        for i in range(10):
            assert s.on_candle(candle, [candle], ctx=ctx) == Signal.HOLD


class TestReset:
    def test_reset_clears_opportunities_counter(self):
        s = MevArbMonitor(min_profit_bps=5.0)
        candle = _candle()
        ctx = _make_ctx(routes=[
            {"profit_bps": 20.0, "hops": 1},
            {"profit_bps": 30.0, "hops": 2},
        ])
        s.on_candle(candle, [candle], ctx=ctx)
        s.on_candle(candle, [candle], ctx=ctx)
        assert s._opportunities_found > 0

        s.reset()
        assert s._opportunities_found == 0

    def test_reset_initial_state(self):
        s = MevArbMonitor()
        assert s._opportunities_found == 0
        s.reset()
        assert s._opportunities_found == 0


class TestOpportunityTracking:
    def test_opportunity_counted_above_threshold(self):
        """Routes above min_profit_bps increment the counter."""
        s = MevArbMonitor(min_profit_bps=10.0)
        candle = _candle()
        ctx = _make_ctx(routes=[{"profit_bps": 20.0, "hops": 1}])
        s.on_candle(candle, [candle], ctx=ctx)
        assert s._opportunities_found == 1

    def test_opportunity_not_counted_below_threshold(self):
        """Routes below min_profit_bps are not counted."""
        s = MevArbMonitor(min_profit_bps=50.0)
        candle = _candle()
        ctx = _make_ctx(routes=[{"profit_bps": 5.0, "hops": 1}])
        s.on_candle(candle, [candle], ctx=ctx)
        assert s._opportunities_found == 0

    def test_multiple_opportunities_in_one_candle(self):
        """Multiple profitable routes in one candle are all counted."""
        s = MevArbMonitor(min_profit_bps=5.0)
        candle = _candle()
        ctx = _make_ctx(routes=[
            {"profit_bps": 10.0, "hops": 1},
            {"profit_bps": 15.0, "hops": 2},
            {"profit_bps": 2.0, "hops": 1},   # below threshold — not counted
        ])
        s.on_candle(candle, [candle], ctx=ctx)
        assert s._opportunities_found == 2

    def test_no_ctx_no_scan(self):
        """Without ctx, no scan runs and counter stays at 0."""
        s = MevArbMonitor()
        candle = _candle()
        s.on_candle(candle, [candle], ctx=None)
        assert s._opportunities_found == 0

    def test_ctx_without_get_arb_routes(self):
        """ctx that lacks get_arb_routes returns no routes gracefully."""
        s = MevArbMonitor()
        candle = _candle()
        ctx = MagicMock(spec=[])  # no attributes
        result = s.on_candle(candle, [candle], ctx=ctx)
        assert result == Signal.HOLD
        assert s._opportunities_found == 0
