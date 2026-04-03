export const FUNDING_ARB_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List, Optional

class FundingArb(Strategy):
    """Cross-Venue Funding Rate Arbitrage.

    Delta-neutral strategy: long on low-funding venue, short on high-funding venue.
    Uses ctx.get_funding_by_venue() — needs funding data from 2+ venues.
    Download funding data in Data Explorer first!
    """
    def __init__(self, min_spread_bps=5.0, exit_spread_bps=1.0,
                 max_hold_hours=24, position_size_usd=1000.0):
        self.min_spread_bps = min_spread_bps
        self.exit_spread_bps = exit_spread_bps
        self.max_hold_hours = max_hold_hours
        self.position_size_usd = position_size_usd
        self.entry_ts = 0
        self.long_venue = ""
        self.short_venue = ""

    @property
    def name(self): return "FundingArb"
    def reset(self):
        self.entry_ts = 0
        self.long_venue = ""
        self.short_venue = ""

    @classmethod
    def parameters(cls):
        return {
            "min_spread_bps": {"type": "float", "low": 3.0, "high": 20.0, "default": 5.0},
            "exit_spread_bps": {"type": "float", "low": 0.5, "high": 5.0, "default": 1.0},
            "max_hold_hours": {"type": "int", "low": 4, "high": 72, "default": 24},
            "position_size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        venue_data = ctx.get_funding_by_venue(candle.market, lookback=24)
        if len(venue_data) < 2:
            return Signal.HOLD

        venues = ["drift", "hyperliquid"]
        rates = {}
        for v in venues:
            r = venue_data.get(v, [])
            if r:
                rates[v] = r[-1][1]
        if len(rates) < 2:
            return Signal.HOLD

        if self.entry_ts > 0:
            # Check exit
            if (candle.ts - self.entry_ts) / 3600 >= self.max_hold_hours:
                ctx.close_position(candle.market, venue=self.long_venue)
                ctx.close_position(candle.market, venue=self.short_venue)
                self.entry_ts = 0
                return Signal.HOLD
            spread = abs(rates.get(self.short_venue, 0) - rates.get(self.long_venue, 0)) * 10000
            if spread < self.exit_spread_bps:
                ctx.close_position(candle.market, venue=self.long_venue)
                ctx.close_position(candle.market, venue=self.short_venue)
                self.entry_ts = 0
        else:
            # Check entry
            spread = abs(rates[venues[0]] - rates[venues[1]]) * 10000
            if spread >= self.min_spread_bps:
                low_v = min(rates, key=rates.get)
                high_v = max(rates, key=rates.get)
                size = self.position_size_usd / candle.close if candle.close > 0 else 0
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size, venue=low_v)
                    ctx.market_order(candle.market, Side.SHORT, size, venue=high_v)
                    self.entry_ts = candle.ts
                    self.long_venue = low_v
                    self.short_venue = high_v
        return Signal.HOLD
`
