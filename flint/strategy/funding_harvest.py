"""Funding rate harvest strategy.

Goes long when funding is deeply negative (shorts are paying longs),
goes short when funding is deeply positive (longs are paying shorts).
Uses the ExecutionContext for order placement and stop-loss.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class FundingHarvestStrategy(Strategy):
    """Harvest funding rate payments by taking the opposite side."""

    def __init__(
        self,
        entry_threshold: float = 0.001,
        exit_threshold: float = 0.0002,
        stop_loss_pct: float = 0.05,
        lookback: int = 8,
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_pct = stop_loss_pct
        self.lookback = lookback
        self._funding_rates: List[float] = []

    @property
    def name(self) -> str:
        return f"FundingHarvest({self.entry_threshold})"

    def reset(self) -> None:
        self._funding_rates = []

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_threshold": {"type": "float", "low": 0.0005, "high": 0.005, "default": 0.001},
            "exit_threshold": {"type": "float", "low": 0.0001, "high": 0.001, "default": 0.0002},
            "stop_loss_pct": {"type": "float", "low": 0.02, "high": 0.10, "default": 0.05},
            "lookback": {"type": "int", "low": 4, "high": 24, "default": 8},
        }

    def on_candle(
        self, candle: Candle, history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD

        # Simulate funding rate from price momentum as proxy
        # (in live/paper, real funding rates would come via on_funding callback)
        recent = [c.close for c in history[-self.lookback:]]
        avg_return = (recent[-1] - recent[0]) / recent[0] if recent[0] else 0
        synthetic_funding = avg_return / self.lookback  # rough proxy

        self._funding_rates.append(synthetic_funding)
        if len(self._funding_rates) < 3:
            return Signal.HOLD

        avg_funding = sum(self._funding_rates[-3:]) / 3

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if avg_funding < -self.entry_threshold:
                    # Negative funding → go long (get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, __import__("flint.models", fromlist=["Side"]).Side.LONG, size)
                        stop = candle.close * (1 - self.stop_loss_pct)
                        ctx.stop_order(candle.market, __import__("flint.models", fromlist=["Side"]).Side.SHORT, size, stop)
                elif avg_funding > self.entry_threshold:
                    # Positive funding → go short (get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, __import__("flint.models", fromlist=["Side"]).Side.SHORT, size)
            else:
                # Exit when funding normalizes
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
