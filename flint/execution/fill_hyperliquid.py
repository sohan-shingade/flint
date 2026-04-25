"""Hyperliquid-specific fill model.

Hyperliquid operates as a pure CLOB (Central Limit Order Book). Market orders
walk the orderbook levels for volume-weighted fill prices. When the book is
exhausted, the HLP (Hyperliquid Liquidity Provider) vault backstops the
remainder at a worse price determined by the remaining size relative to
candle volume.

Falls back to a synthetic orderbook generated from Hyperliquid's depth profile
when no live orderbook snapshot is available.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import VENUE_PROFILES, generate_synthetic_book


class HyperliquidFillModel(FillModel):
    """Walk the Hyperliquid CLOB; HLP vault backstops any unfilled remainder.

    Args:
        impact_coefficient: Scales HLP backstop price impact as a fraction
            of (remaining / candle_volume). Default 0.005 (0.5% per unit ratio).
    """

    def __init__(self, impact_coefficient: float = 0.005) -> None:
        self._impact_k = impact_coefficient
        self._current_book: Optional[OrderbookSnapshot] = None
        self._profile = VENUE_PROFILES["hyperliquid"]

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        """Called by the engine/context to update the current orderbook state."""
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a market order by walking CLOB levels, then HLP backstop."""
        book = (
            self._current_book
            if self._current_book is not None and order.market == self._current_book.market
            else generate_synthetic_book(
                candle.close, self._profile, market=candle.market, ts=candle.ts
            )
        )

        levels = book.asks if order.side == Side.LONG else book.bids
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

        # HLP vault backstops whatever the CLOB could not fill.
        if remaining > 0:
            hlp_price = self._hlp_price(remaining, candle, order.side)
            total_cost += remaining * hlp_price
            filled += remaining

        if filled <= 0:
            return None

        return Fill(
            order_id=order.order_id,
            market=order.market,
            side=order.side,
            price=total_cost / filled,
            size=filled,
            fee=0.0,
            ts=candle.ts,
            venue="hyperliquid",
        )

    def _hlp_price(self, remaining: float, candle: Candle, side: Side) -> float:
        """Compute HLP backstop price with impact proportional to remaining size."""
        pct = self._impact_k * (remaining / max(candle.volume, 1.0))
        if side == Side.LONG:
            return candle.close * (1 + pct)
        return candle.close * (1 - pct)

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a limit order if the candle's range crosses the limit price."""
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(
                order_id=order.order_id,
                market=order.market,
                side=order.side,
                price=order.price,
                size=order.size,
                fee=0.0,
                ts=candle.ts,
                venue="hyperliquid",
            )
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(
                order_id=order.order_id,
                market=order.market,
                side=order.side,
                price=order.price,
                size=order.size,
                fee=0.0,
                ts=candle.ts,
                venue="hyperliquid",
            )
        return None
