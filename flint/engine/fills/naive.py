"""``NaiveFillModel`` — the Tier-C placeholder fill (§6.3).

The honest floor: with no order book, a market order fills fully at the
reference price (next-bar open) and a resting order fills at its trigger price,
paying the taker fee, recorded as fidelity tier ``"C"``. It invents no depth, no
partials, no slippage — exactly the coarse behaviour §6.3 assigns to
OHLCV-only data, and it never *pretends* to more (that is the wedge). Slice 3.2
replaces it, on the same seam, with the tier-A/B CLOB models that walk real
recorded depth.

Liquidity classification follows the Tier-C rule (§6.3): a limit that executes on
its placement bar is a taker (it almost certainly crossed); market orders are
always taker.
"""

from __future__ import annotations

from flint.core.models import Fill, Order

from ..money import money
from .base import FillContext, FillModel


class NaiveFillModel(FillModel):
    """Full fill at the reference price, taker fee, fidelity tier C."""

    def fill(self, order: Order, ctx: FillContext) -> Fill | None:
        if order.size <= 0:
            return None
        price = ctx.reference_price
        if price <= 0:
            return None
        fee = float(money(price) * money(order.size) * money(ctx.taker_fee_rate))
        return Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=order.size,
            fee=fee,
            ts=ctx.ts,
            client_order_id=order.client_order_id,
            is_partial=False,
            slippage_bps=0.0,
            venue=order.venue,
            liquidity="taker",
            fidelity_tier="C",
        )
