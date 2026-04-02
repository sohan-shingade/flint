"""BasisTradeStrategy — cross-venue basis arbitrage.

Compares the latest close prices for the same market on two (or more)
venues. When the price spread (basis) exceeds ``entry_basis_bps``, the
strategy longs the cheaper venue and shorts the more expensive venue.
It exits when the basis converges below ``exit_basis_bps`` or when
``max_hold_hours`` elapses.

Venue-tagged candles are retrieved via ``ctx.get_candles(market)`` which
is expected to return ``List[Candle]`` where each candle has a ``venue``
attribute.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

logger = logging.getLogger("flint.strategy.basis_trade")


class BasisTradeStrategy(Strategy):
    """Cross-venue basis arbitrage strategy (v2).

    Parameters
    ----------
    entry_basis_bps:
        Minimum basis (in bps) required to open a position.
    exit_basis_bps:
        Basis below which an existing position is closed.
    max_hold_hours:
        Maximum time to hold a position before force-closing.
    position_size_usd:
        Notional size (in USD) to use on each leg.
    venues:
        List of venue names to compare. Requires at least 2 venues.
    candle_resolution_s:
        Candle granularity hint.
    """

    def __init__(
        self,
        entry_basis_bps: float = 30.0,
        exit_basis_bps: float = 5.0,
        max_hold_hours: int = 12,
        position_size_usd: float = 1000.0,
        venues: Optional[List[str]] = None,
        candle_resolution_s: int = 3600,
    ) -> None:
        self.entry_basis_bps = entry_basis_bps
        self.exit_basis_bps = exit_basis_bps
        self.max_hold_hours = max_hold_hours
        self.position_size_usd = position_size_usd
        self._venues = venues if venues is not None else ["drift", "hyperliquid"]
        self.candle_resolution_s = candle_resolution_s
        self._entry_ts: int = 0
        self._long_venue: str = ""
        self._short_venue: str = ""

    @property
    def name(self) -> str:
        return "basis_trade"

    def reset(self) -> None:
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_basis_bps": {"type": "float", "low": 5.0, "high": 200.0, "default": 30.0},
            "exit_basis_bps": {"type": "float", "low": 0.5, "high": 50.0, "default": 5.0},
            "max_hold_hours": {"type": "int", "low": 1, "high": 72, "default": 12},
            "position_size_usd": {"type": "float", "low": 100.0, "high": 50000.0, "default": 1000.0},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
        }

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx=None,
    ) -> Signal:
        if ctx is None:
            return Signal.HOLD

        venue_prices = self._get_venue_prices(candle, ctx)
        if len(venue_prices) < 2:
            return Signal.HOLD

        has_position = self._entry_ts > 0
        if has_position:
            return self._check_exit(candle, ctx, venue_prices)
        else:
            return self._check_entry(candle, ctx, venue_prices)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_venue_prices(self, candle: Candle, ctx) -> Dict[str, float]:
        """Build a dict of {venue: latest_close} from ctx.get_candles()."""
        venue_prices: Dict[str, float] = {}
        try:
            all_candles: List[Candle] = ctx.get_candles(candle.market)
        except Exception:
            return venue_prices

        if not all_candles:
            return venue_prices

        # Take the latest candle per venue
        for c in all_candles:
            venue = getattr(c, "venue", "default")
            if venue not in venue_prices or c.ts > _ts_for_venue(all_candles, venue):
                venue_prices[venue] = c.close

        # Filter to configured venues only
        return {v: p for v, p in venue_prices.items() if v in self._venues}

    def _check_entry(self, candle: Candle, ctx, venue_prices: Dict[str, float]) -> Signal:
        venues = list(venue_prices.keys())
        best_spread_bps = 0.0
        long_venue = ""
        short_venue = ""

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                va, vb = venues[i], venues[j]
                pa, pb = venue_prices[va], venue_prices[vb]
                mid = (pa + pb) / 2.0
                if mid <= 0:
                    continue
                spread_bps = abs(pa - pb) / mid * 10_000

                if spread_bps > best_spread_bps:
                    best_spread_bps = spread_bps
                    if pa < pb:
                        long_venue, short_venue = va, vb
                    else:
                        long_venue, short_venue = vb, va

        if best_spread_bps < self.entry_basis_bps:
            return Signal.HOLD

        long_price = venue_prices[long_venue]
        if long_price <= 0:
            return Signal.HOLD

        size = self.position_size_usd / long_price

        ctx.market_order(candle.market, Side.LONG, size, venue=long_venue)
        ctx.market_order(candle.market, Side.SHORT, size, venue=short_venue)

        self._entry_ts = candle.ts
        self._long_venue = long_venue
        self._short_venue = short_venue

        logger.info(
            "Entry: long %s / short %s basis=%.2fbps ts=%d",
            long_venue, short_venue, best_spread_bps, candle.ts,
        )
        return Signal.HOLD

    def _check_exit(self, candle: Candle, ctx, venue_prices: Dict[str, float]) -> Signal:
        hold_hours = (candle.ts - self._entry_ts) / 3600

        if hold_hours >= self.max_hold_hours:
            self._close_both(candle, ctx, "max hold")
            return Signal.HOLD

        long_price = venue_prices.get(self._long_venue, 0.0)
        short_price = venue_prices.get(self._short_venue, 0.0)

        if long_price > 0 and short_price > 0:
            mid = (long_price + short_price) / 2.0
            current_basis_bps = abs(long_price - short_price) / mid * 10_000
            if current_basis_bps <= self.exit_basis_bps:
                self._close_both(candle, ctx, f"basis converged ({current_basis_bps:.2f}bps)")

        return Signal.HOLD

    def _close_both(self, candle: Candle, ctx, reason: str) -> None:
        logger.info("Exit (%s): closing %s / %s at ts=%d", reason, self._long_venue, self._short_venue, candle.ts)
        ctx.close_position(candle.market, venue=self._long_venue)
        ctx.close_position(candle.market, venue=self._short_venue)
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _ts_for_venue(candles: List[Candle], venue: str) -> int:
    """Return the highest ts for a given venue in a candle list."""
    return max((c.ts for c in candles if getattr(c, "venue", "default") == venue), default=0)
