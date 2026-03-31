"""Tests for LiveExecutionContext base class — uses a mock venue implementation."""
import asyncio
import time
import pytest

from flint.models import (
    AccountState, Candle, Fill, Order, OrderType, OrderState,
    PositionInfo, Side,
)
from flint.execution.live_base import LiveExecutionContext
from flint.execution.order_tracker import OrderTracker
from flint.risk.guards import RiskManager


class MockVenueContext(LiveExecutionContext):
    """Concrete implementation for testing the base class."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connected = False
        self._placed_orders = []
        self._cancelled_orders = []
        self._mock_positions = []
        self._mock_balance = 10000.0
        self._mock_order_counter = 0

    async def _connect(self):
        self._connected = True

    async def _disconnect(self):
        self._connected = False

    async def _place_order(self, order):
        self._mock_order_counter += 1
        self._placed_orders.append(order)
        return (f"tx_{self._mock_order_counter}", self._mock_order_counter)

    async def _cancel_order(self, venue_order_id):
        self._cancelled_orders.append(venue_order_id)
        return True

    async def _fetch_positions(self):
        return self._mock_positions

    async def _fetch_balance(self):
        return self._mock_balance

    async def _poll_order_status(self, venue_order_id):
        return OrderState.CONFIRMED


class TestLifecycle:
    def test_create(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        assert ctx.account.cash == 10000.0
        assert ctx.positions == []
        assert ctx.pending_orders == []

    def test_connect(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        asyncio.get_event_loop().run_until_complete(ctx.connect())
        assert ctx._connected is True

    def test_disconnect(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(ctx.connect())
        loop.run_until_complete(ctx.disconnect())
        assert ctx._connected is False


class TestOrderFlow:
    def test_market_order_returns_id(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid.startswith("live-")
        assert len(ctx._tracker.active_orders) == 1

    def test_limit_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        tracked = ctx._tracker.get(oid)
        assert tracked is not None
        assert tracked.order.order_type == OrderType.LIMIT

    def test_stop_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.STOP_LOSS

    def test_take_profit_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.take_profit_order("SOL-PERP", Side.LONG, 5.0, 160.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.TAKE_PROFIT

    def test_cancel_pending_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        result = ctx.cancel(oid)
        assert result is True
        tracked = ctx._tracker.get(oid)
        assert tracked.state == OrderState.CANCELLED

    def test_cancel_nonexistent(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        result = ctx.cancel("nonexistent-id")
        assert result is False

    def test_cancel_all(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        count = ctx.cancel_all()
        assert count == 2

    def test_cancel_all_by_market(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        count = ctx.cancel_all(market="SOL-PERP")
        assert count == 1
        assert len(ctx._tracker.active_orders) == 1


class TestRiskGuardIntegration:
    def test_order_rejected_by_risk_guard(self):
        from flint.risk.guards import MaxOpenPositions
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])
        ctx = MockVenueContext(
            venue="test", initial_capital=10000.0, risk_manager=rm,
        )
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid == ""


class TestPositionCache:
    def test_position_lookup(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, venue="test",
        )
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0

    def test_positions_list(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, venue="test",
        )
        assert len(ctx.positions) == 1


class TestAccountState:
    def test_account_with_positions(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._positions_cache[("test", "SOL-PERP")] = PositionInfo(
            market="SOL-PERP", side=Side.LONG, size=10.0,
            entry_price=150.0, unrealized_pnl=50.0, venue="test",
        )
        account = ctx.account
        assert account.cash == 10000.0
        assert account.unrealized_pnl == 50.0
        assert account.equity == 10050.0
