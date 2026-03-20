"""RSI (Relative Strength Index) mean-reversion strategy."""
from __future__ import annotations

from typing import List

import numpy as np

from ..models import Candle, Signal
from .base import Strategy


class RSIStrategy(Strategy):
    """Buy when RSI drops below oversold, sell when it rises above overbought."""

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self._prev_rsi: float = 50.0

    @property
    def name(self) -> str:
        return f"RSI({self.period}, {self.oversold}/{self.overbought})"

    def reset(self) -> None:
        self._prev_rsi = 50.0

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.period + 1:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-(self.period + 1):]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)

        signal = Signal.HOLD
        if self._prev_rsi >= self.oversold and rsi < self.oversold:
            signal = Signal.BUY
        elif self._prev_rsi <= self.overbought and rsi > self.overbought:
            signal = Signal.SELL

        self._prev_rsi = rsi
        return signal
