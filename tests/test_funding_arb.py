"""Tests for FundingArbStrategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from flint.models import AccountState, Candle, PositionInfo, Side, Signal


def _make_mock_ctx(funding_by_venue=None, positions=None, cash=10000.0):
    ctx = MagicMock()
    positions = positions or []
    unrealized = sum(p.unrealized_pnl for p in positions)
    type(ctx).account = PropertyMock(return_value=AccountState(
        equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized,
    ))
    type(ctx).positions = PropertyMock(return_value=positions)
    ctx.get_funding_by_venue.return_value = funding_by_venue or {}
    ctx.position.return_value = None
    ctx.total_exposure = MagicMock(return_value=0.0)
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    return ctx


class TestSignalGeneration:
    def test_no_entry_when_spread_below_threshold(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(min_spread_bps=5.0, venues=["drift", "hyperliquid"])
        strategy.reset()
        ctx = _make_mock_ctx(funding_by_venue={
            "drift": [(1000, 0.0001)],
            "hyperliquid": [(1000, 0.0002)],
        })
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_entry_when_spread_above_threshold(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(
            min_spread_bps=5.0, position_size_usd=1000.0,
            min_spread_duration=0, venues=["drift", "hyperliquid"],
        )
        strategy.reset()
        ctx = _make_mock_ctx(funding_by_venue={
            "drift": [(1000, 0.0001)],
            "hyperliquid": [(1000, 0.001)],
        })
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=150.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.market_order.call_count == 2

    def test_exit_when_spread_converges(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        strategy = FundingArbStrategy(
            min_spread_bps=5.0, exit_spread_bps=1.0, venues=["drift", "hyperliquid"],
        )
        strategy.reset()
        strategy._entry_ts = 500
        strategy._long_venue = "drift"
        strategy._short_venue = "hyperliquid"

        drift_pos = PositionInfo(market="SOL-PERP", side=Side.LONG, size=6.0, entry_price=150.0, venue="drift")
        hl_pos = PositionInfo(market="SOL-PERP", side=Side.SHORT, size=6.0, entry_price=150.0, venue="hyperliquid")

        ctx = _make_mock_ctx(
            funding_by_venue={
                "drift": [(1000, 0.0002)],
                "hyperliquid": [(1000, 0.00025)],
            },
            positions=[drift_pos, hl_pos],
        )
        ctx.position.side_effect = lambda market, venue="default": {
            "drift": drift_pos, "hyperliquid": hl_pos,
        }.get(venue)

        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = strategy.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        assert ctx.close_position.call_count == 2


class TestParameters:
    def test_parameters_returns_bounds(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        params = FundingArbStrategy.parameters()
        assert "min_spread_bps" in params
        assert "exit_spread_bps" in params
        assert "max_hold_hours" in params
        assert "position_size_usd" in params


class TestName:
    def test_name(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        s = FundingArbStrategy()
        assert s.name == "funding_arb"
