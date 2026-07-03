"""Funding settlement — the payment a single settlement moves (§6.4).

Slice 3.1 needs exactly one honest piece of the funding engine: given a
settlement (a *final* ``FundingRate``) and the oracle price at the settlement
second, compute the signed payment to a position and settle it in ``Decimal``.
The full engine (predicted/final split, per-venue ``rate_cap_hourly``, the
hard-gate on missing coverage, per-bar sub-interval settlement) lands in slice
3.3 — this module is the settlement primitive it will build on.

Sign convention (§6.4): a **positive** rate means longs pay shorts. So the
payment *to* a position is::

    payment = -side.sign * size * oracle_price * rate

A long (sign +1) with a positive rate pays (negative payment); a long with a
negative rate receives. Funding is priced on the **oracle/index** price, never
mark — that separation is the whole point of the two prices (§2.4).
"""

from __future__ import annotations

from flint.core.models import Position

from ..money import Money, money


def settlement_payment(
    position: Position, rate_hourly: float, oracle_price: float
) -> Money:
    """The signed ``Decimal`` funding payment to ``position`` for one settlement.

    Positive = the position receives; negative = the position pays. Computed in
    ``Decimal`` because it is a monetary accumulator (§5) — the golden funding
    example (§6.4) depends on the value being exact, not float-noisy.
    """
    return (
        money(-position.side.sign)
        * money(position.size)
        * money(oracle_price)
        * money(rate_hourly)
    )
