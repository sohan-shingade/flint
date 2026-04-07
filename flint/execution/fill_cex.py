"""CEX fill models for Binance, OKX, and Bybit.

Each model walks the L2 orderbook for a volume-weighted average fill price,
falling back to synthetic depth when no real book is available.

Bybit overrides fill_market with IOC semantics: the walk is capped at a
1% price band from candle.close, returning a partial fill if the band is
exhausted before the full order size is consumed.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import VENUE_PROFILES, generate_synthetic_book


class CexFillModel(FillModel):
    """Base CEX fill model.  Walks L2 orderbook or falls back to synthetic depth.

    Args:
        venue: Name of the CEX venue (must exist in VENUE_PROFILES).
        taker_fee_bps: Taker fee in basis points applied to the fill notional.
    """

    def __init__(self, venue: str, taker_fee_bps: float) -> None:
        self._venue = venue
        self._taker_fee_pct = taker_fee_bps / 10_000
        self._profile = VENUE_PROFILES.get(venue)
        self._current_book: Optional[OrderbookSnapshot] = None

    # ------------------------------------------------------------------
    # FillModel interface
    # ------------------------------------------------------------------

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        """Store the current orderbook snapshot (called by the engine each bar)."""
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a market order, walking the book or using synthetic depth."""
        if self._current_book is not None and order.market == self._current_book.market:
            levels = (
                self._current_book.asks
                if order.side == Side.LONG
                else self._current_book.bids
            )
        else:
            # Generate synthetic orderbook from venue depth profile.
            book = self._synthetic_book(candle)
            levels = book.asks if order.side == Side.LONG else book.bids

        return self._walk_book(order, candle, list(levels))

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a limit order using simple price-cross logic."""
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None

        fee = fill_price * order.size * self._taker_fee_pct
        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=fee,
            ts=candle.ts,
            order_id=order.order_id,
            venue=self._venue,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _synthetic_book(self, candle: Candle) -> OrderbookSnapshot:
        """Build a synthetic book from the venue depth profile."""
        profile = self._profile or next(iter(VENUE_PROFILES.values()))
        return generate_synthetic_book(
            mid_price=candle.close,
            profile=profile,
            market=candle.market,
            ts=candle.ts,
        )

    def _walk_book(
        self,
        order: Order,
        candle: Candle,
        levels: list,
        price_cap: Optional[float] = None,
    ) -> Optional[Fill]:
        """Walk orderbook levels accumulating a volume-weighted average fill.

        Args:
            order: The order to fill.
            candle: Current bar (used for timestamp and fallback price).
            levels: Ordered list of OrderbookLevel to walk (ask side for buys,
                    bid side for sells).
            price_cap: Optional upper (for buys) or lower (for sells) price
                       boundary — levels beyond the cap are ignored (IOC band).

        Returns:
            A Fill with the VWAP price and actual filled size, or None if no
            liquidity is available.
        """
        remaining = order.size
        total_cost = 0.0
        filled = 0.0

        for level in levels:
            # Enforce price cap (IOC band) if provided.
            if price_cap is not None:
                if order.side == Side.LONG and level.price > price_cap:
                    break
                if order.side == Side.SHORT and level.price < price_cap:
                    break

            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break

        if filled <= 0:
            return None

        avg_price = total_cost / filled
        fee = avg_price * filled * self._taker_fee_pct

        return Fill(
            market=order.market,
            side=order.side,
            price=avg_price,
            size=filled,
            fee=fee,
            ts=candle.ts,
            order_id=order.order_id,
            venue=self._venue,
            is_partial=(filled < order.size),
        )


class BinanceFillModel(CexFillModel):
    """Binance perpetuals fill model.

    Uses standard L2 book walk with a 5 bps taker fee.
    """

    def __init__(self) -> None:
        super().__init__(venue="binance", taker_fee_bps=5.0)


class OkxFillModel(CexFillModel):
    """OKX perpetuals fill model.

    Uses standard L2 book walk with a 5 bps taker fee.
    """

    def __init__(self) -> None:
        super().__init__(venue="okx", taker_fee_bps=5.0)


class BybitFillModel(CexFillModel):
    """Bybit perpetuals fill model with IOC price-band semantics.

    Market orders are treated as Immediate-Or-Cancel capped at 1% from
    candle.close.  If the book within that band cannot fill the full
    order size, only the available liquidity is returned as a partial fill.
    """

    def __init__(self) -> None:
        super().__init__(venue="bybit", taker_fee_bps=5.5)

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill market order capped at a 1% price band (IOC conversion)."""
        band_pct = 0.01  # 1 % IOC band

        if self._current_book is not None and order.market == self._current_book.market:
            levels = list(
                self._current_book.asks
                if order.side == Side.LONG
                else self._current_book.bids
            )
        else:
            book = self._synthetic_book(candle)
            levels = list(book.asks if order.side == Side.LONG else book.bids)

        if order.side == Side.LONG:
            price_cap = candle.close * (1 + band_pct)
        else:
            price_cap = candle.close * (1 - band_pct)

        return self._walk_book(order, candle, levels, price_cap=price_cap)
