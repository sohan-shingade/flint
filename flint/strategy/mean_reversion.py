"""Z-score mean reversion strategy with stop-loss.

Buys when price deviates below the rolling mean by N standard deviations,
sells when it reverts. Uses ctx for stop-loss placement.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MeanReversionStrategy(Strategy):
    """Buy oversold, sell overbought based on Z-score."""

    def __init__(self, period: int = 20, entry_z: float = 2.0,
                 exit_z: float = 0.5, stop_loss_pct: float = 0.05):
        self.period = period
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_loss_pct = stop_loss_pct

    @property
    def name(self) -> str:
        return f"MeanReversion({self.period}, z={self.entry_z})"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_z": {"type": "float", "low": 1.0, "high": 3.5, "default": 2.0},
            "exit_z": {"type": "float", "low": 0.0, "high": 1.5, "default": 0.5},
            "stop_loss_pct": {"type": "float", "low": 0.02, "high": 0.10, "default": 0.05},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) < self.period:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.period:]])
        mean = float(np.mean(closes))
        std = float(np.std(closes))
        if std == 0:
            return Signal.HOLD

        z_score = (candle.close - mean) / std

        if z_score <= -self.entry_z:
            return Signal.BUY
        elif z_score >= self.entry_z:
            return Signal.SELL
        elif abs(z_score) <= self.exit_z:
            return Signal.SELL  # exit position near mean
        return Signal.HOLD
