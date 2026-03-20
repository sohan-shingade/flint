"""Bollinger Bands mean-reversion strategy."""
from __future__ import annotations

from typing import List

import numpy as np

from ..models import Candle, Signal
from .base import Strategy


class BollingerStrategy(Strategy):
    """Buy when price touches lower band, sell when it touches upper band."""

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        self.period = period
        self.num_std = num_std

    @property
    def name(self) -> str:
        return f"Bollinger({self.period}, {self.num_std})"

    def reset(self) -> None:
        pass

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.period:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.period:]])
        sma = float(np.mean(closes))
        std = float(np.std(closes))

        upper = sma + self.num_std * std
        lower = sma - self.num_std * std
        price = candle.close

        if price <= lower:
            return Signal.BUY
        elif price >= upper:
            return Signal.SELL
        return Signal.HOLD
