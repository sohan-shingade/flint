"""D-2.1.b Step 6 — BorrowLedger unit tests."""
from __future__ import annotations


from flint.execution.borrow_ledger import BorrowLedger
from flint.models import BorrowSnapshot


def _bs(market="JUP-PERP", ts=1700000000, rate=0.0001, cum=0.001) -> BorrowSnapshot:
    return BorrowSnapshot(
        market=market, ts=ts, rate_hourly=rate,
        utilization=0.5, cumulative_rate=cum, source="rpc",
    )


class TestRecordAndLatest:
    def test_empty_returns_none(self):
        bl = BorrowLedger()
        assert bl.latest("JUP-PERP") is None

    def test_latest_returns_most_recent(self):
        bl = BorrowLedger()
        bl.record(_bs(rate=0.0001))
        bl.record(_bs(ts=1700003600, rate=0.0002))
        assert bl.latest("JUP-PERP") == 0.0002


class TestRecent:
    def test_returns_ts_rate_pairs(self):
        bl = BorrowLedger()
        bl.record(_bs(ts=1, rate=0.001))
        bl.record(_bs(ts=2, rate=0.002))
        assert bl.recent("JUP-PERP") == [(1, 0.001), (2, 0.002)]

    def test_lookback_truncates(self):
        bl = BorrowLedger()
        for i in range(50):
            bl.record(_bs(ts=i, rate=0.0001 * i))
        rec = bl.recent("JUP-PERP", lookback=5)
        assert len(rec) == 5


class TestCumulativeAt:
    def test_returns_value_at_or_before_ts(self):
        bl = BorrowLedger()
        bl.record(_bs(ts=10, cum=0.005))
        bl.record(_bs(ts=20, cum=0.010))
        bl.record(_bs(ts=30, cum=0.015))
        assert bl.cumulative_at("JUP-PERP", 5) is None       # before all
        assert bl.cumulative_at("JUP-PERP", 10) == 0.005
        assert bl.cumulative_at("JUP-PERP", 25) == 0.010     # latest ≤ 25
        assert bl.cumulative_at("JUP-PERP", 100) == 0.015    # past end


class TestPaymentsAndTotal:
    def test_record_payment_appends(self):
        bl = BorrowLedger()
        bl.record_payment({"market": "JUP-PERP", "cost": 1.5, "ts": 1})
        bl.record_payment({"market": "JUP-PERP", "cost": 2.0, "ts": 2})
        assert len(bl.payments) == 2
        assert bl.payments[0]["cost"] == 1.5

    def test_add_paid_accumulates(self):
        bl = BorrowLedger()
        bl.add_paid(0.5)
        bl.add_paid(1.25)
        assert bl.total_paid == 1.75


class TestBacktestContextIntegration:
    def test_context_owns_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._bl, BorrowLedger)

    def test_add_borrow_rate_routes_through_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_borrow_rate(_bs(rate=0.0009))
        assert ctx.get_borrow_rate("JUP-PERP") == 0.0009

    def test_get_borrow_cumulative_at_uses_ledger(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_borrow_rate(_bs(ts=10, cum=0.001))
        ctx.add_borrow_rate(_bs(ts=20, cum=0.002))
        assert ctx.get_borrow_cumulative_at("JUP-PERP", 25) == 0.002

    def test_legacy_total_borrow_paid_compound_assignment(self):
        """`_apply_fill` does `self._total_borrow_paid += borrow_cost`.
        The setter must persist that to the ledger."""
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._total_borrow_paid += 7.5
        ctx._total_borrow_paid += 2.5
        assert ctx._bl.total_paid == 10.0
        assert ctx.total_borrow_paid == 10.0  # public property reads same

    def test_legacy_borrow_payments_append(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._borrow_payments.append({"market": "JUP-PERP", "cost": 3.14})
        assert ctx._bl.payments == [{"market": "JUP-PERP", "cost": 3.14}]


class TestLedgerIsolation:
    def test_two_contexts_have_independent_ledgers(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000)
        b = BacktestContext(initial_capital=2_000)
        a.add_borrow_rate(_bs(rate=0.0001))
        a._total_borrow_paid += 5.0
        assert b.get_borrow_rate("JUP-PERP") is None
        assert b._total_borrow_paid == 0.0
        assert a._bl is not b._bl
