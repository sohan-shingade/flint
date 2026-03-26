export const MACD_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class MACDStrategy(Strategy):
    """MACD Crossover — buy when MACD crosses above signal, sell when below.

    Uses v2 execution: stop-loss, impact checks, position sizing.
    """

    def __init__(self, fast=12, slow=26, signal=9, stop_pct=3.0):
        self.fast, self.slow, self.signal = fast, slow, signal
        self.stop_pct = stop_pct / 100

    @property
    def name(self): return f"MACD({self.fast}/{self.slow}/{self.signal})"

    def _ema(self, data, period):
        ema = [data[0]]
        m = 2 / (period + 1)
        for x in data[1:]: ema.append(x * m + ema[-1] * (1 - m))
        return ema

    def on_candle(self, candle, history, ctx=None):
        if len(history) < self.slow + self.signal:
            return Signal.HOLD
        if ctx is None:
            return Signal.HOLD

        closes = [c.close for c in history]
        fast_ema = self._ema(closes, self.fast)
        slow_ema = self._ema(closes, self.slow)
        macd = [f - s for f, s in zip(fast_ema, slow_ema)]
        signal_line = self._ema(macd[-self.signal*2:], self.signal)
        if len(signal_line) < 2:
            return Signal.HOLD

        bullish_cross = macd[-1] > signal_line[-1] and macd[-2] <= signal_line[-2]
        bearish_cross = macd[-1] < signal_line[-1] and macd[-2] >= signal_line[-2]

        if bullish_cross and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.LONG, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                ctx.stop_order(candle.market, Side.SHORT, size,
                               candle.close * (1 - self.stop_pct))

        elif bearish_cross and ctx.positions:
            ctx.close_position(candle.market)
            ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self): pass

    @classmethod
    def parameters(cls):
        return {"fast": {"type": "int", "low": 8, "high": 20, "default": 12},
                "slow": {"type": "int", "low": 20, "high": 40, "default": 26},
                "signal": {"type": "int", "low": 5, "high": 15, "default": 9},
                "stop_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0}}
`
