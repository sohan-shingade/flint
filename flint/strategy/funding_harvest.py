"""Funding rate harvest strategy.

Goes long when funding is deeply negative (shorts are paying longs),
goes short when funding is deeply positive (longs are paying shorts).
Uses real funding rate data via ctx.get_funding_rate() when available,
falls back to price momentum proxy when no funding data exists.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class FundingHarvestStrategy(Strategy):
    """Harvest funding rate payments by taking the opposite side.

    Download funding data in the Data Explorer before backtesting
    for accurate results. Without funding data, uses price momentum
    as a rough proxy.
    """

    def __init__(
        self,
        entry_threshold: float = 0.00003,
        exit_threshold: float = 0.000005,
        stop_loss_pct: float = 0.05,
        lookback: int = 8,
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_pct = stop_loss_pct
        self.lookback = lookback
        self._has_real_data = False
        self._warned = False

    @property
    def name(self) -> str:
        return f"FundingHarvest({self.entry_threshold})"

    def reset(self) -> None:
        self._has_real_data = False
        self._warned = False

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_threshold": {"type": "float", "low": 0.00001, "high": 0.0001, "default": 0.00003},
            "exit_threshold": {"type": "float", "low": 0.000001, "high": 0.00002, "default": 0.000005},
            "stop_loss_pct": {"type": "float", "low": 0.02, "high": 0.10, "default": 0.05},
            "lookback": {"type": "int", "low": 4, "high": 24, "default": 8},
        }

    def _get_avg_funding(self, candle: Candle, history: List[Candle],
                         ctx: Optional["ExecutionContext"]) -> Optional[float]:
        """Get average funding rate from real data or fallback to proxy."""
        if ctx is not None:
            rates = ctx.get_funding_rates(candle.market, lookback=self.lookback)
            if len(rates) >= 3:
                self._has_real_data = True
                return sum(r for _, r in rates[-self.lookback:]) / len(rates[-self.lookback:])

        # Fallback: price momentum proxy (less accurate)
        if ctx is not None and not self._warned:
            ctx.log("WARNING: No funding rate data found — using price momentum proxy. "
                    "Download funding data in Data Explorer for accurate results.")
            self._warned = True
        if len(history) < self.lookback:
            return None
        recent = [c.close for c in history[-self.lookback:]]
        avg_return = (recent[-1] - recent[0]) / recent[0] if recent[0] else 0
        return avg_return / self.lookback

    def on_candle(
        self, candle: Candle, history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        avg_funding = self._get_avg_funding(candle, history, ctx)
        if avg_funding is None:
            return Signal.HOLD

        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if avg_funding < -self.entry_threshold:
                    # Negative funding → go long (longs get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.LONG, size)
                        stop = candle.close * (1 - self.stop_loss_pct)
                        ctx.stop_order(candle.market, Side.SHORT, size, stop)
                elif avg_funding > self.entry_threshold:
                    # Positive funding → go short (shorts get paid)
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.SHORT, size)
                        stop = candle.close * (1 + self.stop_loss_pct)
                        ctx.stop_order(candle.market, Side.LONG, size, stop)
            else:
                # Exit when funding normalizes
                if abs(avg_funding) < self.exit_threshold:
                    ctx.close_position(candle.market)
                    ctx.cancel_all(candle.market)
            return Signal.HOLD

        # v1 fallback (no context)
        if avg_funding < -self.entry_threshold:
            return Signal.BUY
        elif avg_funding > self.entry_threshold:
            return Signal.SELL
        return Signal.HOLD
