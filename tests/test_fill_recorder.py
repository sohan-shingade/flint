"""D-2.1.b Step 3 — FillRecorder unit tests."""
from __future__ import annotations

from flint.execution.fill_recorder import FillRecorder
from flint.models import Fill, Side


def _fill(market="SOL-PERP", price=100.0) -> Fill:
    return Fill(
        market=market, side=Side.LONG, price=price, size=1.0, fee=0.05,
        ts=1700000000, order_id="o1",
    )


class TestFills:
    def test_record_and_iterate(self):
        fr = FillRecorder()
        assert fr.fills == []
        fr.record(_fill())
        fr.record(_fill(market="BTC-PERP"))
        assert len(fr.fills) == 2
        assert fr.fills[0].market == "SOL-PERP"

    def test_all_fills_returns_copy(self):
        fr = FillRecorder()
        fr.record(_fill())
        snap = fr.all_fills()
        snap.append(_fill(market="ETH-PERP"))
        assert len(fr.fills) == 1  # internal not polluted

    def test_legacy_append_via_property(self):
        """BacktestContext call sites use `self._fills.append(...)`,
        which routes through the property to the manager's underlying
        list. Verify the same access pattern works directly."""
        fr = FillRecorder()
        fr.fills.append(_fill())
        assert len(fr.fills) == 1


class TestLogs:
    def test_log_and_iterate(self):
        fr = FillRecorder()
        fr.log("first")
        fr.log("second")
        assert fr.logs == ["first", "second"]

    def test_messages_returns_copy(self):
        fr = FillRecorder()
        fr.log("hello")
        snap = fr.messages()
        snap.append("injected")
        assert fr.logs == ["hello"]


class TestBacktestContextIntegration:
    def test_context_owns_a_fill_recorder(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._fr, FillRecorder)

    def test_legacy_append_routes_through_recorder(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._fills.append(_fill())
        ctx._log_messages.append("MARGIN REJECTED: test")
        assert len(ctx._fr.fills) == 1
        assert ctx._fr.logs == ["MARGIN REJECTED: test"]

    def test_all_fills_property_reads_from_recorder(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._fills.append(_fill())
        # Public `all_fills` property still works.
        assert ctx.all_fills == [_fill()]

    def test_log_messages_property_reads_from_recorder(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._log_messages.append("xyz")
        assert ctx.log_messages == ["xyz"]


class TestFillRecorderDoesNotLeakAcrossInstances:
    def test_two_contexts_have_independent_recorders(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000)
        b = BacktestContext(initial_capital=2_000)
        a._fills.append(_fill())
        a._log_messages.append("a-only")
        assert b._fills == []
        assert b._log_messages == []
        assert a._fr is not b._fr
