"""Tests for OrderTracker — state machine, rate limiting, timeouts."""
import time
import pytest

from flint.models import Order, OrderType, OrderState, Side, Fill
from flint.execution.order_tracker import OrderTracker, TrackedOrder


class TestTrackedOrder:
    def test_create(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-1", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        assert tracked.state == OrderState.PENDING
        assert tracked.flint_order_id == "test-1"
        assert tracked.venue_order_id is None
        assert tracked.tx_sig is None
        assert tracked.retry_count == 0
        assert len(tracked.state_history) == 1
        assert tracked.state_history[0][0] == OrderState.PENDING

    def test_transition_valid(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-2", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        tracked.transition(OrderState.SUBMITTED, tx_sig="tx_abc")
        assert tracked.state == OrderState.SUBMITTED
        assert tracked.tx_sig == "tx_abc"
        assert len(tracked.state_history) == 2

    def test_transition_to_confirmed(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-3", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        tracked.transition(OrderState.SUBMITTED, tx_sig="tx_abc")
        tracked.transition(OrderState.CONFIRMED, venue_order_id=42)
        assert tracked.state == OrderState.CONFIRMED
        assert tracked.venue_order_id == 42

    def test_is_terminal(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-4", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.SUBMITTED)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.CONFIRMED)
        assert tracked.is_terminal is False
        tracked.transition(OrderState.FILLED)
        assert tracked.is_terminal is True

    def test_add_fill(self):
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="test-5", ts=1000,
        )
        tracked = TrackedOrder(order=order)
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=1001, order_id="test-5",
        )
        tracked.add_fill(fill)
        assert len(tracked.fills) == 1
        assert tracked.filled_size == 10.0


class TestOrderTracker:
    def test_submit_order(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-1", ts=1000,
        )
        tracked = tracker.submit(order)
        assert tracked.state == OrderState.PENDING
        assert "ot-1" in tracker.active_orders

    def test_get_order(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-2", ts=1000,
        )
        tracker.submit(order)
        assert tracker.get("ot-2") is not None
        assert tracker.get("nonexistent") is None

    def test_mark_submitted(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-3", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_submitted("ot-3", tx_sig="sig_xyz")
        tracked = tracker.get("ot-3")
        assert tracked.state == OrderState.SUBMITTED
        assert tracked.tx_sig == "sig_xyz"

    def test_mark_filled_moves_to_completed(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-4", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_submitted("ot-4", tx_sig="sig")
        tracker.mark_confirmed("ot-4", venue_order_id=99)
        fill = Fill(
            market="SOL-PERP", side=Side.LONG, price=150.0,
            size=10.0, fee=0.15, ts=1001, order_id="ot-4",
        )
        tracker.mark_filled("ot-4", fill)
        assert "ot-4" not in tracker.active_orders
        assert "ot-4" in tracker.completed_orders

    def test_mark_failed(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        order = Order(
            market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
            size=10.0, order_id="ot-5", ts=1000,
        )
        tracker.submit(order)
        tracker.mark_failed("ot-5", reason="retries exhausted")
        assert "ot-5" not in tracker.active_orders
        assert "ot-5" in tracker.completed_orders
        tracked = tracker.completed_orders["ot-5"]
        assert tracked.state == OrderState.FAILED

    def test_pending_submission_returns_pending_orders(self):
        tracker = OrderTracker(max_retries=3, on_failure="drop")
        o1 = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                   size=10.0, order_id="p-1", ts=1000)
        o2 = Order(market="BTC-PERP", side=Side.SHORT, order_type=OrderType.LIMIT,
                   size=0.1, price=65000.0, order_id="p-2", ts=1000)
        tracker.submit(o1)
        tracker.submit(o2)
        tracker.mark_submitted("p-1", tx_sig="sig1")
        pending = tracker.get_pending()
        assert len(pending) == 1
        assert pending[0].flint_order_id == "p-2"

    def test_callbacks_on_fill(self):
        fills_received = []
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            on_fill=lambda oid, f: fills_received.append((oid, f)),
        )
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10.0, order_id="cb-1", ts=1000)
        tracker.submit(order)
        tracker.mark_submitted("cb-1", tx_sig="sig")
        tracker.mark_confirmed("cb-1", venue_order_id=1)
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0,
                    size=10.0, fee=0.15, ts=1001, order_id="cb-1")
        tracker.mark_filled("cb-1", fill)
        assert len(fills_received) == 1
        assert fills_received[0][0] == "cb-1"

    def test_callbacks_on_fail(self):
        fails_received = []
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            on_fail=lambda oid, reason: fails_received.append((oid, reason)),
        )
        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=10.0, order_id="cb-2", ts=1000)
        tracker.submit(order)
        tracker.mark_failed("cb-2", reason="timeout")
        assert len(fails_received) == 1
        assert fails_received[0][1] == "timeout"


class TestRateLimiter:
    def test_can_submit_within_limits(self):
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            max_orders_per_sec=10, max_concurrent_tx=2,
        )
        assert tracker.can_submit() is True

    def test_concurrent_limit(self):
        tracker = OrderTracker(
            max_retries=3, on_failure="drop",
            max_orders_per_sec=10, max_concurrent_tx=2,
        )
        for i in range(2):
            o = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                      size=1.0, order_id=f"rl-{i}", ts=1000)
            tracker.submit(o)
            tracker.mark_submitted(f"rl-{i}", tx_sig=f"sig-{i}")
        assert tracker.in_flight_count == 2
        assert tracker.can_submit() is False
        tracker.mark_confirmed("rl-0", venue_order_id=1)
        assert tracker.can_submit() is True
