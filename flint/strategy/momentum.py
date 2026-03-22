"""Momentum / Rate-of-Change strategy."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MomentumStrategy(Strategy):
    """Buy when price is up X% over lookback period, sell when down X%."""

    def __init__(self, lookback: int = 24, threshold_pct: float = 5.0) -> None:
        self.lookback = lookback
        self.threshold_pct = threshold_pct

    @property
    def name(self) -> str:
        return f"Momentum({self.lookback}, {self.threshold_pct}%)"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "lookback": {"type": "int", "low": 6, "high": 72, "default": 24},
            "threshold_pct": {"type": "float", "low": 1.0, "high": 15.0, "default": 5.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) <= self.lookback:
            return Signal.HOLD

        past_price = history[-self.lookback].close
        current_price = candle.close
        pct_change = ((current_price - past_price) / past_price) * 100

        if pct_change >= self.threshold_pct:
            return Signal.BUY
        elif pct_change <= -self.threshold_pct:
            return Signal.SELL
        return Signal.HOLD
