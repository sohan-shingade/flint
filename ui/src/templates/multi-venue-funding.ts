export const MULTI_VENUE_FUNDING_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class MultiVenueFunding(Strategy):
    """Cross-venue funding arbitrage — unique to Flint.

    Reads REAL funding rates from multiple venues via ctx.get_funding_by_venue().
    Enters when the majority of venues agree funding is skewed.

    IMPORTANT: Download funding data for multiple venues in Data Explorer first!
    Select Drift + Hyperliquid + OKX (or more) before downloading.
    Returns HOLD when no real funding data is available.
    """

    def __init__(self, entry_threshold=0.00003, exit_threshold=0.000005,
                 lookback=12, min_venues=2, stop_pct=0.05):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        self.min_venues = min_venues
        self.stop_pct = stop_pct

    @property
    def name(self): return f"MultiVenueFunding(th={self.entry_threshold})"

    def _get_avg_funding(self, candle, ctx):
        """Get cross-venue average funding rate."""
        if ctx is None:
            return None

        # Try multi-venue first
        venue_data = ctx.get_funding_by_venue(candle.market, lookback=self.lookback)
        if venue_data and len(venue_data) >= self.min_venues:
            venue_avgs = []
            for venue, rates in venue_data.items():
                if rates:
                    recent = [r for _, r in rates[-self.lookback:]]
                    if recent:
                        venue_avgs.append(sum(recent) / len(recent))
            if len(venue_avgs) >= self.min_venues:
                return sum(venue_avgs) / len(venue_avgs)

        # Single venue fallback
        rates = ctx.get_funding_rates(candle.market, lookback=self.lookback)
        if len(rates) >= 3:
            return sum(r for _, r in rates) / len(rates)

        return None

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        avg = self._get_avg_funding(candle, ctx)
        if avg is None:
            return Signal.HOLD

        pos = ctx.position(candle.market)
        if pos is None:
            if avg < -self.entry_threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_pct))
            elif avg > self.entry_threshold:
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close * (1 + self.stop_pct))
        else:
            if abs(avg) < self.exit_threshold:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self): pass

    @classmethod
    def parameters(cls):
        return {"entry_threshold": {"type": "float", "low": 0.00001, "high": 0.0001, "default": 0.00003},
                "exit_threshold": {"type": "float", "low": 0.000001, "high": 0.00002, "default": 0.000005},
                "lookback": {"type": "int", "low": 6, "high": 24, "default": 12},
                "min_venues": {"type": "int", "low": 1, "high": 5, "default": 2},
                "stop_pct": {"type": "float", "low": 0.02, "high": 0.10, "default": 0.05}}
`
