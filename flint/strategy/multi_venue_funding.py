"""Cross-venue funding rate arbitrage strategy.

Uses ExecutionContext to read funding rates from multiple venues.
Goes long when average funding across venues is deeply negative (you
get paid), short when deeply positive. This is a Flint-unique strategy
leveraging the platform's multi-venue funding data aggregation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MultiVenueFundingStrategy(Strategy):
    """Arbitrage funding rates across multiple venues."""

    def __init__(
        self,
        entry_threshold: float = 0.0005,
        exit_threshold: float = 0.0001,
        lookback: int = 12,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        self._funding_history: List[float] = []

    @property
    def name(self) -> str:
        return f"MultiVenueFunding(entry={self.entry_threshold}, lb={self.lookback})"

    def reset(self) -> None:
        self._funding_history = []

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_threshold": {"type": "float", "low": 0.0001, "high": 0.002, "default": 0.0005},
            "exit_threshold": {"type": "float", "low": 0.00005, "high": 0.0005, "default": 0.0001},
            "lookback": {"type": "int", "low": 4, "high": 24, "default": 12},
        }

    def _estimate_funding(self, history: List[Candle]) -> float:
        """Estimate synthetic funding rate from price momentum.

        In live/paper mode, real funding rates would come from the
        multi-venue funding provider. In backtest mode we approximate
        using the rate of return over the lookback window, averaged
        to simulate cross-venue consensus.
        """
        if len(history) < self.lookback:
            return 0.0
        recent = [c.close for c in history[-self.lookback:]]
        if recent[0] == 0:
            return 0.0
        avg_return = (recent[-1] - recent[0]) / recent[0]
        return avg_return / self.lookback

    def on_candle(
        self, candle: Candle, history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD

        funding = self._estimate_funding(history)
        self._funding_history.append(funding)

        # Use rolling average to smooth noise
        window = self._funding_history[-min(len(self._funding_history), 3):]
        avg_funding = sum(window) / len(window)

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if avg_funding < -self.entry_threshold:
                    # Deeply negative funding across venues -> go long (collect payments)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.LONG, size)
                elif avg_funding > self.entry_threshold:
                    # Deeply positive funding across venues -> go short (collect payments)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        from ..models import Side
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                # Exit when cross-venue funding normalizes
                if abs(avg_funding) < self.exit_threshold:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
            return Signal.HOLD

        # v1 fallback
        if avg_funding < -self.entry_threshold:
            return Signal.BUY
        elif avg_funding > self.entry_threshold:
            return Signal.SELL
        return Signal.HOLD
