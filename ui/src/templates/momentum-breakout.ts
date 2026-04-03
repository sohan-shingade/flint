export const MOMENTUM_BREAKOUT_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List, Optional

class MomentumBreakout(Strategy):
    """Breakout strategy with optional oracle confirmation.

    Buys when price breaks above N-bar high.
    Sells when price breaks below N-bar low.
    Oracle confirmation (Drift only): blocks entry if Pyth disagrees.
    """
    def __init__(self, lookback=20, oracle_confirm=True):
        self.lookback = lookback
        self.oracle_confirm = oracle_confirm

    @property
    def name(self): return f"MomentumBreakout({self.lookback})"
    def reset(self): pass

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback + 1:
            return Signal.HOLD
        window = history[-(self.lookback + 1):-1]
        high = max(c.high for c in window)
        low = min(c.low for c in window)

        if candle.close > high:
            if self.oracle_confirm and ctx:
                oracle = ctx.get_oracle_price(candle.market)
                if oracle and oracle[0] < candle.close:
                    return Signal.HOLD
            return Signal.BUY
        if candle.close < low:
            if self.oracle_confirm and ctx:
                oracle = ctx.get_oracle_price(candle.market)
                if oracle and oracle[0] > candle.close:
                    return Signal.HOLD
            return Signal.SELL
        return Signal.HOLD
`
