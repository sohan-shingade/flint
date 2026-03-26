"""ImpactStage — computes fill price using orderbook, sqrt model, or flat bps."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..models import Candle, Order, OrderbookSnapshot, Side


@dataclass
class ImpactResult:
    """Output of the impact stage."""
    fill_price: float
    available_size: float
    impact_bps: float
    tier: str  # "orderbook", "sqrt", "fallback"


class ImpactStage:
    """Determines fill price via three-tier fallback.

    Tier 1: Walk orderbook levels (when snapshot exists).
    Tier 2: Square-root participation model (when bar volume exists).
    Tier 3: Flat basis-point penalty (last resort).
    """

    def __init__(
        self,
        impact_coefficient: float = 0.005,
        fallback_bps: float = 5.0,
    ):
        self._k = impact_coefficient
        self._fallback_bps = fallback_bps

    def compute(
        self,
        order: Order,
        candle: Candle,
        book: Optional[OrderbookSnapshot],
    ) -> ImpactResult:
        """Compute fill price and available liquidity for an order."""
        # Tier 1: Orderbook walk
        if book is not None and order.market == book.market:
            levels = book.asks if order.side == Side.LONG else book.bids
            if levels:
                return self._walk_book(order, candle, levels)

        # Tier 2: Sqrt participation model
        if candle.volume > 0:
            return self._sqrt_model(order, candle)

        # Tier 3: Flat bps fallback
        return self._flat_fallback(order, candle)

    def _walk_book(self, order, candle, levels):
        remaining = order.size
        total_cost = 0.0
        filled = 0.0
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            if candle.volume > 0:
                return self._sqrt_model(order, candle)
            return self._flat_fallback(order, candle)
        avg_price = total_cost / filled
        impact_bps = abs(avg_price - candle.close) / candle.close * 10_000 if candle.close > 0 else 0.0
        return ImpactResult(fill_price=avg_price, available_size=filled,
                            impact_bps=impact_bps, tier="orderbook")

    def _sqrt_model(self, order, candle):
        participation = order.size / candle.volume
        impact_pct = self._k * math.sqrt(participation)
        impact_bps = impact_pct * 10_000
        if order.side == Side.LONG:
            fill_price = candle.close * (1 + impact_pct)
        else:
            fill_price = candle.close * (1 - impact_pct)
        return ImpactResult(fill_price=fill_price, available_size=order.size,
                            impact_bps=impact_bps, tier="sqrt")

    def _flat_fallback(self, order, candle):
        pct = self._fallback_bps / 10_000
        if order.side == Side.LONG:
            fill_price = candle.close * (1 + pct)
        else:
            fill_price = candle.close * (1 - pct)
        return ImpactResult(fill_price=fill_price, available_size=order.size,
                            impact_bps=self._fallback_bps, tier="fallback")
