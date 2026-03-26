export const MA_CROSSOVER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class MACrossover(Strategy):
    """Moving Average Crossover — trend following.

    Buys when fast SMA crosses above slow SMA (golden cross).
    Sells when fast crosses below slow (death cross).
    Uses v2 execution: stop-loss, impact checks, position sizing.
    """
    def __init__(self, fast_period=10, slow_period=30, stop_pct=3.0):
        self.fast = fast_period
        self.slow = slow_period
        self.stop_pct = stop_pct / 100
        self._prev_fast = None
        self._prev_slow = None

    @property
    def name(self) -> str:
        return f"MA-Crossover({self.fast}/{self.slow})"

    @classmethod
    def parameters(cls):
        return {
            "fast_period": {"type": "int", "low": 5, "high": 50, "default": 10},
            "slow_period": {"type": "int", "low": 20, "high": 200, "default": 30},
            "stop_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.slow:
            return Signal.HOLD
        closes = [c.close for c in history[-self.slow:]]
        fast_ma = float(np.mean(closes[-self.fast:]))
        slow_ma = float(np.mean(closes))

        if ctx is None or self._prev_fast is None:
            self._prev_fast = fast_ma
            self._prev_slow = slow_ma
            return Signal.HOLD

        golden_cross = self._prev_fast <= self._prev_slow and fast_ma > slow_ma
        death_cross = self._prev_fast >= self._prev_slow and fast_ma < slow_ma
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

        if golden_cross and not ctx.positions:
            size = (ctx.account.cash * 0.9) / candle.close
            impact = ctx.get_impact_price(candle.market, Side.LONG, size)
            if impact and abs(impact - candle.close) / candle.close > 0.002:
                ctx.log(f"Skip entry: impact too high ({abs(impact - candle.close)/candle.close*10000:.0f}bps)")
                return Signal.HOLD
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                ctx.stop_order(candle.market, Side.SHORT, size,
                               candle.close * (1 - self.stop_pct))

        elif death_cross and ctx.positions:
            ctx.close_position(candle.market)
            ctx.cancel_all(candle.market)

        return Signal.HOLD

    def reset(self) -> None:
        self._prev_fast = None
        self._prev_slow = None
`
