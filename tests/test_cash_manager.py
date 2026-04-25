"""D-2.1.b Step 2 — CashManager unit tests.

Pins the contract before Steps 3–7 start migrating call sites to
explicit `self._cm.debit / add_fee / ...` calls.
"""
from __future__ import annotations

import pytest

from flint.execution.cash_manager import CashManager


class _StubAllocator:
    """Minimal shape of `flint.execution.capital.VenueAllocator`."""

    def __init__(self, balances: dict):
        self.balances = dict(balances)
        self._pnl = {}

    @property
    def total_cash(self) -> float:
        return sum(self.balances.values())

    def available(self, venue: str) -> float:
        return self.balances.get(venue, 0.0)

    def debit(self, venue: str, amount: float) -> bool:
        if self.balances.get(venue, 0.0) < amount:
            return False
        self.balances[venue] -= amount
        return True

    def credit(self, venue: str, amount: float) -> None:
        self.balances[venue] = self.balances.get(venue, 0.0) + amount

    def track_pnl(self, venue: str, amount: float) -> None:
        self._pnl[venue] = self._pnl.get(venue, 0.0) + amount


class TestInit:
    def test_default_without_allocator(self):
        cm = CashManager(initial_capital=10_000.0)
        assert cm.cash == 10_000.0
        assert cm.allocator is None
        assert cm.total_fees == 0.0
        assert cm.total_tx_costs == 0.0
        assert cm.total_funding == 0.0

    def test_with_allocator_syncs_initial_cash(self):
        alloc = _StubAllocator({"drift": 6_000.0, "hyperliquid": 4_000.0})
        cm = CashManager(initial_capital=1_000.0, allocator=alloc)
        # cash is overridden to allocator.total_cash, not the constructor arg
        assert cm.cash == 10_000.0


class TestDebitCredit:
    def test_debit_without_allocator_reduces_cash(self):
        cm = CashManager(10_000.0)
        ok = cm.debit(250.0)
        assert ok is True
        assert cm.cash == 9_750.0

    def test_credit_without_allocator_adds_cash(self):
        cm = CashManager(10_000.0)
        cm.credit(500.0)
        assert cm.cash == 10_500.0

    def test_debit_via_allocator_returns_false_on_rejection(self):
        alloc = _StubAllocator({"drift": 100.0})
        cm = CashManager(100.0, allocator=alloc)
        ok = cm.debit(500.0, venue="drift")
        assert ok is False
        # cash stays synced to allocator (which didn't change)
        assert cm.cash == 100.0

    def test_debit_via_allocator_updates_cash(self):
        alloc = _StubAllocator({"drift": 100.0})
        cm = CashManager(100.0, allocator=alloc)
        assert cm.debit(30.0, venue="drift") is True
        assert cm.cash == 70.0

    def test_credit_via_allocator_tracks_pnl(self):
        alloc = _StubAllocator({"drift": 100.0})
        cm = CashManager(100.0, allocator=alloc)
        cm.credit(25.0, venue="drift")
        assert cm.cash == 125.0
        assert alloc._pnl["drift"] == 25.0


class TestCounters:
    def test_add_fee_accumulates(self):
        cm = CashManager(10_000.0)
        cm.add_fee(1.5)
        cm.add_fee(3.25)
        assert cm.total_fees == 4.75

    def test_compound_assignment_via_attribute(self):
        """Sanity check that `cm.total_fees += x` (used via the
        BacktestContext property alias) still works on the __slots__
        class."""
        cm = CashManager(10_000.0)
        cm.total_fees += 10.0
        cm.total_tx_costs += 5.0
        cm.total_funding += 2.5
        assert cm.total_fees == 10.0
        assert cm.total_tx_costs == 5.0
        assert cm.total_funding == 2.5


class TestAvailableBalances:
    def test_without_allocator_defaults_to_global_cash(self):
        cm = CashManager(500.0)
        assert cm.available("anything") == 500.0
        assert cm.balances() == {"default": 500.0}

    def test_with_allocator_returns_per_venue(self):
        alloc = _StubAllocator({"drift": 600.0, "hyperliquid": 400.0})
        cm = CashManager(1.0, allocator=alloc)
        assert cm.available("drift") == 600.0
        assert cm.available("hyperliquid") == 400.0
        assert cm.balances() == {"drift": 600.0, "hyperliquid": 400.0}


class TestBacktestContextIntegration:
    def test_context_owns_a_cash_manager(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000.0)
        assert isinstance(ctx._cm, CashManager)

    def test_legacy_cash_assignment_routes_through_manager(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000.0)
        ctx._cash -= 750.0
        assert ctx._cm.cash == 9_250.0
        ctx._cash = 0.0
        assert ctx._cm.cash == 0.0

    def test_legacy_counter_compound_assignment_routes_through_manager(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000.0)
        ctx._total_fees += 7.5
        ctx._total_tx_costs += 1.25
        ctx._total_funding += 0.5
        assert ctx._cm.total_fees == 7.5
        assert ctx._cm.total_tx_costs == 1.25
        assert ctx._cm.total_funding == 0.5

    def test_legacy_allocator_property(self):
        from flint.execution.backtest_context import BacktestContext
        alloc = _StubAllocator({"drift": 5_000.0})
        ctx = BacktestContext(initial_capital=1.0, capital_allocator=alloc)
        assert ctx._allocator is alloc
        # And cash is synced to the allocator total, matching the
        # pre-extraction __init__ behavior.
        assert ctx._cash == pytest.approx(5_000.0)


class TestCashManagerDoesNotLeakAcrossInstances:
    """Each BacktestContext must own its own CashManager — no class-level
    state pollution."""

    def test_two_contexts_have_independent_cash(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000.0)
        b = BacktestContext(initial_capital=2_000.0)
        a._cash -= 100.0
        assert a._cash == 900.0
        assert b._cash == 2_000.0
        assert a._cm is not b._cm
