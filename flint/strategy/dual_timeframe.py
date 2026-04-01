"""Dual-timeframe strategy — uses higher timeframe trend + lower timeframe entry.

Approximates multi-timeframe analysis using a longer lookback for trend
and a shorter lookback for entry signals. In v0.1 style (Signal-based),
this demonstrates multi-timeframe thinking within a single candle feed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class DualTimeframeStrategy(Strategy):
    """Trend on long lookback, entry on short lookback."""

    def __init__(self, trend_period: int = 50, entry_period: int = 10,
                 entry_threshold: float = 0.02):
        self.trend_period = trend_period
        self.entry_period = entry_period
        self.entry_threshold = entry_threshold

    @property
    def name(self) -> str:
        return f"DualTF({self.trend_period}/{self.entry_period})"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "trend_period": {"type": "int", "low": 30, "high": 100, "default": 50},
            "entry_period": {"type": "int", "low": 5, "high": 20, "default": 10},
            "entry_threshold": {"type": "float", "low": 0.005, "high": 0.05, "default": 0.02},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) < self.trend_period:
            return Signal.HOLD

        # Higher timeframe: trend direction via long SMA slope
        long_closes = np.array([c.close for c in history[-self.trend_period:]])
        long_sma = float(np.mean(long_closes))
        long_sma_prev = float(np.mean(long_closes[:-1]))
        trend_up = long_sma > long_sma_prev

        # Lower timeframe: entry signal via short-term momentum
        short_closes = [c.close for c in history[-self.entry_period:]]
        short_return = (short_closes[-1] - short_closes[0]) / short_closes[0] if short_closes[0] else 0

        # Only enter in trend direction with momentum confirmation
        if trend_up and short_return > self.entry_threshold:
            return Signal.BUY
        elif not trend_up and short_return < -self.entry_threshold:
            return Signal.SELL

        return Signal.HOLD
