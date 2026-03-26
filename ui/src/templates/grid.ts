export const GRID_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List
import numpy as np


class GridTrader(Strategy):
    """Grid Trading — buy low, sell high in ranging markets.

    Places limit orders at grid levels using GTC time-in-force.
    Uses v2 execution with impact checks and proper sizing.
    Trend filter: only grid in the direction of a 100-bar SMA.
    """
    def __init__(self, grid_pct=2.0, lookback=20, stop_pct=8.0, trend_period=100):
        self.grid_pct = grid_pct / 100
        self.lookback = lookback
        self.stop_pct = stop_pct / 100
        self.trend_period = trend_period

    @property
    def name(self) -> str:
        return f"Grid({self.grid_pct*100:.1f}%)"

    @classmethod
    def parameters(cls):
        return {
            "grid_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "stop_pct": {"type": "float", "low": 3.0, "high": 15.0, "default": 8.0},
            "trend_period": {"type": "int", "low": 50, "high": 200, "default": 100},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        min_bars = max(self.lookback, self.trend_period)
        if ctx is None or len(history) < min_bars:
            return Signal.HOLD

        closes = [c.close for c in history[-self.lookback:]]
        mid = sum(closes) / len(closes)

        # Trend filter: 100-bar SMA
        trend_closes = [c.close for c in history[-self.trend_period:]]
        sma = np.mean(trend_closes)
        uptrend = candle.close > sma

        if not ctx.positions:
            if uptrend and candle.close < mid * (1 - self.grid_pct):
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_pct))
            elif not uptrend and candle.close > mid * (1 + self.grid_pct):
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.SHORT, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.SHORT, size)
                    ctx.stop_order(candle.market, Side.LONG, size,
                                   candle.close * (1 + self.stop_pct))
        else:
            # Exit: price reverts to grid_pct beyond current rolling mean
            if uptrend and candle.close > mid * (1 + self.grid_pct):
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
            elif not uptrend and candle.close < mid * (1 - self.grid_pct):
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
        return Signal.HOLD

    def reset(self) -> None:
        pass
`
