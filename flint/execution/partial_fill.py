"""PartialFillStage — applies time-in-force semantics to determine fill size."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Order, OrderType, TimeInForce
from .impact import ImpactResult


@dataclass
class FillDecision:
    """Output of the partial fill stage."""
    fill_size: float
    fill_price: float
    is_partial: bool
    cancelled: bool
    resting_order: Optional[Order]


class PartialFillStage:
    """Applies time-in-force semantics to determine actual fill size.

    IOC: Fill available, cancel rest.
    FOK: Fill all or nothing.
    GTC: Fill available, rest becomes a resting limit order.
    """

    def decide(self, order: Order, impact: ImpactResult) -> FillDecision:
        available = min(order.size, impact.available_size)
        tif = order.time_in_force

        if tif == TimeInForce.FOK:
            if available < order.size:
                return FillDecision(
                    fill_size=0.0, fill_price=impact.fill_price,
                    is_partial=False, cancelled=True, resting_order=None,
                )
            return FillDecision(
                fill_size=order.size, fill_price=impact.fill_price,
                is_partial=False, cancelled=False, resting_order=None,
            )

        if tif == TimeInForce.GTC:
            remainder = order.size - available
            resting = None
            if remainder > 0:
                resting = Order(
                    market=order.market, side=order.side,
                    order_type=OrderType.LIMIT,
                    size=remainder, price=impact.fill_price,
                    order_id=f"{order.order_id}-gtc",
                    ts=order.ts, venue=order.venue,
                    time_in_force=TimeInForce.GTC,
                )
            return FillDecision(
                fill_size=available, fill_price=impact.fill_price,
                is_partial=available < order.size, cancelled=False,
                resting_order=resting,
            )

        # IOC (default)
        return FillDecision(
            fill_size=available, fill_price=impact.fill_price,
            is_partial=available < order.size and available > 0,
            cancelled=False, resting_order=None,
        )
