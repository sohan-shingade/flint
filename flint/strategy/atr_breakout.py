"""ATR channel breakout strategy.

Trend-following: buy when price breaks above SMA + N*ATR,
sell when price drops below SMA - N*ATR. Uses ATR-based dynamic
channels that adapt to volatility.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class ATRBreakoutStrategy(Strategy):
    """Break out of ATR-based channels around the SMA."""

    def __init__(self, period: int = 20, atr_period: int = 14,
                 multiplier: float = 2.0) -> None:
        self.period = period
        self.atr_period = atr_period
        self.multiplier = multiplier

    @property
    def name(self) -> str:
        return f"ATRBreakout({self.period}, atr={self.atr_period}, x{self.multiplier})"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "atr_period": {"type": "int", "low": 7, "high": 28, "default": 14},
            "multiplier": {"type": "float", "low": 1.0, "high": 4.0, "default": 2.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        min_needed = max(self.period, self.atr_period + 1)
        if len(history) < min_needed:
            return Signal.HOLD

        # Compute SMA of close prices
        closes = np.array([c.close for c in history[-self.period:]])
        sma = float(np.mean(closes))

        # Compute ATR: average of true range over atr_period
        atr_window = history[-(self.atr_period + 1):]
        true_ranges = []
        for i in range(1, len(atr_window)):
            prev_close = atr_window[i - 1].close
            curr = atr_window[i]
            tr = max(
                curr.high - curr.low,
                abs(curr.high - prev_close),
                abs(curr.low - prev_close),
            )
            true_ranges.append(tr)
        atr = float(np.mean(true_ranges))

        upper_channel = sma + self.multiplier * atr
        lower_channel = sma - self.multiplier * atr
        price = candle.close

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if price > upper_channel:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.LONG, size)
                        stop = price - self.multiplier * atr
                        ctx.stop_order(candle.market, Side.SHORT, size, stop)
                elif price < lower_channel:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                # Exit when price returns inside the channel
                if lower_channel <= price <= upper_channel:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
            return Signal.HOLD

        # v1 fallback
        if price > upper_channel:
            return Signal.BUY
        elif price < lower_channel:
            return Signal.SELL
        return Signal.HOLD
