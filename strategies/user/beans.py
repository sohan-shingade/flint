from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MACrossover(Strategy):
    """Moving Average Crossover — trend following.

    Buys when fast SMA crosses above slow SMA (golden cross).
    Sells when fast crosses below slow (death cross).
    """
    def __init__(self, fast_period=10, slow_period=30):
        self.fast = fast_period
        self.slow = slow_period
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
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.slow:
            return Signal.HOLD
        closes = [c.close for c in history[-self.slow:]]
        fast_ma = float(np.mean(closes[-self.fast:]))
        slow_ma = float(np.mean(closes))
        signal = Signal.HOLD
        if self._prev_fast is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal

    def reset(self) -> None:
        self._prev_fast = None
        self._prev_slow = None
