export const BASIS_TRADE_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List, Optional

class BasisTrade(Strategy):
    """Cross-Venue Basis Arbitrage.

    Exploits price differences for the same asset across venues.
    Long on cheaper venue, short on expensive (delta neutral).
    Uses ctx.get_candles() to find venue-tagged prices.
    """
    def __init__(self, entry_bps=30.0, exit_bps=5.0, size_usd=1000.0):
        self.entry_bps = entry_bps
        self.exit_bps = exit_bps
        self.size_usd = size_usd
        self.entry_ts = 0
        self.long_venue = ""
        self.short_venue = ""

    @property
    def name(self): return "BasisTrade"
    def reset(self):
        self.entry_ts = 0
        self.long_venue = ""
        self.short_venue = ""

    @classmethod
    def parameters(cls):
        return {
            "entry_bps": {"type": "float", "low": 10.0, "high": 100.0, "default": 30.0},
            "exit_bps": {"type": "float", "low": 2.0, "high": 20.0, "default": 5.0},
            "size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        candles = ctx.get_candles(candle.market, lookback=5)
        prices = {}
        for c in candles:
            v = getattr(c, 'venue', 'default')
            if v in ('drift', 'hyperliquid'):
                prices[v] = c.close
        if len(prices) < 2:
            return Signal.HOLD

        venues = list(prices.keys())
        diff = abs(prices[venues[0]] - prices[venues[1]])
        ref = max(prices.values())
        basis = (diff / ref) * 10000 if ref > 0 else 0

        if self.entry_ts > 0:
            if basis < self.exit_bps:
                ctx.close_position(candle.market, venue=self.long_venue)
                ctx.close_position(candle.market, venue=self.short_venue)
                self.entry_ts = 0
        else:
            if basis >= self.entry_bps:
                cheap = min(prices, key=prices.get)
                expensive = max(prices, key=prices.get)
                size = self.size_usd / prices[cheap] if prices[cheap] > 0 else 0
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size, venue=cheap)
                    ctx.market_order(candle.market, Side.SHORT, size, venue=expensive)
                    self.entry_ts = candle.ts
                    self.long_venue = cheap
                    self.short_venue = expensive
        return Signal.HOLD
`
