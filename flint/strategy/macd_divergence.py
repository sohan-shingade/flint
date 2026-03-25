"""MACD signal crossover strategy.

Classic MACD: buy when MACD line crosses above the signal line,
sell when it crosses below. Trend-following with momentum confirmation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MACDDivergenceStrategy(Strategy):
    """Trade MACD / signal line crossovers."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        if fast >= slow:
            raise ValueError("fast must be < slow")
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self._fast_ema: float = 0.0
        self._slow_ema: float = 0.0
        self._signal_ema: float = 0.0
        self._prev_histogram: float = 0.0
        self._initialized: bool = False
        self._macd_history: List[float] = []

    @property
    def name(self) -> str:
        return f"MACD({self.fast}/{self.slow}/{self.signal})"

    def reset(self) -> None:
        self._fast_ema = 0.0
        self._slow_ema = 0.0
        self._signal_ema = 0.0
        self._prev_histogram = 0.0
        self._initialized = False
        self._macd_history = []

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "fast": {"type": "int", "low": 5, "high": 20, "default": 12},
            "slow": {"type": "int", "low": 15, "high": 50, "default": 26},
            "signal": {"type": "int", "low": 5, "high": 15, "default": 9},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        price = candle.close

        if not self._initialized:
            if len(history) < self.slow:
                return Signal.HOLD
            # Seed EMAs with SMA
            closes = [c.close for c in history[-self.slow:]]
            self._fast_ema = sum(closes[-self.fast:]) / self.fast
            self._slow_ema = sum(closes) / self.slow
            self._initialized = True
            # Need signal periods of MACD history before generating signals
            macd_val = self._fast_ema - self._slow_ema
            self._macd_history.append(macd_val)
            self._signal_ema = macd_val
            self._prev_histogram = 0.0
            return Signal.HOLD

        fast_mult = 2 / (self.fast + 1)
        slow_mult = 2 / (self.slow + 1)
        self._fast_ema = price * fast_mult + self._fast_ema * (1 - fast_mult)
        self._slow_ema = price * slow_mult + self._slow_ema * (1 - slow_mult)

        macd_val = self._fast_ema - self._slow_ema
        self._macd_history.append(macd_val)
        if len(self._macd_history) > self.signal * 2:
            self._macd_history = self._macd_history[-self.signal:]

        if len(self._macd_history) < self.signal:
            return Signal.HOLD

        # Seed signal EMA once we have enough MACD values
        if len(self._macd_history) == self.signal:
            self._signal_ema = sum(self._macd_history) / self.signal
            self._prev_histogram = macd_val - self._signal_ema
            return Signal.HOLD

        signal_mult = 2 / (self.signal + 1)
        self._signal_ema = macd_val * signal_mult + self._signal_ema * (1 - signal_mult)

        histogram = macd_val - self._signal_ema
        prev = self._prev_histogram
        self._prev_histogram = histogram

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if prev <= 0 and histogram > 0:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.LONG, size)
                elif prev >= 0 and histogram < 0:
                    size = (ctx.account.cash * 0.9) / price
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                # Exit on opposite crossover
                if prev <= 0 and histogram > 0:
                    ctx.close_position(candle.market)
                elif prev >= 0 and histogram < 0:
                    ctx.close_position(candle.market)
            return Signal.HOLD

        # v1 fallback
        if prev <= 0 and histogram > 0:
            return Signal.BUY
        elif prev >= 0 and histogram < 0:
            return Signal.SELL
        return Signal.HOLD
