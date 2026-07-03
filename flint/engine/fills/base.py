"""The ``FillModel`` seam — one interface, tiered implementations (§6.3).

Both backtest and paper route every order through a ``FillModel``: it turns an
``Order`` plus the market context at the fill's effective time into a ``Fill``
(or ``None`` = rejected). Slice 3.1 ships the seam and a single naive Tier-C
model so the per-bar loop is end-to-end runnable; slice 3.2 adds the real CLOB
tier-A/B models (effective-time latency, spread cross, book-walk VWAP, queue
position, the Hyperliquid oracle band) behind this *same* interface, so the loop
never changes when fidelity improves.

``FillContext`` carries what the model needs at the moment of execution. In 3.1
that is just the reference price and the effective timestamp; 3.2 extends it with
the order-book snapshot, latency draw, and ``ctx.rng`` — additively, never a
rewrite of the callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from flint.core.models import Fill, Order


@dataclass(frozen=True)
class FillContext:
    """What a ``FillModel`` sees at the effective time of a fill.

    ``reference_price`` is the price the fill anchors to — the next bar's open
    for a T+1 market order, or the trigger price for a resting stop/limit/TP.
    Fee rates are per-notional; the 3.1 defaults are Hyperliquid base-tier
    placeholders and get their primary-source citation + unit test in slice 3.3
    (D14).
    """

    reference_price: float
    ts: int
    taker_fee_rate: float = 0.00035
    maker_fee_rate: float = 0.0001


class FillModel(Protocol):
    """Turn an ``Order`` into a ``Fill`` at its effective time, or reject it."""

    def fill(self, order: Order, ctx: FillContext) -> Fill | None:
        """Return the resulting ``Fill``, or ``None`` if the order is rejected."""
        ...
