"""RSI + MACD confluence strategy.

Only takes trades when both RSI and MACD agree on direction.
More selective than either indicator alone, producing fewer trades
but with higher conviction.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class RSIMACDComboStrategy(Strategy):
    """Trade only when RSI and MACD both confirm the signal."""

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
    ) -> None:
        if macd_fast >= macd_slow:
            raise ValueError("macd_fast must be < macd_slow")
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        # MACD state
        self._fast_ema: float = 0.0
        self._slow_ema: float = 0.0
        self._signal_ema: float = 0.0
        self._prev_histogram: float = 0.0
        self._macd_initialized: bool = False
        self._macd_history: List[float] = []
        # RSI state
        self._prev_rsi: float = 50.0

    @property
    def name(self) -> str:
        return (
            f"RSI-MACD({self.rsi_period}, "
            f"{self.macd_fast}/{self.macd_slow}/{self.macd_signal})"
        )

    def reset(self) -> None:
        self._fast_ema = 0.0
        self._slow_ema = 0.0
        self._signal_ema = 0.0
        self._prev_histogram = 0.0
        self._macd_initialized = False
        self._macd_history = []
        self._prev_rsi = 50.0

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "rsi_period": {"type": "int", "low": 5, "high": 30, "default": 14},
            "macd_fast": {"type": "int", "low": 5, "high": 20, "default": 12},
            "macd_slow": {"type": "int", "low": 15, "high": 50, "default": 26},
            "macd_signal": {"type": "int", "low": 5, "high": 15, "default": 9},
            "rsi_oversold": {"type": "float", "low": 15, "high": 40, "default": 30},
            "rsi_overbought": {"type": "float", "low": 60, "high": 85, "default": 70},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def _compute_rsi(self, history: List[Candle]) -> float:
        """Compute RSI from recent history."""
        closes = np.array([c.close for c in history[-(self.rsi_period + 1):]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    def _update_macd(self, price: float, history: List[Candle]) -> Optional[float]:
        """Update MACD state and return histogram value, or None if not ready."""
        if not self._macd_initialized:
            if len(history) < self.macd_slow:
                return None
            closes = [c.close for c in history[-self.macd_slow:]]
            self._fast_ema = sum(closes[-self.macd_fast:]) / self.macd_fast
            self._slow_ema = sum(closes) / self.macd_slow
            self._macd_initialized = True
            macd_val = self._fast_ema - self._slow_ema
            self._macd_history.append(macd_val)
            self._signal_ema = macd_val
            self._prev_histogram = 0.0
            return None

        fast_mult = 2 / (self.macd_fast + 1)
        slow_mult = 2 / (self.macd_slow + 1)
        self._fast_ema = price * fast_mult + self._fast_ema * (1 - fast_mult)
        self._slow_ema = price * slow_mult + self._slow_ema * (1 - slow_mult)

        macd_val = self._fast_ema - self._slow_ema
        self._macd_history.append(macd_val)
        if len(self._macd_history) > self.macd_signal * 2:
            self._macd_history = self._macd_history[-self.macd_signal:]

        if len(self._macd_history) < self.macd_signal:
            return None

        if len(self._macd_history) == self.macd_signal:
            self._signal_ema = sum(self._macd_history) / self.macd_signal
            self._prev_histogram = macd_val - self._signal_ema
            return None

        signal_mult = 2 / (self.macd_signal + 1)
        self._signal_ema = macd_val * signal_mult + self._signal_ema * (1 - signal_mult)

        histogram = macd_val - self._signal_ema
        prev = self._prev_histogram
        self._prev_histogram = histogram
        return histogram if prev != 0 else None

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        min_needed = max(self.rsi_period + 1, self.macd_slow)
        if len(history) < min_needed:
            return Signal.HOLD

        # Compute RSI
        rsi = self._compute_rsi(history)
        rsi_bullish = rsi < self.rsi_oversold
        rsi_bearish = rsi > self.rsi_overbought
        self._prev_rsi = rsi

        # Update MACD
        prev_hist = self._prev_histogram
        histogram = self._update_macd(candle.close, history)
        if histogram is None:
            return Signal.HOLD

        macd_bullish = prev_hist <= 0 and histogram > 0
        macd_bearish = prev_hist >= 0 and histogram < 0

        # Confluence: both must agree
        buy_signal = rsi_bullish and macd_bullish
        sell_signal = rsi_bearish and macd_bearish

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if buy_signal:
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.LONG, size)
                elif sell_signal:
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                # Exit on opposite confluence
                if buy_signal or sell_signal:
                    ctx.close_position(candle.market)
            return Signal.HOLD

        # v1 fallback
        if buy_signal:
            return Signal.BUY
        elif sell_signal:
            return Signal.SELL
        return Signal.HOLD
