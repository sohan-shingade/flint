"""VWAP reversion strategy.

Buys when price drops X% below VWAP, sells when it returns to VWAP.
Works best in range-bound markets where price oscillates around fair value.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class VWAPReversionStrategy(Strategy):
    """Buy below VWAP, sell when price reverts back toward VWAP."""

    def __init__(self, period: int = 20, entry_pct: float = 2.0,
                 exit_pct: float = 0.5) -> None:
        self.period = period
        self.entry_pct = entry_pct
        self.exit_pct = exit_pct

    @property
    def name(self) -> str:
        return f"VWAPReversion({self.period}, {self.entry_pct}%)"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "exit_pct": {"type": "float", "low": 0.1, "high": 2.0, "default": 0.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) < self.period:
            return Signal.HOLD

        window = history[-self.period:]
        typical_prices = np.array([(c.high + c.low + c.close) / 3 for c in window])
        volumes = np.array([c.volume for c in window])

        total_volume = float(np.sum(volumes))
        if total_volume == 0:
            return Signal.HOLD

        vwap = float(np.sum(typical_prices * volumes) / total_volume)
        price = candle.close
        deviation_pct = ((price - vwap) / vwap) * 100

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if deviation_pct <= -self.entry_pct:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.LONG, size)
                elif deviation_pct >= self.entry_pct:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                if abs(deviation_pct) <= self.exit_pct:
                    ctx.close_position(candle.market)
            return Signal.HOLD

        # v1 fallback
        if deviation_pct <= -self.entry_pct:
            return Signal.BUY
        elif deviation_pct >= self.entry_pct:
            return Signal.SELL
        return Signal.HOLD
