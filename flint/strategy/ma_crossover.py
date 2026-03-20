"""Simple moving-average crossover strategy."""
from __future__ import annotations

from typing import List

import numpy as np

from ..models import Candle, Signal
from .base import Strategy


class MACrossoverStrategy(Strategy):
    """Go long when fast SMA crosses above slow SMA, exit when it crosses below."""

    def __init__(self, fast_period: int = 10, slow_period: int = 30) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._prev_fast: float = 0.0
        self._prev_slow: float = 0.0

    @property
    def name(self) -> str:
        return f"MA-Crossover({self.fast_period}/{self.slow_period})"

    def reset(self) -> None:
        self._prev_fast = 0.0
        self._prev_slow = 0.0

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.slow_period:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.slow_period :]])
        fast_ma = float(np.mean(closes[-self.fast_period :]))
        slow_ma = float(np.mean(closes))

        signal = Signal.HOLD
        if self._prev_fast != 0.0 and self._prev_slow != 0.0:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal
