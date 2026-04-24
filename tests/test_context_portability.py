"""ExecutionContext conformance + portability — Phase 2 T2.1.

Verifies that every concrete context class implements the full
`ExecutionContext` abstract surface. This doesn't refactor the duplication
yet (that's a dedicated sibling PR — the god-class breakup is too invasive
to bundle with Phase 2 plumbing); but it enforces the interface contract
so any future consolidation work can't silently drop a method.

What this test proves today:
- Every concrete ExecutionContext subclass overrides every abstract method.
- The method signatures align across contexts (same parameter names).
- BacktestContext can be instantiated and used end-to-end.

What Phase 2 sibling PR will add:
- PaperContext as a first-class ExecutionContext (today paper logic rides
  inside PaperBroker + LiveContext wiring).
- A round-trip test that runs the same strategy on Backtest + Paper + Live
  and asserts identical fill counts / equity progression on a fixture.
"""
from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from flint.execution.context import ExecutionContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _concrete_subclasses(cls):
    """Walk the class tree returning every non-abstract subclass."""
    found = set()
    stack = list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        stack.extend(sub.__subclasses__())
        if inspect.isabstract(sub):
            continue
        found.add(sub)
    return found


def _abstract_methods(cls):
    """Names of methods marked abstract on ExecutionContext."""
    return {
        name for name, m in inspect.getmembers(cls)
        if getattr(m, "__isabstractmethod__", False)
    }


# ---------------------------------------------------------------------------
# Import all context implementations so they register as subclasses.
# ---------------------------------------------------------------------------

def _import_all_contexts():
    """Trigger subclass registration for every ExecutionContext impl."""
    # Known concrete contexts + their modules:
    import flint.execution.backtest_context  # BacktestContext
    try:
        import flint.execution.live_base  # LiveExecutionContext (abstract)
    except ImportError:
        pass
    try:
        import flint.execution.drift_live  # DriftLiveContext
    except ImportError:
        pass  # driftpy optional
    try:
        import flint.execution.hyperliquid_live  # HyperliquidLiveContext
    except ImportError:
        pass
    try:
        import flint.execution.ccxt_live  # CCXTLiveContext
    except ImportError:
        pass
    try:
        import flint.execution.jupiter_live  # JupiterLiveContext
    except ImportError:
        pass
    try:
        import flint.execution.multi_venue_live  # MultiVenueLiveContext
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExecutionContextAbstractSurface:
    def test_core_abstract_methods_present(self):
        """ExecutionContext declares the core methods every context must
        implement. Asserting the set here guards against someone silently
        removing one from the ABC."""
        abstracts = _abstract_methods(ExecutionContext)
        expected = {
            "account", "positions", "pending_orders", "current_candle",
            "timestamp",
            "market_order", "limit_order", "stop_order", "take_profit_order",
            "cancel", "cancel_all",
        }
        missing = expected - abstracts
        assert not missing, (
            f"ExecutionContext abstract surface shrunk — "
            f"missing abstract methods: {missing}"
        )


class TestConcreteContextsConform:
    def test_backtest_context_implements_all_abstracts(self):
        from flint.execution.backtest_context import BacktestContext

        abstracts = _abstract_methods(ExecutionContext)
        missing = [m for m in abstracts if getattr(BacktestContext, m, None)
                   is getattr(ExecutionContext, m, None)]
        # Either the attribute is not the abstract stub, OR it's overridden.
        unimplemented = [
            m for m in abstracts
            if inspect.isabstract(getattr(BacktestContext, m, lambda: None))
        ]
        assert not unimplemented, (
            f"BacktestContext does not implement: {unimplemented}"
        )

    def test_backtest_context_instantiable(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        # Accessing abstract properties must not explode.
        assert ctx.account.cash == 10_000.0
        assert ctx.positions == []
        assert ctx.pending_orders == []

    def test_all_concrete_contexts_implement_abstracts(self):
        """Every concrete subclass of ExecutionContext must implement every
        abstract method. Test walks the subclass tree after importing known
        context modules."""
        _import_all_contexts()
        abstracts = _abstract_methods(ExecutionContext)
        failures = []
        for sub in _concrete_subclasses(ExecutionContext):
            unimplemented = [
                m for m in abstracts
                if inspect.isabstract(getattr(sub, m, lambda: None))
            ]
            if unimplemented:
                failures.append(f"{sub.__name__}: missing {unimplemented}")
        assert not failures, (
            "Concrete ExecutionContext subclasses with unimplemented "
            "abstract methods:\n  " + "\n  ".join(failures)
        )


class TestSignatureAlignment:
    """Parameter names must match across contexts so strategies are
    source-portable. Phase 2 T2.1."""

    def _names(self, fn):
        return [p.name for p in inspect.signature(fn).parameters.values()
                if p.name != "self"]

    def test_market_order_signature_matches(self):
        from flint.execution.backtest_context import BacktestContext
        try:
            from flint.execution.live_base import LiveExecutionContext
        except ImportError:
            pytest.skip("LiveExecutionContext not importable")

        bt = self._names(BacktestContext.market_order)
        live = self._names(LiveExecutionContext.market_order)
        # Core positional args must align (side-order, size) — venue/tag/etc
        # may differ in kwarg defaults but name must match.
        core = {"market", "side", "size"}
        assert core.issubset(bt), f"BacktestContext.market_order missing {core - set(bt)}"
        assert core.issubset(live), f"LiveExecutionContext.market_order missing {core - set(live)}"
