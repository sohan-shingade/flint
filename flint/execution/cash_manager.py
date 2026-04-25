"""CashManager — owns cash, allocator, and running cost counters.

D-2.1.b Step 2 (Wave 2) of breaking up `BacktestContext`. After Step 1
extracted position state, this slice pulls the cash + running-counter
ownership into a dedicated class. BacktestContext now composes it and
exposes `self._cash` / `self._total_fees` / `self._total_tx_costs` /
`self._total_funding` as property read/write aliases, so the 20+
existing call sites with compound assignments (`self._cash -= x`,
`self._total_funding += p`) keep working unchanged.

Steps 3–7 will migrate call sites to explicit `self._cm.debit(...)`
/ `self._cm.add_fee(...)` calls and then retire the aliases.
"""
from __future__ import annotations


class CashManager:
    """Single-ledger cash + counters. Allocator-aware when multi-venue."""

    __slots__ = (
        "_cash",
        "_allocator",
        "total_fees",
        "total_tx_costs",
        "total_funding",
    )

    def __init__(self, initial_capital: float, allocator=None) -> None:
        self._cash: float = initial_capital
        self._allocator = allocator
        if allocator is not None:
            # Sync the headline cash number to the allocator's aggregate.
            self._cash = allocator.total_cash
        self.total_fees: float = 0.0
        self.total_tx_costs: float = 0.0
        self.total_funding: float = 0.0

    # --- cash read/write ----------------------------------------------------

    @property
    def cash(self) -> float:
        return self._cash

    @cash.setter
    def cash(self, value: float) -> None:
        self._cash = value

    @property
    def allocator(self):
        return self._allocator

    # --- allocator-aware debit/credit --------------------------------------

    def debit(self, amount: float, venue: str = "default") -> bool:
        """Deduct cash. Returns True on success; False when allocator
        rejects (insufficient per-venue balance)."""
        if self._allocator is not None:
            ok = self._allocator.debit(venue, amount)
            self._cash = self._allocator.total_cash
            return bool(ok)
        self._cash -= amount
        return True

    def credit(self, amount: float, venue: str = "default") -> None:
        """Add cash; routes through allocator when present. `track_pnl`
        is called so the allocator's per-venue PnL ledger stays in sync
        (matches pre-extraction behavior)."""
        if self._allocator is not None:
            self._allocator.credit(venue, amount)
            self._allocator.track_pnl(venue, amount)
            self._cash = self._allocator.total_cash
        else:
            self._cash += amount

    # --- counters ----------------------------------------------------------

    def add_fee(self, amount: float) -> None:
        self.total_fees += amount

    def add_tx_cost(self, amount: float) -> None:
        self.total_tx_costs += amount

    def add_funding(self, amount: float) -> None:
        self.total_funding += amount

    # --- venue balance helpers --------------------------------------------

    def available(self, venue: str = "default") -> float:
        if self._allocator is not None:
            return self._allocator.available(venue)
        return self._cash

    def balances(self) -> dict:
        if self._allocator is not None:
            return self._allocator.balances
        return {"default": self._cash}
