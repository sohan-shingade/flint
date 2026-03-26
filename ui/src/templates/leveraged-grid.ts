export const LEVERAGED_GRID_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class LeveragedGrid(Strategy):
    """Leveraged Grid — DCA grid trading with margin-aware sizing.

    Places grid orders at fixed % intervals. Uses venue leverage
    for capital efficiency. Monitors margin utilization to avoid
    liquidation.

    FEATURES DEMONSTRATED:
    - ctx.account.leverage — real-time leverage monitoring
    - ctx.account.free_margin — margin-aware position sizing
    - ctx.limit_order(venue=) — venue-specific limit orders
    - Margin rejection warnings when overleveraged

    Enable 'MARGIN TRACKING' for this strategy to work correctly.
    Without it, there are no leverage limits.
    """
    def __init__(self, grid_pct=2.0, max_leverage=5.0, grid_levels=3):
        self.grid_pct = grid_pct / 100
        self.max_leverage = max_leverage
        self.grid_levels = grid_levels
        self._last_grid_price = 0.0
        self._grid_count = 0

    @property
    def name(self): return f"LevGrid({self.grid_pct*100:.0f}%, {self.max_leverage}x)"

    @classmethod
    def parameters(cls):
        return {
            "grid_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 2.0},
            "max_leverage": {"type": "float", "low": 2.0, "high": 10.0, "default": 5.0},
            "grid_levels": {"type": "int", "low": 2, "high": 5, "default": 3},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < 20:
            return Signal.HOLD

        # Initialize grid center
        if self._last_grid_price == 0:
            self._last_grid_price = candle.close
            return Signal.HOLD

        # Check current leverage
        current_lev = ctx.account.leverage
        if current_lev > self.max_leverage:
            ctx.log(f"At max leverage ({current_lev:.1f}x), skipping grid")
            return Signal.HOLD

        price_move = (candle.close - self._last_grid_price) / self._last_grid_price

        # Grid buy: price dropped by grid_pct
        if price_move < -self.grid_pct and self._grid_count < self.grid_levels:
            # Size based on remaining margin
            free = ctx.account.free_margin
            if free <= 0:
                ctx.log("No free margin for grid buy")
                return Signal.HOLD
            size = min(free * 0.3, ctx.account.equity * 0.1) / candle.close
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size, venue="drift")
                stop = candle.close * (1 - self.grid_pct * 3)
                ctx.stop_order(candle.market, Side.SHORT, size, stop, venue="drift")
                self._last_grid_price = candle.close
                self._grid_count += 1
                ctx.log(f"Grid BUY #{self._grid_count}: {size:.2f} @ {candle.close:.2f} | lev={current_lev:.1f}x")

        # Grid sell: price rose by grid_pct from last grid
        elif price_move > self.grid_pct and ctx.positions:
            ctx.close_position(candle.market, venue="drift")
            ctx.cancel_all(candle.market)
            self._last_grid_price = candle.close
            self._grid_count = 0
            ctx.log(f"Grid CLOSE: sold @ {candle.close:.2f} | lev={current_lev:.1f}x")

        return Signal.HOLD

    def reset(self):
        self._last_grid_price = 0.0
        self._grid_count = 0
`
