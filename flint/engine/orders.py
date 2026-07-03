"""The persisted order state machine (§6.2).

Both backtest and paper route every order through the *same* shape, and an order
is not a fire-and-forget instruction — it moves through a state machine that is
**persisted** (as events, folded back by ``replay.fold``) so paper survives a
restart and live (v2) can reconcile against the exchange after a dropped
connection. The legal transitions are the §6.2 diagram, verbatim::

    pending ─▶ placed ─▶ partial ─▶ filled
       │         │  │        │  └────────┐
       └▶rejected │  ├▶ filled           └▶ cancelled
                  ├▶ rejected
                  └▶ cancelled

``pending`` is transient — it exists only between a client submitting an id and
the engine accepting it, and is never itself an event (the first persisted state
is ``placed`` = ORDER_PLACED). ``client_order_id`` is the client-generated
idempotency key: the same id submitted twice is recognized and never
double-filled (the paper-reconnect guard, §6.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from flint.core.models import OrderType, Side

# A fill is treated as complete when the unfilled remainder is within this many
# base units of zero — float lot arithmetic never lands exactly on 0.
_FILL_EPSILON = 1e-9


class OrderStatus(StrEnum):
    """The states an order occupies over its life (§6.2)."""

    PENDING = "pending"  # submitted, not yet accepted (transient, never persisted)
    PLACED = "placed"  # accepted onto the shared fill path (ORDER_PLACED)
    PARTIAL = "partial"  # some size filled, remainder still working
    FILLED = "filled"  # fully filled (terminal)
    REJECTED = "rejected"  # a pre-trade / fill check failed (terminal)
    CANCELLED = "cancelled"  # cancelled by IOC remainder, close, or run end (terminal)


# The only legal transitions — anything else is an engine bug and raises. A
# ``partial`` may take further partial fills (a resting limit clearing across
# several bars), so it self-loops.
_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.PLACED, OrderStatus.REJECTED}),
    OrderStatus.PLACED: frozenset(
        {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.PARTIAL: frozenset(
        {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

TERMINAL: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
)


class OrderTransitionError(Exception):
    """An illegal state transition was attempted — always an engine bug (§6.2)."""


@dataclass
class OrderRecord:
    """One order's live position in the state machine (§6.2).

    ``size`` is the original requested base size; ``filled_size`` accumulates as
    the order fills (possibly across several bars for a resting limit). The record
    is what ``replay.fold`` reconstructs from the ORDER_PLACED / FILL /
    ORDER_REJECTED / ORDER_CANCELLED events, so a folded machine is identical to
    the live one.
    """

    client_order_id: str
    market: str
    venue: str
    side: Side
    type: OrderType
    size: float
    price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    reason: str = ""  # why it was rejected / cancelled (attribution)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def remaining(self) -> float:
        return self.size - self.filled_size

    def transition(self, to: OrderStatus, *, reason: str = "") -> None:
        """Advance to ``to``, rejecting any transition the §6.2 machine forbids."""
        if to not in _ALLOWED[self.status]:
            raise OrderTransitionError(
                f"illegal transition {self.status} -> {to} "
                f"for order {self.client_order_id}"
            )
        self.status = to
        if reason:
            self.reason = reason

    def apply_fill(self, size: float) -> None:
        """Record ``size`` base units filled, advancing placed/partial → filled.

        Filling a terminal order is an engine bug (a cancelled/rejected order must
        never fill) and raises, so a replay that double-counts a fill is caught.
        """
        if self.is_terminal:
            raise OrderTransitionError(
                f"cannot fill {self.status} order {self.client_order_id}"
            )
        self.filled_size += size
        self.status = (
            OrderStatus.FILLED
            if self.remaining <= _FILL_EPSILON
            else OrderStatus.PARTIAL
        )
