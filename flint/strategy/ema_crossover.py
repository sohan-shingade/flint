"""EMA (Exponential Moving Average) crossover strategy."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class EMACrossoverStrategy(Strategy):
    """Go long when fast EMA crosses above slow EMA, exit on cross below."""

    def __init__(self, fast_period: int = 12, slow_period: int = 26) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._fast_ema: float = 0.0
        self._slow_ema: float = 0.0
        self._prev_fast: float = 0.0
        self._prev_slow: float = 0.0
        self._initialized = False

    @property
    def name(self) -> str:
        return f"EMA-Cross({self.fast_period}/{self.slow_period})"

    def reset(self) -> None:
        self._fast_ema = 0.0
        self._slow_ema = 0.0
        self._prev_fast = 0.0
        self._prev_slow = 0.0
        self._initialized = False

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "fast_period": {"type": "int", "low": 5, "high": 50, "default": 12},
            "slow_period": {"type": "int", "low": 20, "high": 200, "default": 26},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None) -> Signal:
        price = candle.close

        if not self._initialized:
            if len(history) < self.slow_period:
                return Signal.HOLD
            # Seed EMAs with SMA
            closes = [c.close for c in history[-self.slow_period:]]
            self._fast_ema = sum(closes[-self.fast_period:]) / self.fast_period
            self._slow_ema = sum(closes) / self.slow_period
            self._initialized = True
            self._prev_fast = self._fast_ema
            self._prev_slow = self._slow_ema
            return Signal.HOLD

        fast_mult = 2 / (self.fast_period + 1)
        slow_mult = 2 / (self.slow_period + 1)

        self._prev_fast = self._fast_ema
        self._prev_slow = self._slow_ema
        self._fast_ema = price * fast_mult + self._fast_ema * (1 - fast_mult)
        self._slow_ema = price * slow_mult + self._slow_ema * (1 - slow_mult)

        if self._prev_fast <= self._prev_slow and self._fast_ema > self._slow_ema:
            return Signal.BUY
        elif self._prev_fast >= self._prev_slow and self._fast_ema < self._slow_ema:
            return Signal.SELL
        return Signal.HOLD
