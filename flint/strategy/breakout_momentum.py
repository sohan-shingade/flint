"""Breakout momentum strategy with volume confirmation and trailing stop.

Enters on breakout above N-period high with volume spike.
Uses ATR-based trailing stop for exits.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class BreakoutMomentumStrategy(Strategy):
    """Enter on high breakout with volume, trail with ATR stop."""

    def __init__(self, lookback: int = 20, volume_mult: float = 1.5,
                 atr_mult: float = 2.0):
        self.lookback = lookback
        self.volume_mult = volume_mult
        self.atr_mult = atr_mult

    @property
    def name(self) -> str:
        return f"Breakout({self.lookback}, vol={self.volume_mult}x)"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "volume_mult": {"type": "float", "low": 1.0, "high": 3.0, "default": 1.5},
            "atr_mult": {"type": "float", "low": 1.0, "high": 4.0, "default": 2.0},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) < self.lookback + 1:
            return Signal.HOLD

        window = history[-(self.lookback + 1):-1]
        highs = [c.high for c in window]
        volumes = [c.volume for c in window]

        highest = max(highs)
        avg_volume = np.mean(volumes) if volumes else 0

        # Breakout: price above N-period high with volume spike
        if candle.close > highest and candle.volume > avg_volume * self.volume_mult:
            return Signal.BUY

        # Exit: price drops below ATR trailing level
        if len(history) >= self.lookback:
            tr_values = []
            for i in range(-self.lookback, 0):
                c = history[i]
                prev_close = history[i - 1].close if i > -len(history) else c.open
                tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
                tr_values.append(tr)
            atr = np.mean(tr_values) if tr_values else 0
            recent_high = max(c.high for c in history[-self.lookback:])
            trailing_stop = recent_high - atr * self.atr_mult
            if candle.close < trailing_stop:
                return Signal.SELL

        return Signal.HOLD
