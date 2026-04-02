"""MomentumBreakoutStrategy — price breakout with optional oracle confirmation."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.momentum_breakout")


class MomentumBreakoutStrategy(Strategy):
    """Buy on N-bar high breakout, sell on N-bar low breakdown.

    Optionally confirms direction against the oracle price — if the oracle
    disagrees with the breakout direction, the entry is blocked.
    """

    def __init__(
        self,
        breakout_lookback: int = 20,
        trailing_stop_pct: float = 0.02,
        oracle_confirmation: int = 1,
        candle_resolution_s: int = 3600,
    ) -> None:
        self.breakout_lookback = breakout_lookback
        self.trailing_stop_pct = trailing_stop_pct
        self.oracle_confirmation = oracle_confirmation
        self.candle_resolution_s = candle_resolution_s

    @property
    def name(self) -> str:
        return "momentum_breakout"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "breakout_lookback": {"type": "int", "low": 5, "high": 100, "default": 20},
            "trailing_stop_pct": {"type": "float", "low": 0.005, "high": 0.10, "default": 0.02},
            "oracle_confirmation": {"type": "int", "low": 0, "high": 1, "default": 1},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 86400, "default": 3600},
        }

    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if len(history) < self.breakout_lookback:
            return Signal.HOLD

        # Use the last `breakout_lookback` candles from history (not including current)
        window = history[-self.breakout_lookback :]
        highest_high = max(c.high for c in window)
        lowest_low = min(c.low for c in window)

        if candle.close > highest_high:
            # Potential BUY breakout — check oracle if requested
            if self.oracle_confirmation and ctx is not None:
                oracle_price = ctx.get_oracle_price(candle.market)
                if oracle_price is not None and oracle_price < candle.close:
                    # Oracle price is below current close → oracle disagrees with bullish move
                    logger.debug(
                        "Oracle confirmation failed for BUY: oracle=%.4f close=%.4f",
                        oracle_price,
                        candle.close,
                    )
                    return Signal.HOLD
            return Signal.BUY

        if candle.close < lowest_low:
            # Potential SELL breakdown — check oracle if requested
            if self.oracle_confirmation and ctx is not None:
                oracle_price = ctx.get_oracle_price(candle.market)
                if oracle_price is not None and oracle_price > candle.close:
                    # Oracle price is above current close → oracle disagrees with bearish move
                    logger.debug(
                        "Oracle confirmation failed for SELL: oracle=%.4f close=%.4f",
                        oracle_price,
                        candle.close,
                    )
                    return Signal.HOLD
            return Signal.SELL

        return Signal.HOLD
