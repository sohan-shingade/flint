"""OrderQueue — owns the two order queues used by BacktestContext.

D-2.1.b Step 4 (Wave 2). Pulls the pending-order list (limit / stop /
take-profit, queued for matching against later bars) and the
market-order queue (orders placed during this bar's strategy callback,
processed at bar close) into one owner. BacktestContext composes it
and delegates the queue mechanics; the order-placement methods
(`market_order`, `limit_order`, ...) stay on BacktestContext because
they need the risk/margin/allocator surface.

The Python BacktestContext has multiple call sites that both append
to and reassign `_pending_orders` (e.g. `cancel_all` filters by
market, `process_pending_orders` swaps in a remaining list). The
class therefore exposes a `pending` property with a setter so the
existing reassignment idioms keep working through the alias.
"""
from __future__ import annotations

from typing import List, Optional

from ..models import Order


# Hard cap on resting orders to bound memory in adversarial strategies.
# Mirrors the pre-extraction `if len(self._pending_orders) >= 100` check.
DEFAULT_PENDING_CAP = 100


class OrderQueue:
    __slots__ = ("_pending", "_market_queue", "pending_cap")

    def __init__(self, pending_cap: int = DEFAULT_PENDING_CAP) -> None:
        self._pending: List[Order] = []
        self._market_queue: List[Order] = []
        self.pending_cap = pending_cap

    # --- pending (resting limit/stop/TP) -----------------------------------

    @property
    def pending(self) -> List[Order]:
        return self._pending

    @pending.setter
    def pending(self, new_list: List[Order]) -> None:
        self._pending = new_list

    def add_pending(self, order: Order) -> bool:
        """Append to the resting queue, returning False when capped."""
        if len(self._pending) >= self.pending_cap:
            return False
        self._pending.append(order)
        return True

    def cancel(self, order_id: str) -> bool:
        for i, o in enumerate(self._pending):
            if o.order_id == order_id:
                self._pending.pop(i)
                return True
        return False

    def cancel_all(self, market: Optional[str] = None) -> int:
        if market is None:
            count = len(self._pending)
            self._pending.clear()
            return count
        before = len(self._pending)
        self._pending = [o for o in self._pending if o.market != market]
        return before - len(self._pending)

    def is_full(self) -> bool:
        return len(self._pending) >= self.pending_cap

    def snapshot(self) -> List[Order]:
        """Read-only copy — what `BacktestContext.pending_orders` returns."""
        return list(self._pending)

    # --- market (this-bar) -------------------------------------------------

    @property
    def market_queue(self) -> List[Order]:
        return self._market_queue

    @market_queue.setter
    def market_queue(self, new_list: List[Order]) -> None:
        self._market_queue = new_list

    def add_market(self, order: Order) -> None:
        self._market_queue.append(order)

    def drain_market(self) -> List[Order]:
        """Atomically swap out the market queue and return the old list.
        Caller takes ownership; new orders submitted during draining go
        into the fresh empty queue."""
        out = self._market_queue
        self._market_queue = []
        return out
