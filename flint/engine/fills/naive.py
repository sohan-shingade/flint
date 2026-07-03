"""``NaiveFillModel`` — a Tier-C placeholder fill for loop-mechanics tests (§6.3).

Fills fully at the reference price, taker fee, fidelity tier ``"C"``, inventing no
depth, slippage, or partials. It exists so tests of loop *mechanics* (T+1 timing,
routing) can pin a trivially predictable price; the honest production model is
``ClobFillModel``, whose Tier-C branch adds the real parametric spread + square-
root impact. Market orders and placement-bar limits are taker (§6.3).
"""

from __future__ import annotations

from flint.core.models import Fill, Order

from ..money import money
from .base import FillContext, FillModel, FillResult


class NaiveFillModel(FillModel):
    """Full fill at the reference price, taker fee, fidelity tier C."""

    def fill(self, order: Order, ctx: FillContext) -> FillResult | None:
        if order.size <= 0:
            return None
        price = ctx.reference_price
        if price <= 0:
            return None
        fee = float(money(price) * money(order.size) * money(ctx.taker_fee_rate))
        fill = Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=order.size,
            fee=fee,
            ts=ctx.effective_ts or ctx.ts,
            client_order_id=order.client_order_id,
            is_partial=False,
            slippage_bps=0.0,
            venue=order.venue,
            liquidity="taker",
            fidelity_tier="C",
        )
        return FillResult(fill=fill)
