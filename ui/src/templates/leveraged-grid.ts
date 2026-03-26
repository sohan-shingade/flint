export const LEVERAGED_GRID_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class LeveragedGrid(Strategy):
    """Leveraged Grid — DCA grid trading with margin-aware sizing.

    Places grid orders at fixed % intervals in the direction of the
    trend. Uses venue leverage for capital efficiency. Monitors margin
    utilization to avoid liquidation.

    FEATURES DEMONSTRATED:
    - ctx.account.leverage — real-time leverage monitoring
    - ctx.account.free_margin — margin-aware position sizing
    - Trend-filtered grid: only buys dips in uptrends, sells rips in downtrends

    Enable 'MARGIN TRACKING' for this strategy to work correctly.
    """
    def __init__(self, grid_pct=2.0, max_leverage=5.0, grid_levels=3, trend_period=100):
        self.grid_pct = grid_pct / 100
        self.max_leverage = max_leverage
        self.grid_levels = grid_levels
        self.trend_period = trend_period
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
            "trend_period": {"type": "int", "low": 50, "high": 200, "default": 100},
        }

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < max(20, self.trend_period):
            return Signal.HOLD

        # Trend filter
        sma = float(np.mean([c.close for c in history[-self.trend_period:]]))
        uptrend = candle.close > sma

        # Initialize grid center
        if self._last_grid_price == 0:
            self._last_grid_price = candle.close
            return Signal.HOLD

        # Check current leverage
        current_lev = ctx.account.leverage
        if current_lev > self.max_leverage:
            return Signal.HOLD

        price_move = (candle.close - self._last_grid_price) / self._last_grid_price

        # In uptrend: buy dips, take profit on rises
        if uptrend:
            if price_move < -self.grid_pct and self._grid_count < self.grid_levels:
                free = ctx.account.free_margin
                if free <= 0:
                    return Signal.HOLD
                size = min(free * 0.3, ctx.account.equity * 0.1) / candle.close
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    stop = candle.close * (1 - self.grid_pct * 4)
                    ctx.stop_order(candle.market, Side.SHORT, size, stop)
                    self._last_grid_price = candle.close
                    self._grid_count += 1
                    ctx.log(f"Grid BUY #{self._grid_count} @ {candle.close:.2f} | lev={current_lev:.1f}x")
            elif price_move > self.grid_pct * 2 and ctx.positions:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
                self._last_grid_price = candle.close
                self._grid_count = 0

        # In downtrend: close longs, don't add
        else:
            if ctx.positions:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
                self._last_grid_price = candle.close
                self._grid_count = 0

        return Signal.HOLD

    def reset(self):
        self._last_grid_price = 0.0
        self._grid_count = 0
`
