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


class TestLegGroupSubmission:
    def test_submit_leg_group_both_fill(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.SHORT, price=150.0, size=10.0, fee=0.05, ts=1000, order_id="hl-ord-1", venue="hyperliquid"),
        ])

        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "filled"
        assert len(result.filled_legs) == 2
        assert len(result.failed_legs) == 0

    def test_submit_leg_group_one_fails_no_unwind(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[])

        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            leg_timeout_s=0.1,
            auto_unwind_failed_legs=False,
        )
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "partial"
        assert len(result.filled_legs) == 1
        assert len(result.failed_legs) == 1
        assert len(result.unwind_order_ids) == 0

    def test_submit_leg_group_auto_unwind(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        from flint.models import OrderLeg

        drift = _make_mock_venue("drift")
        drift.market_order.return_value = "drift-ord-1"
        hl = _make_mock_venue("hyperliquid")
        hl.market_order.return_value = "hl-ord-1"

        drift.submit_pending_orders = AsyncMock(return_value=[
            Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000, order_id="drift-ord-1", venue="drift"),
        ])
        hl.submit_pending_orders = AsyncMock(return_value=[])

        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            leg_timeout_s=0.1,
            auto_unwind_failed_legs=True,
        )
        legs = [
            OrderLeg(order_id="", venue="drift", market="SOL-PERP", side=Side.LONG, size=10.0),
            OrderLeg(order_id="", venue="hyperliquid", market="SOL-PERP", side=Side.SHORT, size=10.0),
        ]
        result = run(ctx.submit_leg_group(legs))
        assert result.status == "unwound"
        assert len(result.unwind_order_ids) == 1


class TestTickRouting:
    def test_on_ws_candle_primary_mode_filters(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            primary_venue="drift",
            tick_mode="primary",
        )
        ctx._candle_queue = asyncio.Queue()

        drift_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")
        ctx._on_ws_candle(drift_candle)
        assert ctx._candle_queue.qsize() == 1

        hl_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")
        ctx._on_ws_candle(hl_candle)
        assert ctx._candle_queue.qsize() == 1  # Still 1

    def test_on_ws_candle_any_mode_enqueues_all(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift = _make_mock_venue("drift")
        hl = _make_mock_venue("hyperliquid")
        ctx = MultiVenueLiveContext(
            contexts={"drift": drift, "hyperliquid": hl},
            tick_mode="any",
        )
        ctx._candle_queue = asyncio.Queue()

        drift_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")
        ctx._on_ws_candle(drift_candle)
        hl_candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=153.0, volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")
        ctx._on_ws_candle(hl_candle)
        assert ctx._candle_queue.qsize() == 2


class TestClosePosition:
    def test_close_position_on_specific_venue(self):
        from flint.execution.multi_venue_live import MultiVenueLiveContext
        drift_pos = [PositionInfo(market="SOL-PERP", side=Side.LONG, size=10.0, entry_price=150.0, venue="drift")]
        drift = _make_mock_venue("drift", positions=drift_pos)
        drift.close_position = MagicMock(return_value="close-1")
        hl = _make_mock_venue("hyperliquid")
        hl.close_position = MagicMock(return_value=None)
        ctx = MultiVenueLiveContext(contexts={"drift": drift, "hyperliquid": hl})
        result = ctx.close_position("SOL-PERP", venue="drift")
        drift.close_position.assert_called_once_with("SOL-PERP", "drift")
