"""Tests for LiveExecutionContext base class — uses a mock venue implementation."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from flint.models import (
    AccountState, Candle, Fill, Order, OrderType, OrderState,
    PositionInfo, Side,
)
from flint.execution.live_base import LiveExecutionContext
from flint.execution.order_tracker import OrderTracker
from flint.risk.guards import RiskManager
from flint.store import FlintStore


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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


class TestTickLoop:
    def test_single_tick(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        mock_strategy = MagicMock()
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP", resolution_s=60)
        async def fetch():
            return candle
        run(ctx._tick(mock_strategy, "SOL-PERP", fetch_candle=fetch))
        mock_strategy.on_candle.assert_called_once_with(ctx)
        assert ctx._current_candle == candle

    def test_tick_no_candle_skips(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        mock_strategy = MagicMock()
        async def fetch():
            return None
        run(ctx._tick(mock_strategy, "SOL-PERP", fetch_candle=fetch))
        mock_strategy.on_candle.assert_not_called()


class TestModifyOrder:
    def test_modify_price(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        new_oid = ctx.modify_order(oid, new_price=145.0)
        assert new_oid != ""
        assert new_oid != oid
        # Original should be cancelled
        old = ctx._tracker.get(oid)
        assert old.state == OrderState.CANCELLED
        # New order should have updated price
        new = ctx._tracker.get(new_oid)
        assert new.order.price == 145.0
        assert new.order.size == 10.0

    def test_modify_size(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        new_oid = ctx.modify_order(oid, new_size=5.0)
        new = ctx._tracker.get(new_oid)
        assert new.order.size == 5.0
        assert new.order.price == 150.0

    def test_modify_both(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        new_oid = ctx.modify_order(oid, new_size=5.0, new_price=145.0)
        new = ctx._tracker.get(new_oid)
        assert new.order.size == 5.0
        assert new.order.price == 145.0

    def test_modify_terminal_order_fails(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx.cancel(oid)  # now it's cancelled (terminal)
        new_oid = ctx.modify_order(oid, new_size=5.0)
        assert new_oid == ""

    def test_modify_nonexistent_fails(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        new_oid = ctx.modify_order("nonexistent", new_size=5.0)
        assert new_oid == ""


class TestOrderPolling:
    def test_poll_detects_fill(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        ctx._tracker.mark_submitted(oid, tx_sig="tx1")
        ctx._tracker.mark_confirmed(oid, venue_order_id=1)
        async def mock_poll(venue_oid):
            return OrderState.FILLED
        ctx._poll_order_status = mock_poll
        run(ctx._poll_active_orders())
        assert oid in ctx._tracker.completed_orders
        assert ctx._tracker.completed_orders[oid].state == OrderState.FILLED


class TestEventDrivenTick:
    def test_candle_queue_triggers_tick(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               tick_mode="on_candle_close", tick_markets=["SOL-PERP"])
        mock_strategy = MagicMock()
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")

        async def fetch():
            return candle

        async def test():
            ctx._candle_queue = asyncio.Queue()
            ctx._running = True
            ctx._candle_queue.put_nowait(candle)
            got = await asyncio.wait_for(ctx._candle_queue.get(), timeout=1.0)
            ctx._current_candle = got
            ctx._tick_count += 1
            await ctx._tick(mock_strategy, "SOL-PERP", fetch_candle=fetch)
        run(test())
        mock_strategy.on_candle.assert_called_once()
        assert ctx._current_candle == candle

    def test_on_ws_candle_filters_by_tick_markets(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               tick_mode="on_candle_close", tick_markets=["SOL-PERP"])
        ctx._candle_queue = asyncio.Queue()

        sol_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                           close=150.5, volume=1000.0, market="SOL-PERP",
                           resolution_s=60, venue="drift")
        btc_candle = Candle(ts=1000, open=65000.0, high=65100.0, low=64900.0,
                           close=65050.0, volume=10.0, market="BTC-PERP",
                           resolution_s=60, venue="drift")

        ctx._on_ws_candle(sol_candle)
        ctx._on_ws_candle(btc_candle)
        assert ctx._candle_queue.qsize() == 1

    def test_venue_specific_tick_markets(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               tick_mode="on_candle_close", tick_markets=["drift:SOL-PERP"])
        ctx._candle_queue = asyncio.Queue()

        drift_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                             close=150.5, volume=1000.0, market="SOL-PERP",
                             resolution_s=60, venue="drift")
        hl_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                          close=150.5, volume=1000.0, market="SOL-PERP",
                          resolution_s=60, venue="hyperliquid")

        ctx._on_ws_candle(drift_candle)
        ctx._on_ws_candle(hl_candle)
        assert ctx._candle_queue.qsize() == 1


class TestOraclePrice:
    def test_get_oracle_price_default_none(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        assert ctx.get_oracle_price("SOL-PERP") is None

    def test_get_oracle_price_from_pyth_feed(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        mock_pyth = MagicMock()
        mock_pyth.get_price.return_value = (150.25, 1000)
        ctx._pyth_feed = mock_pyth
        result = ctx.get_oracle_price("SOL-PERP")
        assert result == (150.25, 1000)


class TestDryRunMode:
    def test_dry_run_simulates_fill(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""
        run(ctx.submit_pending_orders())
        tracked = ctx._tracker.get(oid)
        assert tracked is not None
        assert tracked.state == OrderState.FILLED
        assert len(tracked.fills) == 1
        assert tracked.fills[0].tx_sig == "DRY_RUN"
        assert tracked.fills[0].price == 150.5

    def test_dry_run_updates_position(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0

    def test_dry_run_does_not_call_place_order(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0, dry_run=True)
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")
        ctx._current_candle = candle
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())
        assert len(ctx._placed_orders) == 0


class TestEquityMonitorIntegration:
    def test_monitor_attaches(self):
        from flint.risk.monitor import EquityMonitor
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._equity_monitor = EquityMonitor(context=ctx, kill_switch_pct=0.15)
        assert ctx._equity_monitor is not None
        assert ctx._equity_monitor.tripped is False


class TestAlertIntegration:
    def test_fill_fires_notification(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               notification_manager=nm)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id="o1", venue="test")
        ctx._handle_fill("o1", fill)
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "fill"

    def test_failure_fires_notification(self):
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               notification_manager=nm)
        ctx._handle_fail("o1", "retries exhausted")
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "order_failed"

    def test_risk_rejection_fires_notification(self):
        from flint.risk.guards import RiskManager, MaxOpenPositions
        nm = MagicMock()
        nm.notify = AsyncMock(return_value=1)
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])
        ctx = MockVenueContext(venue="test", initial_capital=10000.0,
                               risk_manager=rm, notification_manager=nm)
        ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert nm.notify.call_count == 1
        event = nm.notify.call_args[0][0]
        assert event.event_type == "risk_rejection"

    def test_no_notification_without_manager(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1000, order_id="o1", venue="test")
        ctx._handle_fill("o1", fill)  # Should not raise
