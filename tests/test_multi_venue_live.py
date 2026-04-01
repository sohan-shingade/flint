"""Tests for MultiVenueLiveContext — mocked venue contexts."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock

from flint.models import AccountState, Candle, Fill, Order, OrderType, OrderState, PositionInfo, Side
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
    ctx.limit_order = MagicMock(return_value="ord-2")
    ctx.stop_order = MagicMock(return_value="ord-3")
    ctx.take_profit_order = MagicMock(return_value="ord-4")
    ctx.cancel = MagicMock(return_value=True)
    ctx.cancel_all = MagicMock(return_value=0)
    ctx.connect = AsyncMock()
    ctx.disconnect = AsyncMock()
    ctx.submit_pending_orders = AsyncMock(return_value=[])
    ctx._poll_orders_loop = AsyncMock()
    return ctx


class TestConstruction:
    def test_creates_with_two_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert len(ctx._contexts) == 2

    def test_primary_defaults_to_first_key(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx._primary_venue == "drift"

    def test_primary_explicit(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl}, primary_venue="hyperliquid")
        assert ctx._primary_venue == "hyperliquid"


class TestAggregatedAccount:
    def test_account_sums_equity(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.account.equity == 8000.0
        assert ctx.account.cash == 8000.0

    def test_venue_account_returns_single(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift", cash=5000.0)
        hl = _make_mock_venue("hyperliquid", cash=3000.0)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.venue_account("drift").equity == 5000.0

    def test_positions_merges_all(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert len(ctx.positions) == 2

    def test_total_exposure_nets_across_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        assert ctx.total_exposure("SOL-PERP") == 0.0

    def test_per_venue_pnl(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, unrealized_pnl=50.0, venue="drift")]
        hl_pos = [PositionInfo(market="SOL-PERP", side=Side.SHORT, size=10.0, entry_price=150.0, unrealized_pnl=-30.0, venue="hyperliquid")]
        drift = _make_mock_venue("drift", cash=5000.0, positions=drift_pos)
        hl = _make_mock_venue("hyperliquid", cash=3000.0, positions=hl_pos)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        pnl = ctx.per_venue_pnl()
        assert pnl["drift"] == 50.0
        assert pnl["hyperliquid"] == -30.0


class TestOrderRouting:
    def test_market_order_routes_to_venue(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.market_order("SOL-PERP", Side.LONG, 10.0, venue="drift")
        drift.market_order.assert_called_once()
        hl.market_order.assert_not_called()

    def test_market_order_routes_to_hyperliquid(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.market_order("SOL-PERP", Side.SHORT, 5.0, venue="hyperliquid")
        hl.market_order.assert_called_once()
        drift.market_order.assert_not_called()

    def test_default_venue_routes_to_primary(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl}, primary_venue="hyperliquid")
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        hl.market_order.assert_called_once()
        drift.market_order.assert_not_called()

    def test_limit_order_routes(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0, venue="drift")
        drift.limit_order.assert_called_once()

    def test_stop_order_routes(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0, venue="hyperliquid")
        hl.stop_order.assert_called_once()

    def test_cancel_all_cancels_across_venues(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        drift.cancel_all.return_value = 2
        hl = _make_mock_venue("hyperliquid")
        hl.cancel_all.return_value = 1
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        total = ctx.cancel_all()
        assert total == 3
