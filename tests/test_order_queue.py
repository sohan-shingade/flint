"""D-2.1.b Step 4 — OrderQueue unit tests."""
from __future__ import annotations


from flint.execution.order_queue import DEFAULT_PENDING_CAP, OrderQueue
from flint.models import Order, OrderType, Side


def _o(market="SOL-PERP", oid="o1", price=100.0) -> Order:
    return Order(
        market=market, side=Side.LONG, order_type=OrderType.LIMIT,
        size=1.0, price=price, order_id=oid, ts=1700000000,
    )


class TestPending:
    def test_add_and_iterate(self):
        q = OrderQueue()
        assert q.pending == []
        assert q.add_pending(_o(oid="a")) is True
        assert q.add_pending(_o(oid="b")) is True
        ids = [o.order_id for o in q.pending]
        assert ids == ["a", "b"]

    def test_pending_cap_returns_false(self):
        q = OrderQueue(pending_cap=3)
        for i in range(3):
            assert q.add_pending(_o(oid=f"o{i}")) is True
        # Fourth attempt rejected — caller treats False as "drop".
        assert q.add_pending(_o(oid="o3")) is False
        assert len(q.pending) == 3

    def test_default_cap_is_100(self):
        assert DEFAULT_PENDING_CAP == 100
        q = OrderQueue()
        assert q.pending_cap == 100

    def test_cancel_by_id(self):
        q = OrderQueue()
        q.add_pending(_o(oid="a"))
        q.add_pending(_o(oid="b"))
        assert q.cancel("a") is True
        assert [o.order_id for o in q.pending] == ["b"]
        assert q.cancel("missing") is False

    def test_cancel_all_no_arg_clears_everything(self):
        q = OrderQueue()
        q.add_pending(_o(oid="a"))
        q.add_pending(_o(oid="b"))
        assert q.cancel_all() == 2
        assert q.pending == []

    def test_cancel_all_filters_by_market(self):
        q = OrderQueue()
        q.add_pending(_o(market="SOL-PERP", oid="s1"))
        q.add_pending(_o(market="BTC-PERP", oid="b1"))
        q.add_pending(_o(market="SOL-PERP", oid="s2"))
        removed = q.cancel_all(market="SOL-PERP")
        assert removed == 2
        assert [o.order_id for o in q.pending] == ["b1"]

    def test_snapshot_is_a_copy(self):
        q = OrderQueue()
        q.add_pending(_o(oid="a"))
        snap = q.snapshot()
        snap.append("polluted")
        assert q.pending == [_o(oid="a")]


class TestMarketQueue:
    def test_add_drain(self):
        q = OrderQueue()
        q.add_market(_o(oid="m1"))
        q.add_market(_o(oid="m2"))
        drained = q.drain_market()
        assert [o.order_id for o in drained] == ["m1", "m2"]
        assert q.market_queue == []

    def test_drain_returns_old_list_then_replaces(self):
        q = OrderQueue()
        q.add_market(_o(oid="m1"))
        first = q.drain_market()
        q.add_market(_o(oid="m2"))
        # The first batch must not include m2 — drain swapped lists.
        assert [o.order_id for o in first] == ["m1"]
        assert [o.order_id for o in q.market_queue] == ["m2"]


class TestPendingReassignment:
    """Existing BacktestContext call sites do
    `self._pending_orders = [filtered list]`. The setter must accept
    that and replace the underlying list."""

    def test_setter_replaces_list(self):
        q = OrderQueue()
        q.add_pending(_o(oid="a"))
        q.add_pending(_o(oid="b"))
        q.pending = [_o(oid="c")]
        assert [o.order_id for o in q.pending] == ["c"]

    def test_market_setter_replaces_list(self):
        q = OrderQueue()
        q.add_market(_o(oid="m1"))
        q.market_queue = []
        assert q.market_queue == []


class TestBacktestContextIntegration:
    def test_context_owns_an_order_queue(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._oq, OrderQueue)

    def test_legacy_append_routes_through_queue(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._pending_orders.append(_o(oid="a"))
        ctx._market_orders_queue.append(_o(oid="m1"))
        assert len(ctx._oq.pending) == 1
        assert len(ctx._oq.market_queue) == 1

    def test_legacy_reassignment_routes_through_queue(self):
        """Critical: `cancel_all` and `process_pending_orders` rebuild
        the list via `self._pending_orders = [...]`. Confirm the
        setter still routes that through the manager."""
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._pending_orders.append(_o(oid="a"))
        ctx._pending_orders.append(_o(oid="b"))
        ctx._pending_orders = [
            o for o in ctx._pending_orders if o.order_id != "a"
        ]
        assert [o.order_id for o in ctx._oq.pending] == ["b"]

    def test_pending_orders_public_property_unchanged(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._pending_orders.append(_o(oid="a"))
        assert len(ctx.pending_orders) == 1


class TestOrderQueueDoesNotLeakAcrossInstances:
    def test_two_contexts_have_independent_queues(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000)
        b = BacktestContext(initial_capital=2_000)
        a._pending_orders.append(_o(oid="a-only"))
        assert b._pending_orders == []
        assert a._oq is not b._oq
