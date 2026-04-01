"""Cross-venue funding rate arbitrage strategy.

Uses ctx.get_funding_by_venue() to read real funding rates from multiple
venues (Drift, Hyperliquid, OKX, etc.). Compares rates across venues to
find consensus — enters when most venues agree funding is skewed.

Download funding data for multiple venues in Data Explorer before backtesting.
Without funding data, falls back to price momentum proxy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MultiVenueFundingStrategy(Strategy):
    """Cross-venue funding rate arbitrage.

    Reads real funding rates from all downloaded venues.
    Enters when the majority of venues show extreme funding,
    indicating market-wide consensus rather than venue-specific noise.
    """

    def __init__(
        self,
        entry_threshold: float = 0.00003,
        exit_threshold: float = 0.000005,
        lookback: int = 12,
        min_venues: int = 2,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        self.min_venues = min_venues
        self._has_real_data = False
        self._warned = False

    @property
    def name(self) -> str:
        return f"MultiVenueFunding(entry={self.entry_threshold}, lb={self.lookback})"

    def reset(self) -> None:
        self._has_real_data = False
        self._warned = False

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_threshold": {"type": "float", "low": 0.00001, "high": 0.0001, "default": 0.00003},
            "exit_threshold": {"type": "float", "low": 0.000001, "high": 0.00002, "default": 0.000005},
            "lookback": {"type": "int", "low": 4, "high": 24, "default": 12},
            "min_venues": {"type": "int", "low": 1, "high": 5, "default": 2},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def _get_cross_venue_signal(self, candle: Candle, history: List[Candle],
                                ctx: Optional["ExecutionContext"]) -> Optional[float]:
        """Get average funding rate across venues. Returns None if insufficient data."""
        if ctx is not None:
            venue_data = ctx.get_funding_by_venue(candle.market, lookback=self.lookback)
            if venue_data and len(venue_data) >= self.min_venues:
                # Average the most recent rate from each venue
                venue_avgs = []
                for venue, rates in venue_data.items():
                    if rates:
                        # Average last N rates for this venue
                        recent = [r for _, r in rates[-self.lookback:]]
                        if recent:
                            venue_avgs.append(sum(recent) / len(recent))
                if len(venue_avgs) >= self.min_venues:
                    self._has_real_data = True
                    return sum(venue_avgs) / len(venue_avgs)

            # Single venue fallback — still real data
            rates = ctx.get_funding_rates(candle.market, lookback=self.lookback)
            if len(rates) >= 3:
                self._has_real_data = True
                return sum(r for _, r in rates) / len(rates)

        # Price momentum proxy (least accurate)
        if ctx is not None and not self._warned:
            ctx.log("WARNING: No funding rate data found — using price momentum proxy. "
                    "Download funding data for 2+ venues in Data Explorer for accurate cross-venue signals.")
            self._warned = True
        if len(history) < self.lookback:
            return None
        recent = [c.close for c in history[-self.lookback:]]
        if recent[0] == 0:
            return None
        return ((recent[-1] - recent[0]) / recent[0]) / self.lookback

    def on_candle(
        self, candle: Candle, history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD

        avg_funding = self._get_cross_venue_signal(candle, history, ctx)
        if avg_funding is None:
            return Signal.HOLD

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if avg_funding < -self.entry_threshold:
                    # Negative funding consensus → go long (longs get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.LONG, size)
                elif avg_funding > self.entry_threshold:
                    # Positive funding consensus → go short (shorts get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                # Exit when cross-venue funding normalizes
                if abs(avg_funding) < self.exit_threshold:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
            return Signal.HOLD

        # v1 fallback
        if avg_funding < -self.entry_threshold:
            return Signal.BUY
        elif avg_funding > self.entry_threshold:
            return Signal.SELL
        return Signal.HOLD
