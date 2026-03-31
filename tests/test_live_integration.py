"""Integration test: full order lifecycle through LiveExecutionContext -> Store."""
import asyncio
import time
import pytest

from flint.models import Fill, Order, OrderState, OrderType, PositionInfo, Side
from flint.store import FlintStore
from flint.execution.live_base import LiveExecutionContext
from flint.execution.order_tracker import OrderTracker


class MockVenueForIntegration(LiveExecutionContext):
    """Mock venue that simulates place -> confirm -> fill lifecycle."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mock_venue_oid = 0

    async def _connect(self):
        pass

    async def _disconnect(self):
        pass

    async def _place_order(self, order):
        self._mock_venue_oid += 1
        return (f"tx_{self._mock_venue_oid}", self._mock_venue_oid)

    async def _cancel_order(self, venue_order_id):
        return True

    async def _fetch_positions(self):
        return []

    async def _fetch_balance(self):
        return 10000.0

    async def _poll_order_status(self, venue_order_id):
        return OrderState.CONFIRMED


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestEndToEndLifecycle:
    def test_submit_order_persists_to_store(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="int-test",
        )

        # Place an order
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""

        # Submit pending orders (simulates tick loop)
        run(ctx.submit_pending_orders())

        # Verify order persisted to store
        orders = store.get_live_orders("int-test")
        assert len(orders) == 1
        assert orders[0]["order_id"] == oid
        assert orders[0]["state"] in ("submitted", "confirmed")

        store.close()

    def test_fill_updates_position_and_store(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="int-test-2",
        )

        # Place and submit
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        run(ctx.submit_pending_orders())

        # Simulate fill arriving via tracker
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=int(time.time()),
            order_id=oid, tx_sig="tx_1", venue="test",
        )
        ctx._tracker.mark_filled(oid, fill)

        # Position should be updated
        pos = ctx.position("SOL-PERP", venue="test")
        assert pos is not None
        assert pos.size == 10.0
        assert pos.entry_price == 150.0

        # Fill should be in store
        fills = store.get_live_fills("int-test-2")
        assert len(fills) == 1
        assert fills[0]["price"] == 150.0

        store.close()

    def test_session_creation_and_equity(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.create_live_session(
            session_id="eq-test",
            strategy_name="test_strat",
            market="SOL-PERP",
            network="devnet",
            venue="test",
            initial_capital=10000.0,
            config_snapshot="{}",
        )

        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="eq-test",
        )

        # Persist equity
        ctx._persist_equity()

        history = store.get_live_equity_history("eq-test")
        assert len(history) == 1
        assert history[0]["equity"] == 10000.0
        assert history[0]["cash"] == 10000.0

        store.close()

    def test_risk_guard_rejects_order(self, tmp_path):
        from flint.risk.guards import RiskManager, MaxOpenPositions
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        rm = RiskManager(guards=[MaxOpenPositions(max_positions=0)])

        ctx = MockVenueForIntegration(
            venue="test", initial_capital=10000.0,
            store=store, session_id="risk-test",
            risk_manager=rm,
        )

        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid == ""  # rejected
        assert len(ctx._tracker.active_orders) == 0

        store.close()
