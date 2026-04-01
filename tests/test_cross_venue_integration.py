"""Integration tests for cross-venue strategies."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock

from flint.models import (
    AccountState, Candle, Fill, FundingRate, OrderLeg,
    PositionInfo, Side, Signal,
)
from flint.execution.live_base import LiveExecutionContext


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_venue(venue_name, cash=5000.0, positions=None):
    ctx = MagicMock(spec=LiveExecutionContext)
    ctx._venue = venue_name
    positions = positions or []
    unrealized = sum(p.unrealized_pnl for p in positions)
    type(ctx).account = PropertyMock(return_value=AccountState(
        equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized,
    ))
    type(ctx).positions = PropertyMock(return_value=positions)
    type(ctx).pending_orders = PropertyMock(return_value=[])
    type(ctx).current_candle = PropertyMock(return_value=None)
    type(ctx).timestamp = PropertyMock(return_value=1000)
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.cancel = MagicMock(return_value=True)
    ctx.cancel_all = MagicMock(return_value=0)
    ctx.close_position = MagicMock(return_value="close-1")
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    ctx.get_funding_rates = MagicMock(return_value=[])
    return ctx


class TestFundingArbBacktest:
    def test_backtest_with_funding_data(self):
        from flint.strategy.funding_arb import FundingArbStrategy
        from flint.backtest.engine import BacktestEngine

        strategy = FundingArbStrategy(
            min_spread_bps=5.0, min_spread_duration=0,
            position_size_usd=1000.0, venues=["drift", "hyperliquid"],
        )
        engine = BacktestEngine(
            strategy=strategy, initial_capital=10000.0,
            funding_rates=[
                FundingRate(market="SOL-PERP", ts=1000 + i * 60, rate=0.0001, oracle_price=150.0, mark_price=150.0, source="drift")
                for i in range(10)
            ] + [
                FundingRate(market="SOL-PERP", ts=1000 + i * 60, rate=0.001, oracle_price=150.0, mark_price=150.0, source="hyperliquid")
                for i in range(10)
            ],
        )
        candles = [
            Candle(ts=1000 + i * 60, open=150.0 + i, high=151.0 + i, low=149.0 + i,
                   close=150.5 + i, volume=100.0, market="SOL-PERP", resolution_s=60)
            for i in range(10)
        ]
        result = engine.run(candles)
        assert result is not None
        assert isinstance(result.per_venue_pnl, dict)


class TestMultiVenueDryRun:
    def test_dry_run_places_on_both_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})

        ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="drift")
        ctx.market_order("SOL-PERP", Side.SHORT, 10.0, venue="hyperliquid")

        drift.market_order.assert_called_once()
        hl.market_order.assert_called_once()

    def test_aggregated_equity_reflects_both(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.account.equity == 8000.0
        assert ctx.venue_account("drift").equity == 5000.0
        assert ctx.venue_account("hyperliquid").equity == 3000.0


class TestLegGroupIntegration:
    def test_leg_group_full_flow(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "d-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "h-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="d-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.SHORT, price=150.0, size=10.0, fee=0.05, ts=1000, order_id="h-1", venue="hyperliquid"),
        ])

        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
