"""The monetary type and the one conversion into it (§5 numeric policy).

Accumulating quantities — cash, fees, funding, realized PnL — are ``Decimal``,
never float. Float accumulation drifts over a long run and, worse, makes the
Rust parity contract (§19.4) impossible: accumulators must match to a documented
tolerance and accumulation order is specified, so the Python engine commits to
``Decimal`` *before* it is written. Prices and instantaneous mark-to-market math
stay float (they do not accumulate) — this module is only for the money that
does.

``money()`` is the single sanctioned way to turn an input into the monetary
type. It routes through ``str`` so ``money(0.03)`` is ``Decimal("0.03")`` rather
than the exact-but-noisy binary expansion ``Decimal(0.03)`` would give — keeping
event payloads and golden-test assertions readable while staying inside the §19.4
tolerance.
"""

from __future__ import annotations

from decimal import Decimal

Money = Decimal

ZERO: Money = Decimal("0")


def money(value: float | int | str | Decimal) -> Money:
    """Convert ``value`` to the monetary ``Decimal`` type via its string form."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
