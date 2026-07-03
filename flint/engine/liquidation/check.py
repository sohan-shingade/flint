"""Liquidation trigger + prices — mark-based, size-tiered maintenance (§6.5).

The *trigger* is evaluated on the **mark** price (never the candle close) and
**after** funding has settled this bar — a funding receipt can rescue a position
a close-price check would have wrongly liquidated (§6.1). A position is liquidated
when the equity backing it falls below its maintenance requirement::

    maintenance = maint_frac(notional) * notional        # size-tiered (§6.5)
    liquidated  = equity < maintenance

``equity`` is instantaneous mark-to-market (cash + unrealized PnL), so it is
float; the cash *inside* it is the ``Decimal`` accumulator (§5). These are the
pure primitives; the loop composes them into the cross-pool cascade and isolated
close-whole logic and applies the post-trigger loss (the 2/3-maintenance backstop
— HL has no clearance fee, D14). The size-tiered ``maint_frac`` and the exact HL
liquidation-price formula live on the venue's ``LiquidationSpec``.
"""

from __future__ import annotations

from flint.core.models import Position
from flint.venues import LiquidationSpec


def maintenance_requirement(
    position: Position, mark_price: float, maint_frac: float
) -> float:
    """The maintenance margin the position must keep, at a flat ``maint_frac``."""
    return maint_frac * position.notional(mark_price)


def tiered_maintenance(
    position: Position, mark_price: float, spec: LiquidationSpec
) -> float:
    """The size-tiered maintenance margin at ``mark_price`` (§6.5, D14).

    Reads the maintenance fraction for the position's *notional* off the venue's
    tier table (larger notional → lower max leverage → higher fraction), then
    applies it to the notional at the current mark.
    """
    notional = position.notional(mark_price)
    return spec.maint_frac(notional) * notional


def liquidation_price(
    position: Position, backing_cash: float, maint_frac: float
) -> float:
    """The mark at which ``position`` hits maintenance — the HL formula (§6.5).

    Solving ``equity == maintenance`` for the mark gives, for signed side ``s``::

        liq = (entry*size - s*backing) / (size * (1 - s*maint_frac))

    (For a long this is ``(entry*size - cash)/(size*(1-m))``; the §6.5 golden:
    ``(1000-50)/(10*0.975) = 97.44``.) ``backing_cash`` is the collateral behind
    the position — pool cash for cross, ``isolated_margin`` for isolated.
    """
    s = position.side.sign
    denom = position.size * (1.0 - s * maint_frac)
    if position.size <= 0 or denom == 0:
        return position.entry_price
    return (position.entry_price * position.size - s * backing_cash) / denom


def bankruptcy_price(position: Position, cash: float) -> float:
    """The price at which the position's margin is exactly zero (§6.5).

    For a long: ``entry - margin/size``; for a short: ``entry + margin/size``.
    ``cash`` is the free collateral backing the position. The liquidation event
    records it so the tearsheet can show how far the backstop close ran toward it.
    """
    if position.size <= 0:
        return position.entry_price
    return position.entry_price - position.side.sign * (cash / position.size)


def is_liquidated(equity: float, maintenance: float) -> bool:
    """True iff ``equity`` has fallen below the maintenance requirement."""
    return equity < maintenance
