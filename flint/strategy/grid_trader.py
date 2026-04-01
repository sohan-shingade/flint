"""Grid trading strategy — places limit orders at fixed intervals.

Demonstrates v2 ExecutionContext usage with limit orders.
Places buy limits below current price and sell limits above.
Collects spread in ranging markets.

Works on any perp market. Best on ranging/sideways markets.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Side, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class GridTraderStrategy(Strategy):
    """Place limit orders in a grid around current price.

    v2 mode (with ctx): Places actual limit orders via ExecutionContext.
    v1 mode (without ctx): Falls back to range-based signal trading.
    """

    def __init__(self, grid_spacing_pct: float = 2.0, grid_levels: int = 3,
                 position_size_pct: float = 0.1):
        self.grid_spacing_pct = grid_spacing_pct / 100
        self.grid_levels = grid_levels
        self.position_size_pct = position_size_pct
        self._grid_placed = False
        self._last_grid_price = 0.0
        self._in_position = False

    @property
    def name(self) -> str:
        return f"Grid({self.grid_spacing_pct * 100:.1f}%, {self.grid_levels}lvl)"

    def reset(self) -> None:
        self._grid_placed = False
        self._last_grid_price = 0.0
        self._in_position = False

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "grid_spacing_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "grid_levels": {"type": "int", "low": 2, "high": 5, "default": 3},
            "candle_resolution_s": {"type": "int", "low": 5, "high": 3600, "default": 60},
        }

    def on_candle(self, candle: Candle, history: List[Candle],
                  ctx: Optional["ExecutionContext"] = None) -> Signal:
        if len(history) < 10:
            return Signal.HOLD

        # --- v1 fallback: simple range trading when no ctx ---
        if ctx is None:
            closes = [c.close for c in history[-20:]] if len(history) >= 20 else [c.close for c in history]
            mid = sum(closes) / len(closes)
            lower = mid * (1 - self.grid_spacing_pct)
            upper = mid * (1 + self.grid_spacing_pct)

            if candle.close <= lower and not self._in_position:
                self._in_position = True
                return Signal.BUY
            elif candle.close >= upper and self._in_position:
                self._in_position = False
                return Signal.SELL
            return Signal.HOLD

        # --- v2 mode: place limit order grid via ctx ---
        price = candle.close

        should_replace = (
            not self._grid_placed
            or (self._last_grid_price > 0
                and abs(price - self._last_grid_price) / self._last_grid_price > self.grid_spacing_pct * 2)
        )

        if should_replace:
            ctx.cancel_all(candle.market)
            per_level = (ctx.account.cash * self.position_size_pct) / price

            if per_level > 0:
                for i in range(1, self.grid_levels + 1):
                    # Buy limits below current price
                    buy_price = price * (1 - self.grid_spacing_pct * i)
                    ctx.limit_order(candle.market, Side.LONG, per_level, buy_price)

                # Only place sell limits if we have a position to close
                pos = ctx.position(candle.market)
                if pos is not None and pos.side == Side.LONG:
                    sell_size = pos.size / self.grid_levels
                    for i in range(1, self.grid_levels + 1):
                        sell_price = price * (1 + self.grid_spacing_pct * i)
                        ctx.limit_order(candle.market, Side.SHORT, sell_size,
                                        sell_price, reduce_only=True)

            self._grid_placed = True
            self._last_grid_price = price

        return Signal.HOLD
