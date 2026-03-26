export const ATR_BREAKOUT_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class ATRBreakout(Strategy):
    """ATR Channel Breakout — buy above upper channel, sell below lower.

    Uses v2 execution: stop at opposite channel, impact checks, position sizing.
    """

    def __init__(self, period=20, atr_period=14, multiplier=2.0):
        self.period, self.atr_period, self.multiplier = period, atr_period, multiplier

    @property
    def name(self): return f"ATRBreakout({self.period}, x{self.multiplier})"

    def on_candle(self, candle, history, ctx=None):
        n = max(self.period, self.atr_period)
        if len(history) < n + 1:
            return Signal.HOLD
        if ctx is None:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.period:]])
        sma = float(np.mean(closes))
        # ATR
        trs = []
        for i in range(-self.atr_period, 0):
            h, l, pc = history[i].high, history[i].low, history[i-1].close
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs) / len(trs)
        upper = sma + self.multiplier * atr
        lower = sma - self.multiplier * atr

        if candle.close > upper and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.LONG, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                ctx.stop_order(candle.market, Side.SHORT, size, lower)

        elif candle.close < lower and ctx.positions:
            ctx.close_position(candle.market)
            ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self): pass

    @classmethod
    def parameters(cls):
        return {"period": {"type": "int", "low": 10, "high": 50, "default": 20},
                "atr_period": {"type": "int", "low": 7, "high": 21, "default": 14},
                "multiplier": {"type": "float", "low": 1.0, "high": 4.0, "default": 2.0}}
`
