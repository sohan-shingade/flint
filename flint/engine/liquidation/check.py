"""Liquidation trigger — mark-based maintenance check (§6.5).

Slice 3.1 needs the *trigger*, evaluated on the **mark** price (never the candle
close), and evaluated **after** funding has settled this bar — because a funding
receipt can rescue a position that a close-price check would have wrongly
liquidated (§6.1). What happens *after* the trigger (distance-based liquidation
fee, bankruptcy price, cross-pool depletion ordering, size-tiered maintenance)
is slice 3.4; this module is the flat-maintenance trigger those refinements
extend.

A position is liquidated when the account equity backing it falls below the
maintenance requirement::

    maintenance = maint_frac * position.notional(mark)
    liquidated  = equity < maintenance

``equity`` is the venue equity (cash + unrealized PnL) — instantaneous
mark-to-market math, so it is float; the cash *inside* it is the ``Decimal``
accumulator. The maintenance fraction here is a single flat rate; slice 3.4
swaps it for the venue's size-tiered table (D14, cited + unit-tested per number).
"""

from __future__ import annotations

from flint.core.models import Position


def maintenance_requirement(
    position: Position, mark_price: float, maint_frac: float
) -> float:
    """The maintenance margin the position must keep, at ``mark_price``."""
    return maint_frac * position.notional(mark_price)


def bankruptcy_price(position: Position, cash: float) -> float:
    """The price at which the position's margin is exactly zero (§6.5).

    For a long: ``entry - margin/size``; for a short: ``entry + margin/size``.
    ``cash`` is the free collateral backing the position. Used by the liquidation
    event so slice 3.4 can price the distance-based fee between mark and here.
    """
    if position.size <= 0:
        return position.entry_price
    return position.entry_price - position.side.sign * (cash / position.size)


def is_liquidated(equity: float, maintenance: float) -> bool:
    """True iff ``equity`` has fallen below the maintenance requirement."""
    return equity < maintenance
