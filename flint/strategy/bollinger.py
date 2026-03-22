"""Bollinger Bands mean-reversion strategy."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


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

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "num_std": {"type": "float", "low": 1.0, "high": 3.5, "default": 2.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None) -> Signal:
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
