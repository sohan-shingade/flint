export const GRID_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side, TimeInForce
from typing import List


class GridTrader(Strategy):
    """Grid Trading — buy low, sell high in ranging markets.

    Places limit orders at grid levels using GTC time-in-force.
    Uses v2 execution with impact checks and proper sizing.
    """
    def __init__(self, grid_pct=2.0, lookback=20, stop_pct=6.0):
        self.grid_pct = grid_pct / 100
        self.lookback = lookback
        self.stop_pct = stop_pct / 100

    @property
    def name(self) -> str:
        return f"Grid({self.grid_pct*100:.1f}%)"

    @classmethod
    def parameters(cls):
        return {
            "grid_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "stop_pct": {"type": "float", "low": 3.0, "high": 10.0, "default": 6.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.lookback:
            return Signal.HOLD

        closes = [c.close for c in history[-self.lookback:]]
        mid = sum(closes) / len(closes)

        if not ctx.positions:
            if candle.close < mid * (1 - self.grid_pct):
                size = (ctx.account.cash * 0.9) / candle.close
                impact = ctx.get_impact_price(candle.market, Side.LONG, size)
                if impact and abs(impact - candle.close) / candle.close > 0.002:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
                    ctx.stop_order(candle.market, Side.SHORT, size,
                                   candle.close * (1 - self.stop_pct))
        else:
            if candle.close > mid * (1 + self.grid_pct):
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)
        return Signal.HOLD

    def reset(self) -> None:
        pass
`
